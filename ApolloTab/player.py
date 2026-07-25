# -*- coding: utf-8 -*-
"""
============================================================
文件名: player.py
功能描述: GTP播放器高级封装类 - 整合解析/渲染/音频/时间线的完整流程

原理:
  将 GTP 文件的完整生命周期（加载→解析→渲染→音频初始化→播放控制→时间线）
  封装为单一的高层 API 类 GTPPlayer，
  使主程序只需调用简单方法即可实现完整的 GTP 播放功能。

核心职责:
  1. 文件加载与解析 (parse_score 智能调度，支持 GP3-5 和 GP7/GP8)
  2. 音轨渲染 (TabRenderer.render_from_file)
  3. 音频引擎管理 (SynthEngine + MidiConverter)
  4. 播放光标时间线构建 (time_ms ↔ scroll_y 映射)
  5. 时间↔位置双向转换 (二分查找+线性插值)

设计原则:
  - 高内聚低耦合: 所有GTP相关逻辑集中在此类中
  - 最小化依赖: 仅依赖 gtp_engine 内部模块和 PyQt5
  - 线程安全: 音频操作在独立线程中执行
  - 优雅降级: 缺少依赖时提供有意义的错误信息

使用示例:
    from gtp_engine.player import GTPPlayer

    # 创建播放器实例
    player = GTPPlayer()

    # 加载并渲染 (支持 .gp3/.gp4/.gp5/.gpx/.gtp/.gp)
    player.load("song.gp5")
    images = player.render_track(0)  # 渲染第1轨
    
    # 初始化音频（可选）
    if player.init_audio():
        player.play()
    
    # 获取时间线数据（用于播放光标）
    timeline = player.build_timeline(page_layouts, images, display_width)
    
    # 时间↔位置转换
    scroll_y = player.time_to_scroll_pos(5000)  # 5000ms → 像素位置
    time_ms = player.scroll_pos_to_time(scroll_y)  # 像素位置 → ms
    
    # 清理资源
    player.shutdown()

依赖库:
  - gtp_engine.parser (parse_score 智能调度, parse_gtp, GTPParser, GP7Parser)
  - gtp_engine.renderer (TabRenderer, RenderConfig)
  - gtp_engine.audio (MidiConverter, SynthEngine)
  - gtp_engine.models (GTPSong, GTPTrack)
  - PyQt5 (QPixmap, 用于图像渲染)

创建日期: 2026-06-12
最后更新: 2026-07-01 (v1.3.1: 修复播放条与 MIDI 音频不同步的问题,
                   build_timeline() 改为按 beat.duration_value 累加 tick;
                   v1.3.0: ThemeConfig 新增 register_theme() 运行时主题注册接口,
                   节拍器默认 gain 调整为1.5, 新增通道音量 CC 事件)
============================================================
"""

import bisect
from typing import List, Optional, Tuple, Dict, Callable

from PyQt5.QtGui import QPixmap, QPainter, QColor, QFont

# 内部模块导入
from .parser import parse_gtp, parse_score, GTPParser
from .renderer import TabRenderer
from .utils import RenderConfig
from .audio import MidiConverter, MidiEvent, SynthEngine
from .audio.metronome import MetronomeConfig, MetronomeGenerator
from .models import GTPSong, GTPTrack


class GTPPlayer:
    """
    GTP文件播放器 - 高级封装类
    
    功能概述:
      提供 Guitar Pro 文件的完整播放解决方案，包括:
      - 文件解析与元数据提取
      - 六线谱渲染为 QPixmap 图像
      - FluidSynth 音频合成与播放
      - 播放光标时间线构建
      - 时间↔位置双向映射
      
    设计模式:
      - 门面模式(Facade): 将多个子系统的复杂操作封装为简单接口
      - 状态模式(State): 管理加载/就绪/播放/暂停等状态转换
    
    参数说明(初始化):
      gain: 主音量(0.0-1.0), 调整效果: 1.0=最大音量, 0.7=推荐默认值
      sample_rate: 采样率(Hz), 调整效果: 44100=CD音质, 48000=高清
      buffer_size: 缓冲区大小, 调整效果: 256=低延迟, 512=稳定
    """
    
    # ===== 音频模式常量 =====
    MODE_ALL = "all"           # 全轨并轨模式
    MODE_CURRENT = "current"   # 仅当前轨模式
    MODE_OFF = "off"           # 关闭音频模式
    
    def __init__(self, gain: float = 0.7, sample_rate: int = 44100, buffer_size: int = 512):
        """
        初始化 GTP 播放器
        
        参数:
            gain:         主音量增益(0.0-1.0), 调整效果: 0.7=适中音量
            sample_rate:  音频采样率(Hz), 调整效果: 44100=CD标准
            buffer_size:  音频缓冲区大小, 调整效果: 512=平衡延迟与稳定性
        """
        # ===== 核心组件 =====
        self._parser = GTPParser()           # 解析器
        self._renderer = TabRenderer()       # 渲染器
        self._midi_converter = MidiConverter()  # MIDI转换器
        self._synth_engine: Optional[SynthEngine] = None  # 音频合成器(延迟初始化)
        
        # ===== 数据状态 =====
        self._song: Optional[GTPSong] = None      # 当前加载的歌曲对象
        self._file_path: str = ""                  # 当前文件路径
        self._current_track: int = 0               # 当前选中的音轨索引
        
        # ===== 音频状态 =====
        self._audio_mode: str = self.MODE_ALL      # 当前音频模式
        self._audio_enabled: bool = True           # 是否启用音频
        self._audio_events: List[MidiEvent] = []   # 当前MIDI事件列表
        self._track_channels: List[int] = []       # 通道映射(全轨模式)
        
        # ===== 时间线数据 =====
        self._playhead_timeline: List[dict] = []   # 播放光标时间线索引
        self._timeline_times: List[float] = []     # 预提取的时间排序列表(性能优化)
        self._timeline_scroll_ys: List[float] = [] # 预提取的scroll_y排序列表
        self._total_audio_duration_ms: float = 0.0 # 总音频时长(ms)

        # ===== [v0.4.0] 反复记号展开数据 =====
        # 由 rebuild_audio_events() 计算，build_timeline() 使用
        # 存储展开后的原始小节索引序列，确保MIDI事件与时间线完全同步
        self._expanded_measure_indices: List[int] = []  # 展开后的索引序列
        
        # ===== 音频参数 =====
        self._gain = gain
        self._sample_rate = sample_rate
        self._buffer_size = buffer_size

        # ===== [v1.1.3] 节拍器状态 =====
        self._metronome_enabled: bool = False      # 节拍器开关
        self._metronome_volume: float = 0.7        # 节拍器音量 (0.0~1.0)
        self._metronome_gain: float = 2.0          # 节拍器全局增益，解决被乐器掩盖问题
        self._metronome_config: MetronomeConfig = MetronomeConfig(
            enabled=False, volume=0.7, gain=2.0
        )

        # ===== 回调函数 =====
        self._note_callback: Optional[Callable] = None  # 音符触发回调
    
    # ================================================================
    # 属性访问器
    # ================================================================
    
    @property
    def song(self) -> Optional[GTPSong]:
        """当前加载的GTP歌曲对象"""
        return self._song
    
    @property
    def file_path(self) -> str:
        """当前GTP文件路径"""
        return self._file_path
    
    @property
    def current_track(self) -> int:
        """当前选中的音轨索引"""
        return self._current_track
    
    @current_track.setter
    def current_track(self, value: int) -> None:
        """设置当前音轨索引"""
        self._current_track = value
    
    @property
    def audio_mode(self) -> str:
        """当前音频模式 (all/current/off)"""
        return self._audio_mode
    
    @property
    def is_audio_ready(self) -> bool:
        """音频引擎是否已初始化且可用"""
        return self._synth_engine is not None and self._synth_engine.is_initialized

    @property
    def is_loaded(self) -> bool:
        """是否已加载GTP文件(用于导出时判断是否有谱面内容)"""
        return hasattr(self, '_song') and self._song is not None

    @property
    def is_playing(self) -> bool:
        """是否正在播放"""
        return self._synth_engine is not None and self._synth_engine.is_playing
    
    @property
    def is_paused(self) -> bool:
        """是否已暂停"""
        return self._synth_engine is not None and self._synth_engine.is_paused
    
    @property
    def current_time_ms(self) -> float:
        """当前音频播放时间(毫秒)"""
        if self._synth_engine:
            return self._synth_engine.current_time_ms
        return 0.0
    
    @property
    def total_duration_ms(self) -> float:
        """总音频时长(毫秒)"""
        return self._total_audio_duration_ms
    
    @property
    def playhead_timeline(self) -> List[dict]:
        """播放光标时间线数据"""
        return self._playhead_timeline
    
    @property
    def track_count(self) -> int:
        """当前文件的音轨数量"""
        if self._song:
            return len(self._song.tracks)
        return 0
    
    @property
    def tracks(self) -> List[GTPTrack]:
        """获取所有音轨列表"""
        if self._song:
            return self._song.tracks
        return []
    
    # ================================================================
    # 文件加载与解析
    # ================================================================
    
    def load(self, file_path: str) -> GTPSong:
        """
        加载并解析 Guitar Pro 文件

        [v0.4.1] 支持 GP7/GP8 (.gp) 文件，通过 parse_score 智能调度

        参数:
            file_path: .gp3/.gp4/.gp5/.gpx/.gtp/.gp 文件路径

        返回:
            GTPSong 歌曲对象

        异常:
            GPException: 文件格式错误或无法解析时抛出
            ImportError: 缺少 pyguitarpro 依赖时抛出
            FileNotFoundError: 文件不存在时抛出

        示例:
            >>> player = GTPPlayer()
            >>> song = player.load("my_song.gp5")
            >>> print(f"标题: {song.title}, 音轨数: {player.track_count}")
            >>> # GP7/GP8 文件
            >>> song = player.load("new_song.gp")
        """
        self._file_path = file_path
        # [v0.4.1] 使用 parse_score 智能调度器，根据扩展名自动选择解析器
        # .gp3/.gp4/.gp5/.gpx/.gtp → GTPParser (PyGuitarPro)
        # .gp (GP7/GP8)            → GP7Parser (原生 ZIP+GPIF 解析)
        self._song = parse_score(file_path)
        self._current_track = 0
        return self._song
    
    def get_track_info(self, track_index: int = None) -> Dict:
        """
        获取指定音轨的详细信息
        
        参数:
            track_index: 音轨索引，None则使用当前音轨
            
        返回:
            包含音轨信息的字典:
            {
                'index': 音轨索引,
                'name': 音轨名称,
                'tuning': 调弦元组(MIDI音高),
                'tuning_name': 调弦名称,
                'fret_count': 品格数,
                'measure_count': 小节数,
                'instrument': MIDI乐器编号,
                'is_visible': 是否可见,
            }
            
        异常:
            IndexError: 音轨索引超出范围时抛出
        """
        if not self._song:
            raise ValueError("尚未加载任何文件，请先调用 load()")
        
        idx = track_index if track_index is not None else self._current_track
        if idx >= len(self._song.tracks):
            raise IndexError(f"音轨索引{idx}超出范围(0-{len(self._song.tracks)-1})")
        
        track = self._song.tracks[idx]
        
        return {
            'index': idx,
            'name': track.name or f"音轨{idx + 1}",
            'tuning': track.strings,
            'tuning_name': track.get_tuning_name(),
            'fret_count': track.fret_count,
            'measure_count': len(track.measures),
            'instrument': track.instrument,
            'is_visible': track.is_visible,
        }
    
    def get_all_tracks_info(self) -> List[Dict]:
        """
        获取所有音轨的信息列表
        
        返回:
            字典列表，每个元素包含一个音轨的详细信息
            (格式同 get_track_info 的返回值)
        """
        if not self._song:
            return []
        
        return [self.get_track_info(i) for i in range(len(self._song.tracks))]
    
    # ================================================================
    # 渲染功能
    # ================================================================
    
    def render_track(self, track_index: int = None, 
                     config: RenderConfig = None) -> List[QPixmap]:
        """
        渲染指定音轨的六线谱图像
        
        参数:
            track_index: 要渲染的音轨索引，None则使用当前音轨
            config:      自定义渲染配置，None则使用默认配置
            
        返回:
            QPixmap列表，每元素对应一页乐谱图像
            
        注意:
            同时会更新 last_layouts 属性，可用于播放光标等功能。
            如果尚未加载文件，会自动调用 load()。
            
        示例:
            >>> pages = player.render_track(0)
            >>> print(f"共{len(pages)}页")
            >>> pages[0].save("page1.png", "PNG")
        """
        if not self._file_path:
            raise ValueError("尚未加载文件，请先调用 load()")
        
        idx = track_index if track_index is not None else self._current_track
        
        if config:
            self._renderer = TabRenderer(config)
        
        pixmaps = self._renderer.render_from_file(self._file_path, track_index=idx)
        
        # 更新当前音轨索引
        self._current_track = idx
        
        return pixmaps
    
    def render_from_song(self, song: GTPSong = None, 
                         track_index: int = None,
                         config: RenderConfig = None) -> List[QPixmap]:
        """
        从已有的 GTPSong 对象渲染（不重新解析文件）
        
        参数:
            song:        GTPSong 对象，None则使用已加载的song
            track_index:  音轨索引
            config:      渲染配置
            
        返回:
            QPixmap列表
        """
        target_song = song or self._song
        if not target_song:
            raise ValueError("没有可用的歌曲数据")
        
        idx = track_index if track_index is not None else self._current_track
        
        if config:
            renderer = TabRenderer(config)
            return renderer.render(target_song, track_index=idx)
        else:
            return self._renderer.render(target_song, track_index=idx)
    
    @property
    def last_layouts(self) -> list:
        """
        获取上次渲染的布局数据
        
        返回:
            List[PageLayout], 由 TabRenderer.render() 生成，
            包含每页/行/小节/拍的精确坐标信息
        """
        return getattr(self._renderer, 'last_layouts', [])
    
    # ================================================================
    # 主题管理
    # ================================================================
    
    def set_theme(self, theme) -> None:
        """
        切换渲染主题（委托给内部渲染器）
        
        功能:
          动态切换六线谱的配色方案，无需重新创建播放器实例。
          切换后需要重新调用 render_track() 才能生成使用新主题的图像。
        
        参数:
            theme: 可以是以下两种形式之一:
              1. 字符串: 预设主题名称 ("light" | "dark")
              2. ThemeConfig 实例: 自定义或预定义的主题对象
              
        使用示例:
            # 方式1: 通过名称字符串切换（推荐）
            player.set_theme("light")   # 黑白主题（适合打印/白天）
            player.set_theme("dark")    # 深色主题（适合夜间/护眼）
            
            # 方式2: 通过 ThemeConfig 实例切换
            from ApolloTab.utils.constants import ThemeConfig
            my_theme = ThemeConfig.get_theme("light")
            player.set_theme(my_theme)
            
            # 方式3: 自定义主题
            custom = ThemeConfig(
                colors={
                    "COLOR_BG": "#FFFDE7",      # 米黄色背景
                    "COLOR_TEXT": "#212121",     # 近黑色文字
                },
                theme_name="sepia"
            )
            player.set_theme(custom)
        
        注意:
          - 此方法仅修改配置，不会自动重新渲染已有图像
          - 调用后需重新执行 render_track() 才能看到效果
          - 内部会同步更新布局引擎的主题（确保一致性）
          
        异常:
          ValueError: 当传入未知的主题名称时抛出（由 TabRenderer.set_theme 抛出）
          TypeError: 当 theme 参数类型不支持时抛出（由 TabRenderer.set_theme 抛出）
          
        性能:
          切换主题是 O(1) 操作（仅替换引用），
          不会触发任何计算或 I/O 操作。
          
        设计模式:
          委托模式(Delegate): 将主题设置请求转发给内部的 TabRenderer 实例处理，
          符合门面模式(Facade)的设计原则，对外提供统一简化的接口。
        """
        # 委托给内部渲染器的 set_theme 方法
        self._renderer.set_theme(theme)
    
    @property
    def current_theme_name(self) -> str:
        """
        获取当前主题名称
        
        返回:
            当前使用的主题标识字符串，如 "light", "dark", "custom" 等
            
        使用示例:
            >>> player = GTPPlayer()
            >>> print(f"当前主题: {player.current_theme_name}")
            当前主题: dark
        """
        return self._renderer.current_theme_name
    
    def get_available_themes(self) -> List[str]:
        """
        获取所有可用的预设主题名称列表
        
        返回:
            主题名称列表，如 ["light", "dark"]
            
        使用示例:
            >>> themes = player.get_available_themes()
            >>> print(f"可用主题: {themes}")
            可用主题: ['light', 'dark']
            
            # 遍历所有主题
            for theme_name in player.get_available_themes():
                print(theme_name)
        """
        return self._renderer.get_available_themes()
    
    # ================================================================
    # 音频引擎管理
    # ================================================================
    
    def init_audio(self, note_callback: Callable = None) -> bool:
        """
        初始化音频播放引擎
        
        功能:
          1. 创建 SynthEngine 实例（FluidSynth 合成器）
          2. 初始化音频输出驱动
          3. 自动搜索并加载 SoundFont 音色文件
          4. 设置音符回调（用于视觉高亮同步）
          5. 根据 audio_mode 转换并加载 MIDI 事件
          
        参数:
            note_callback: 音符触发回调函数，签名为 (midi_pitch, velocity, time_ms) → None
                          用于在UI中高亮当前发声的音符
                          
        返回:
            True 表示初始化成功，False 表示失败（缺少依赖或SoundFont）
            
        注意:
          - 正常应在 load() 之后调用。
          - [v1.1.3] 无 song 时也可调用，用于图片/PDF 模式下的纯节拍器播放。
          - 失败时不会抛出异常，而是返回 False 并打印警告信息。
          
        错误处理:
          - ImportError(pyfluidsynth未安装): 返回False，提示安装
          - SoundFont未找到: 返回False，提示放置sf2文件
          - 初始化失败: 返回False，打印详细错误
        """
        try:
            # === Step 1: 创建 FluidSynth 合成器 ===
            self._synth_engine = SynthEngine(
                gain=self._gain,
                sample_rate=self._sample_rate,
                buffer_size=self._buffer_size
            )

            # === Step 2: 初始化音频驱动 ===
            if not self._synth_engine.initialize():
                print("[GTPPlayer] 错误: FluidSynth 初始化失败")
                print("  可能原因: fluidsynth 库未正确安装或音频设备不可用")
                print("  解决方案:")
                print("    Windows: 下载 libfluidsynth-3.dll 放到项目目录")
                print("    Linux:   sudo apt-get install fluidsynth")
                return False

            # === Step 3: 加载 SoundFont ===
            sf_path = self._synth_engine.load_soundfont()
            if not sf_path:
                print("[GTPPlayer] 警告: 未找到 SoundFont 文件")
                print("  将使用默认音色（可能效果不佳）")
                print("  建议: 下载 FluidR3_GM.sf2 放到 ./soundfont/ 目录")
            else:
                print(f"[GTPPlayer] ✓ 已加载 SoundFont: {sf_path}")

            # === Step 4: 设置音符回调 ===
            self._note_callback = note_callback
            if note_callback:
                self._synth_engine.set_note_callback(note_callback)

            # === Step 5: 转换并加载 MIDI 事件 ===
            # [v1.1.3] 无 song 时(图片/PDF模式仅使用节拍器)跳过事件重建
            if self._song:
                self.rebuild_audio_events()

                # 打印就绪信息
                mode_label = "全轨并轨" if self._audio_mode == self.MODE_ALL else f"仅当前轨"
                duration_ms = self.get_current_duration_ms()
                print(f"[GTPPlayer] ✓ 引擎就绪[{mode_label}]: "
                      f"{len(self._audio_events)}个MIDI事件, "
                      f"{len(self._track_channels)}个通道, "
                      f"BPM={self._song.tempo}, "
                      f"时长={duration_ms / 1000:.1f}秒")
            else:
                print("[GTPPlayer] ✓ 引擎就绪(无GTP文件，仅节拍器模式)")

            return True
            
        except ImportError as e:
            print(f"[GTPPlayer] 错误: 依赖库缺失 - {e}")
            print("  请安装: pip install pyfluidsynth")
            return False
        except Exception as e:
            print(f"[GTPPlayer] 错误: 音频初始化失败 - {e}")
            return False
    
    def get_current_duration_ms(self) -> float:
        """
        获取当前模式的音频总时长(ms)
        
        返回:
          全轨模式: 所有轨道的最长时长
          单轨模式: 当前轨道的时长
        """
        if not self._song or not self._midi_converter:
            return 0.0
        
        if self._audio_mode == self.MODE_ALL:
            return self._midi_converter.get_all_tracks_duration_ms(self._song)
        else:
            return self._midi_converter.get_total_duration_ms(
                self._song, self._current_track
            )
    
    def set_audio_mode(self, mode: str) -> None:
        """
        切换音频播放模式
        
        参数:
            mode: 目标模式
              - "all"(MODE_ALL):     全轨并轨 - 所有音轨同时播放(默认)
              - "current"(MODE_CURRENT): 仅当前轨 - 只播放当前选中音轨
              - "off"(MODE_OFF):     关闭音频 - 仅滚动播放，不输出声音
              
        原理:
          切换模式时会自动停止当前播放、重建MIDI事件序列、重新加载到合成器。
          如果引擎未初始化，仅更新模式标记（待后续 init_audio() 时生效）。
        """
        if self._audio_mode == mode:
            return  # 模式未变，跳过
        
        old_mode = self._audio_mode
        self._audio_mode = mode
        
        # 更新启用状态
        self._audio_enabled = (mode != self.MODE_OFF)
        
        # 如果是关闭模式，立即停止音频
        if mode == self.MODE_OFF and self._synth_engine:
            self._synth_engine.stop()
        
        # 如果引擎已初始化，根据新模式重建事件
        if self._synth_engine and mode != self.MODE_OFF:
            self.rebuild_audio_events()
        
        print(f"[GTPPlayer] 音频模式: {old_mode} → {mode}")
    
    def rebuild_audio_events(self) -> None:
        """
        重新构建 MIDI 事件序列（切换音轨/切换音频模式时调用）

        功能:
          根据 audio_mode 决定转换范围:
          - 全轨模式: 转换所有音轨，每轨独立 MIDI 通道
          - 单轨模式: 仅转换当前选中音轨
          - [v0.4.0] 自动展开反复记号，确保播放顺序正确

        流程:
          1. 停止当前播放（如有）
          2. 根据模式选择转换方式
          3. [v0.4.0] 计算并缓存反复展开序列（供 build_timeline 同步使用）
          4. 为各通道设置乐器音色（吉他/鼓组）
          5. 重新加载事件到合成器
          6. [解耦] 节拍器事件单独通过 SynthEngine.load_metronome_events() 加载
             不再混入主事件流。切换节拍器开关/音量时无需重建主事件。
        """
        if not self._song or not self._midi_converter or not self._synth_engine:
            return

        # 先停止正在播放的音频
        self._synth_engine.stop()

        # === [v0.4.0] 计算并缓存反复记号展开序列 ===
        # 使用当前音轨的小节列表计算展开索引
        # build_timeline() 会使用相同的序列来同步时间线
        current_track = self._song.tracks[self._current_track]
        self._expanded_measure_indices = self._midi_converter.expand_measure_indices(
            current_track.measures
        )

        # 打印展开信息(调试用)
        if len(self._expanded_measure_indices) != len(current_track.measures):
            print(f"[GTPPlayer] 反复记号展开: {len(current_track.measures)}小节 → "
                  f"{len(self._expanded_measure_indices)}个播放位置")

        # === 根据模式选择转换方式 ===
        # [解耦] MidiConverter 不再接受 metronome_config，主事件流中不再混入节拍器
        if self._audio_mode == self.MODE_ALL:
            # 全轨并轨: 转换所有音轨
            self._audio_events, self._track_channels = (
                self._midi_converter.convert_all_tracks(self._song)
            )

            # [v1.1.1] 不再强制覆盖通道音色。
            # MidiConverter.convert_all_tracks() 已在每个音轨开头生成
            # Bank Select + Program Change 事件，音色由 GP 文件本身决定。
            # 此处强制 set_instrument(ch, 27) 会覆盖 GP7/GP8 解析出的具体音色，
            # 并在 seek 后导致音色状态丢失、声音发闷。
        else:
            # 仅当前轨: 只转换当前选中的音轨
            self._track_channels = []
            self._audio_events = self._midi_converter.convert(
                self._song, track_index=self._current_track
            )

        # 重新加载到合成器
        if self._audio_events:
            self._synth_engine.load_events(
                self._audio_events,
                bpm=self._song.tempo,
                ticks_per_beat=480  # MIDI标准分辨率
            )

            # 打印日志
            mode_label = ("全轨并轨" if self._audio_mode == self.MODE_ALL
                         else f"仅当前轨(#{self._current_track + 1})")
            print(f"[GTPPlayer] 事件重建[{mode_label}]: "
                  f"{len(self._audio_events)}个事件, "
                  f"{len(self._track_channels)}个通道")

        # === [解耦] 独立加载节拍器事件 ===
        # 主事件流已加载完成，节拍器事件单独生成并通过独立线程播放。
        # 这里调用 _sync_metronome() 即可；它会判断是否启用，并按需启动节拍器线程。
        self._sync_metronome()
    
    # ================================================================
    # [v1.1.3] 节拍器控制
    # ================================================================

    def set_metronome(self, enabled: bool, volume: float,
                      gain: float = None) -> None:
        """
        设置节拍器开关、音量与全局增益

        参数:
            enabled: 是否启用节拍器
            volume:  音量 (0.0 ~ 1.0)，调整效果: 0=静音, 1=最大音量
            gain:    全局增益 (>=0)，调整效果: 1.0=原音量, 2.0=翻倍,
                     用于提升木鱼音色在伴奏中的突出程度；None=保持当前值

        说明:
            [解耦] 节拍器事件已与主事件流分离。修改配置后：
              - 若已加载 GTP 歌曲且音频引擎可用：调用 _sync_metronome()
                单独重启节拍器线程，不再重建主 MIDI 事件流。
              - 图片/PDF 模式下不自动启动，由主程序调用 play_metronome_only()
        """
        self._metronome_enabled = enabled
        self._metronome_volume = max(0.0, min(1.0, volume))
        if gain is not None:
            self._metronome_gain = max(0.0, gain)

        self._metronome_config.enabled = self._metronome_enabled
        self._metronome_config.volume = self._metronome_volume
        self._metronome_config.gain = self._metronome_gain

        # GTP 模式下同步节拍器状态（不重建主事件流）
        if self._song and self._synth_engine and self._audio_mode != self.MODE_OFF:
            self._sync_metronome()

    def _sync_metronome(self) -> None:
        """
        [解耦] 同步当前节拍器配置到 SynthEngine

        调用场景:
          - rebuild_audio_events() 末尾（首次加载节拍器）
          - set_metronome() 修改配置后
          - play() / set_audio_mode 切换后

        行为:
          - 若 _metronome_enabled 为 False: 卸载节拍器事件
          - 若 True: 根据当前播放位置/歌曲信息生成节拍器事件并独立加载

        [对齐] 播放中开启节拍器时不卡顿:
          - 用 bisect 计算 start_index（跳过已过去的事件索引）
          - 把 start_offset_ms + start_index 一起传给 SynthEngine
          - 配合 SynthEngine.load_metronome_events() 内部对 start_perf 的偏移修正，
            首事件会在毫秒级延迟内立刻发声，不会卡 30 秒
        """
        if not self._synth_engine:
            return

        if not self._metronome_enabled:
            self._synth_engine.unload_metronome_events()
            return

        # GTP 模式: 根据当前音轨的展开序列生成节拍器事件
        if self._song and self._song.tracks:
            import bisect
            ticks_per_beat = self._midi_converter.TICKS_PER_BEAT
            track_idx = min(self._current_track, len(self._song.tracks) - 1)
            # 复用 _expanded_measure_indices (在 rebuild_audio_events 中已缓存)
            expanded = getattr(self, '_expanded_measure_indices', None)
            if expanded is None:
                track = self._song.tracks[track_idx]
                expanded = self._midi_converter.expand_measure_indices(track.measures)
            events = MetronomeGenerator.generate_for_song(
                song=self._song,
                track_index=track_idx,
                config=self._metronome_config,
                expanded_indices=expanded,
                ticks_per_beat=ticks_per_beat
            )
            # 计算总 tick 数(按展开序列逐小节求和)
            total_ticks = 0
            for orig_idx in expanded:
                m = self._song.tracks[track_idx].measures[orig_idx]
                num, den = getattr(m, 'time_signature', (4, 4))
                if den <= 0:
                    den = 4
                total_ticks += int(num * ticks_per_beat * 4.0 / den)

            # [对齐] 播放中开启: 用 bisect 找到第一个 time > start_offset_ticks 的事件
            # 跳过这些已过去的事件，避免节拍器线程遍历大量旧事件导致卡顿
            start_offset_ms = 0.0
            start_index = 0
            if self._synth_engine.is_playing:
                cur_ms = self._synth_engine.current_time_ms
                bpm = self._song.tempo or 120
                if bpm <= 0:
                    bpm = 120
                # 将"主播放位置(毫秒)"换算成"tick 偏移"
                start_offset_ticks = (
                    cur_ms / 1000.0 * bpm / 60.0 * ticks_per_beat
                )
                # 还原为毫秒(传给 SynthEngine)
                start_offset_ms = (
                    start_offset_ticks * 60000.0 / (bpm * ticks_per_beat)
                )
                # bisect_right 找第一个 time > start_offset_ticks 的事件
                # 这样既能跳过该 tick 之前的 note_on，也能跳过对应 note_off
                # （note_off 在 note_on 之后几个 tick，但仍在同一 click 内）
                if events:
                    times = [e.time for e in events]
                    start_index = bisect.bisect_right(times, start_offset_ticks)

            self._synth_engine.load_metronome_events(
                events=events,
                bpm=self._song.tempo or 120,
                ticks_per_beat=ticks_per_beat,
                total_ticks=total_ticks,
                start_offset_ms=start_offset_ms,
                start_index=start_index
            )
        else:
            # 非 GTP 模式: 保持现有行为，由 play_metronome_only() 启动
            self._synth_engine.unload_metronome_events()

    def play_metronome_only(self, bpm: int = 120, numerator: int = 4,
                            denominator: int = 4,
                            duration_minutes: int = 10) -> None:
        """
        仅播放节拍器（用于图片/PDF 等非 GTP 模式）

        参数:
            bpm:             每分钟拍数
            numerator:       拍号分子
            denominator:     拍号分母
            duration_minutes: 生成事件的总时长（分钟），默认 10 分钟

        说明:
            [解耦] 此方法直接调用 SynthEngine.load_metronome_events() 启动
                  独立节拍器线程；不再占用主播放线程的事件队列。
            停止播放由 stop() 统一处理。
        """
        if not self._synth_engine:
            return

        if not self._metronome_enabled:
            return

        # 先生成足够长的节拍器事件
        ticks_per_beat = self._midi_converter.TICKS_PER_BEAT
        total_beats = int(bpm * duration_minutes)
        # 按拍号计算总 tick 数（向上取整到整小节）
        ticks_per_measure = int(numerator * ticks_per_beat * 4.0 / denominator)
        measure_count = (total_beats // numerator) + 1
        total_ticks = measure_count * ticks_per_measure

        events = MetronomeGenerator.generate_simple(
            bpm=bpm,
            numerator=numerator,
            denominator=denominator,
            total_ticks=total_ticks,
            ticks_per_beat=ticks_per_beat,
            config=self._metronome_config
        )

        # [解耦] 独立线程播放节拍器（不通过主 _play_loop 调度）
        self._synth_engine.load_metronome_events(
            events=events,
            bpm=bpm,
            ticks_per_beat=ticks_per_beat,
            total_ticks=total_ticks,
            start_offset_ms=0.0,
            start_index=0
        )

    # ================================================================
    # 播放控制
    # ================================================================

    def play(self) -> None:
        """
        开始播放音频
        
        注意: 需要先调用 init_audio() 初始化引擎
        """
        if self._synth_engine and self._audio_enabled:
            self._synth_engine.play()
    
    def pause(self) -> None:
        """
        暂停播放（保持当前位置，可恢复）
        """
        if self._synth_engine:
            self._synth_engine.pause()
    
    def resume(self) -> None:
        """
        恢复播放（从暂停位置继续）
        """
        if self._synth_engine:
            self._synth_engine.resume()
    
    def stop(self) -> None:
        """
        停止播放（回到开头）
        """
        if self._synth_engine:
            self._synth_engine.stop()
    
    def seek(self, time_ms: float) -> None:
        """
        跳转到指定时间位置
        
        参数:
            time_ms: 目标时间(毫秒)，0=开头
        """
        if self._synth_engine:
            self._synth_engine.seek(time_ms)
    
    # ================================================================
    # 音量控制（实时调整）
    # ================================================================
    
    def set_master_volume(self, db_value: float) -> None:
        """
        设置Master总音量(影响所有通道的最终输出)
        
        原理:
          将dB值转换为线性增益系数，通过修改FluidSynth合成器的gain属性实现。
          FluidSynth的gain参数控制最终输出音量，范围0.0-10.0。
        
        参数:
            db_value: dB值, 范围-60.0~+12.0
              - 0.0dB = 原始音量(单位增益)
              - +6.0dB = 约2倍音量
              - -60.0dB = 静音(接近0)
              - 调整效果: 每变化6dB约等于音量翻倍/减半
        
        调用时机:
          - 用户拖动Master音量滑块时
          - 双击Master滑块重置为0dB时
        """
        import math
        
        # 将dB转换为线性增益 (20 * log10(gain) = db)
        # 范围映射: -60dB → 0.001, 0dB → 1.0, +12dB → ~4.0
        if db_value <= -60.0:
            linear_gain = 0.001  # 接近静音但不完全为0
        else:
            linear_gain = 10 ** (db_value / 20.0)
        
        # 限制在FluidSynth支持的范围内(0.0-10.0)
        linear_gain = max(0.0, min(10.0, linear_gain))
        
        # 更新内部gain值和synth_engine的gain
        self._gain = linear_gain
        if self._synth_engine and self._synth_engine.is_initialized:
            try:
                # [封装] 通过公共 API 设置 gain 与主音量
                # 不再直接访问 self._synth_engine._synth
                self._synth_engine.gain = linear_gain
                # FluidSynth 的 gain 在初始化后不可热修改，
                # 用公共 set_master_volume() 对全部 16 通道发送 CC#7 模拟
                master_vol = int(linear_gain * 127)
                self._synth_engine.set_master_volume(master_vol)
            except Exception as e:
                print(f"[GTPPlayer] 设置主音量失败: {e}")
    
    def set_track_volume(self, track_index: int, db_value: float) -> None:
        """
        设置单个音轨的音量(MIDI CC#7 Volume)
        
        原理:
          使用MIDI Control Change消息7(Volume)来控制对应MIDI通道的音量。
          MIDI音量范围0-127, 其中127=最大, 0=静音, 100=默认。
          将dB值转换为MIDI音量值(0-127)后发送CC消息给对应通道。
        
        参数:
            track_index: 音轨索引(0-based), 对应self.tracks[track_index]
            db_value:    dB值, 范围-60.0~+12.0
              - 0.0dB → MIDI音量100(默认)
              - +12dB → MIDI音量127(最大)
              - -60dB → MIDI音量0(静音)
              - 调整效果: 实时生效，无需重启播放
        
        注意事项:
          - 仅在全轨模式(MODE_ALL)下有效(每个音轨有独立MIDI通道)
          - 单轨模式下只有当前轨一个通道，效果等同于Master音量
          - 如果音频引擎未初始化，仅保存设置值(待后续init_audio时应用)
        """
        # 将dB值转换为MIDI音量值(0-127)
        # 映射规则: -60dB→0, 0dB→100(默认), +12dB→127
        if db_value <= -60.0:
            midi_volume = 0
        elif db_value >= 12.0:
            midi_volume = 127
        else:
            # 线性插值: -60~+12dB 映射到 0~127
            # 0dB时应该是100(标准默认音量)
            normalized = (db_value + 60.0) / 72.0  # 归一化到0-1
            midi_volume = int(normalized * 127)
            midi_volume = max(0, min(127, midi_volume))
        
        # 存储设置(用于后续查询或重新初始化时恢复)
        if not hasattr(self, '_track_volumes'):
            self._track_volumes: Dict[int, int] = {}
        self._track_volumes[track_index] = midi_volume
        
        # 尝试实时应用到音频引擎
        if self._synth_engine and self._synth_engine.is_initialized:
            try:
                # [封装] 通过公共 set_channel_volume 发送 CC#7
                # 不再直接访问 self._synth_engine._synth
                if self._audio_mode == self.MODE_ALL and self._track_channels:
                    # 全轨模式: 每个音轨有独立的MIDI通道
                    if track_index < len(self._track_channels):
                        channel = self._track_channels[track_index]
                        if self._synth_engine.set_channel_volume(channel, midi_volume):
                            print(f"[GTPPlayer] Track {track_index} (CH{channel}): {midi_volume}/127")
                elif self._audio_mode == self.MODE_CURRENT:
                    # 单轨模式: 只有一个活动通道(通常是channel 0)
                    # 所有音轨滑块都控制同一个通道
                    if self._synth_engine.set_channel_volume(0, midi_volume):
                        print(f"[GTPPlayer] Track {track_index} (CH0): {midi_volume}/127")
            except Exception as e:
                print(f"[GTPPlayer] 设置音轨{track_index}音量失败: {e}")
    
    # ================================================================
    # A/B区域循环（基于小节原子单位）
    # ================================================================
    
    def set_loop_region(self, start_ms: float, end_ms: float) -> None:
        """
        设置A/B区域循环范围(毫秒时间)
        
        [v0.3.8] 将循环逻辑下沉到SynthEngine音频线程内部。
        设置后音频引擎在播放到end_ms时自动回到start_ms继续循环，
        UI层无需任何冷却/帧计数器/模拟时钟等复杂机制。
        
        参数:
            start_ms: 循环起始时间(毫秒)
            end_ms:   循环结束时间(毫秒)
        """
        if self._synth_engine:
            self._synth_engine.set_loop_region(start_ms, end_ms)
    
    def set_loop_region_by_measure(self, start_measure_idx: int, 
                                     end_measure_idx: int) -> bool:
        """
        基于小节索引设置A/B区域循环
        
        原理: 利用 build_timeline() 生成的 playhead_timeline 数据，
              找到起始小节的第一个拍的时间作为loop_start_ms，
              找到结束小节的最后一个拍的时间作为loop_end_ms。
              这样每个小节就是一个"模块"，循环以完整的小节为单位进行。
        
        参数:
            start_measure_idx: 起始小节索引(0-based)，对应A点所在小节
            end_measure_idx:   结束小节索引(0-based)，对应B点所在小节
            
        返回:
            True=设置成功, False=失败(无时间线数据或索引超出范围)
            
        示例:
            >>> player.set_loop_region_by_measure(0, 3)  # 循环第1-4小节
            >>> player.set_loop_region_by_measure(2, 2)  # 只循环第3小节
        """
        if not self._playhead_timeline or not self._synth_engine:
            return False
        
        # [v2.0.6修复] 使用全局唯一小节ID(global_meas_idx)作为字典key
        # 旧代码用meas_idx做key，但meas_idx在每个系统(System/行)内从0重新计数，
        # 导致不同系统的同名小节(如都是小节0)被合并到同一个key下 → 循环设置错误
        # 新方案: global_meas_idx在build_timeline中递增，跨系统/页全局唯一
        measure_entries = {}  # global_meas_idx -> [entries]
        for entry in self._playhead_timeline:
            g_idx = entry.get('global_meas_idx', -1)
            if g_idx >= 0:
                if g_idx not in measure_entries:
                    measure_entries[g_idx] = []
                measure_entries[g_idx].append(entry)
        
        if not measure_entries:
            return False
        
        all_meas_indices = sorted(measure_entries.keys())
        
        # 边界检查
        if start_measure_idx < 0:
            start_measure_idx = all_meas_indices[0]
        if end_measure_idx >= len(all_meas_indices):
            end_measure_idx = all_meas_indices[-1]
        
        # 获取起始小节第一个拍的时间
        start_entries = measure_entries.get(start_measure_idx, [])
        if not start_entries:
            return False
        loop_start_ms = start_entries[0]['time_ms']
        
        # 获取结束小节最后一个拍的时间
        end_entries = measure_entries.get(end_measure_idx, [])
        if not end_entries:
            return False
        loop_end_ms = end_entries[-1]['time_ms']
        
        # 确保end > start(至少留1ms余量)
        if loop_end_ms <= loop_start_ms:
            loop_end_ms = loop_start_ms + 100  # 最少100ms的循环区间
        
        self._synth_engine.set_loop_region(loop_start_ms, loop_end_ms)
        
        print(f"[GTPPlayer] A/B循环设置: 小节[{start_measure_idx}-{end_measure_idx}] "
              f"= 时间[{loop_start_ms:.0f}ms - {loop_end_ms:.0f}ms] "
              f"(跨度{loop_end_ms - loop_start_ms:.0f}ms)")
        return True
    
    def set_loop_region_by_position(self, start_pct: float, 
                                      end_pct: float,
                                      total_scroll_distance: float = 0,
                                      display_height: int = 0) -> bool:
        """
        基于滚动位置百分比设置A/B区域循环(UI层调用入口)
        
        原理: 将UI层的百分比位置转换为小节索引，再基于小节设置循环。
              这实现了用户要求的"每个小节作为一个模块"的设计：
              - A点落在哪个小节，就从该小节开头开始播
              - B点落在哪个小节，就在该小节末尾结束
        
        参数:
            start_pct: 起始位置百分比(0-100)，对应进度条上的A点
            end_pct:   结束位置百分比(0-100)，对应进度条上的B点
            total_scroll_distance: 总滚动距离(像素)，用于pct→scroll_y→measure转换
            display_height: 显示区域高度(像素)，用于居中修正
            
        返回:
            True=设置成功, False=失败
        """
        if not self._playhead_timeline or not self._synth_engine:
            return False
        
        # 方法1: 通过时间线索引找到对应的小节
        total_dur = self._total_audio_duration_ms
        if total_dur <= 0:
            return False
        
        # 将百分比转换为目标时间(ms)
        target_start_ms = total_dur * start_pct / 100.0
        target_end_ms = total_dur * end_pct / 100.0
        
        # [调试] 打印查找参数(正式版可移除或改为DEBUG级别日志)
        print(f"[GTPPlayer] 循环查找: pct[{start_pct:.1f}-{end_pct:.1f}] "
              f"→ time[{target_start_ms:.0f}ms-{target_end_ms:.0f}ms] "
              f"总时长={total_dur:.0f}ms, 时间线条目数={len(self._timeline_times)}")
        
        # 在时间线中查找对应的小节索引
        start_meas_idx = self._find_measure_at_time(target_start_ms)
        end_meas_idx = self._find_measure_at_time(target_end_ms)
        
        if start_meas_idx is None or end_meas_idx is None:
            return False
        
        # 基于小节设置循环（自动对齐到小节边界）
        return self.set_loop_region_by_measure(start_meas_idx, end_meas_idx)
    
    def _find_measure_at_time(self, time_ms: float) -> Optional[int]:
        """
        根据时间戳查找所在的小节索引(内部方法)
        
        在 playhead_timeline 中二分查找指定时间对应的小节编号。
        
        参数:
            time_ms: 音频时间(毫秒)
            
        返回:
            小节索引(int)，找不到返回None
        """
        if not self._timeline_times or not self._playhead_timeline:
            print(f"[GTPPlayer] _find_measure_at_time: 时间线为空! "
                  f"_timeline_times长度={len(self._timeline_times) if self._timeline_times else 0}, "
                  f"_playhead_timeline长度={len(self._playhead_timeline) if self._playhead_timeline else 0}")
            return None
        
        # 二分查找: 找到第一个 > time_ms 的位置，退一位就是 <= time_ms 的最大元素
        idx = bisect.bisect_right(self._timeline_times, time_ms) - 1
        
        # 边界修正: idx < 0 说明 time_ms 比所有时间线点都早 → 返回第1个条目的小节
        if idx < 0:
            idx = 0
        # 边界修正: idx >= len 说明 time_ms 超过所有时间线点 → 返回最后1个有效条目的小节
        elif idx >= len(self._playhead_timeline):
            idx = len(self._playhead_timeline) - 1
        
        # [v2.0.6修复] 返回全局唯一小节ID(而非系统内局部meas_idx)
        global_meas_idx = self._playhead_timeline[idx].get('global_meas_idx')
        local_meas_idx = self._playhead_timeline[idx].get('meas_idx')  # 保留用于调试显示
        found_time = self._playhead_timeline[idx].get('time_ms', 0)
        
        # [调试] 打印查找结果
        print(f"[GTPPlayer] _find_measure_at_time({time_ms:.0f}ms): "
              f"idx={idx}/{len(self._playhead_timeline)-1}, "
              f"global_meas={global_meas_idx}(local={local_meas_idx}), "
              f"该条目time_ms={found_time:.0f}")
        
        return global_meas_idx
    
    def clear_loop_region(self) -> None:
        """清除A/B区域循环设置"""
        if self._synth_engine:
            self._synth_engine.clear_loop_region()
    
    @property
    def is_loop_enabled(self) -> bool:
        """
        是否已启用A/B区域循环

        [封装] 通过 SynthEngine.is_loop_enabled 公共属性查询，
        不再直接访问 self._synth_engine._lock / self._synth_engine._loop_enabled
        """
        if self._synth_engine:
            return self._synth_engine.is_loop_enabled
        return False

    @property
    def loop_time_range(self) -> tuple:
        """
        获取当前A/B循环的时间范围(毫秒)

        返回:
            (loop_start_ms, loop_end_ms) 元组，若循环未启用则返回 (0.0, 0.0)

        用途: UI层判断点击的小节是否在循环区间内

        [封装] 通过 SynthEngine.loop_time_range 公共属性查询，
        不再直接访问 self._synth_engine._lock / self._synth_engine._loop_*
        """
        if self._synth_engine:
            return self._synth_engine.loop_time_range
        return (0.0, 0.0)
    
    def find_measure_at_scroll_pos(self, scroll_y: float) -> Optional[dict]:
        """
        根据滚动Y位置查找所在的小节信息(点击跳转用)
        
        原理: 在 _playhead_timeline 中对 scroll_y 做二分查找，
              找到点击位置对应的拍条目，再获取该条目所在的小节索引，
              最后找到该小节的第一个拍(起始位置)的时间和scroll_y。
        
        参数:
            scroll_y: 点击处在总内容中的绝对Y坐标(像素)
            
        返回:
            dict包含:
              - meas_idx: 小节索引(int)
              - start_time_ms: 该小节第一个拍的音频时间(毫秒)
              - start_scroll_y: 该小节第一个拍的scroll_y(像素)
            若找不到返回None(非GTP文件或时间线为空时)
            
        用途: 点击谱面时以"小节"为单位定位而非精确像素，
              确保跳转到小节开头，与A/B循环的小节原子单位一致
        """
        if not self._playhead_timeline or not self._timeline_scroll_ys:
            return None
        
        # 边界处理: 超出范围 → 钳位到首/尾
        if scroll_y <= 0:
            first = self._playhead_timeline[0]
            return {
                'global_meas_idx': first.get('global_meas_idx', 0),  # [v2.0.6] 全局唯一ID
                'meas_idx': first.get('meas_idx', 0),               # 局部ID(用于显示)
                'start_time_ms': first.get('time_ms', 0),
                'start_scroll_y': first.get('scroll_y', 0),
            }
        
        last_scroll_y = self._timeline_scroll_ys[-1] if self._timeline_scroll_ys else 0
        if scroll_y >= last_scroll_y:
            # 找到最后一个有效条目所在的小节
            last = self._playhead_timeline[-1]
            target_global_meas = last.get('global_meas_idx', 0)
            # 回溯找该小节第一个拍
            for entry in self._playhead_timeline:
                if entry.get('global_meas_idx') == target_global_meas:  # [v2.0.6] 用全局ID匹配
                    return {
                        'global_meas_idx': target_global_meas,
                        'meas_idx': entry.get('meas_idx', 0),
                        'start_time_ms': entry.get('time_ms', 0),
                        'start_scroll_y': entry.get('scroll_y', 0),
                    }
            return {
                'global_meas_idx': target_global_meas,
                'meas_idx': last.get('meas_idx', 0),
                'start_time_ms': last.get('time_ms', 0),
                'start_scroll_y': last.get('scroll_y', 0),
            }
        
        # 二分查找: 找到第一个 > scroll_y 的位置，退一位
        idx = bisect.bisect_right(self._timeline_scroll_ys, scroll_y) - 1
        if idx < 0:
            idx = 0
        elif idx >= len(self._playhead_timeline):
            idx = len(self._playhead_timeline) - 1
        
        clicked_entry = self._playhead_timeline[idx]
        target_global_meas = clicked_entry.get('global_meas_idx')  # [v2.0.6] 全局唯一ID
        
        # 向前回溯找到该小节的第一个拍(起始位置)
        start_entry = clicked_entry
        for i in range(idx, -1, -1):
            if self._playhead_timeline[i].get('global_meas_idx') == target_global_meas:  # [v2.0.6]
                start_entry = self._playhead_timeline[i]
            else:
                break  # 进入前一个小节，停止
        
        return {
            'global_meas_idx': target_global_meas,
            'meas_idx': start_entry.get('meas_idx', 0),  # 局部ID(用于UI显示)
            'start_time_ms': start_entry.get('time_ms', 0),
            'start_scroll_y': start_entry.get('scroll_y', 0),
        }
    
    def shutdown(self) -> None:
        """
        完全关闭音频引擎并释放所有资源
        
        应在程序退出前调用，确保:
        - 停止播放线程
        - 释放音频设备
        - 释放合成器内存
        """
        if self._synth_engine:
            try:
                self._synth_engine.stop()
                self._synth_engine.shutdown()
            except Exception:
                pass
            finally:
                self._synth_engine = None
    
    # ================================================================
    # 播放光标时间线
    # ================================================================
    
    def build_timeline(self, page_layouts: list, images: List[QPixmap], 
                       display_width: int) -> List[dict]:
        """
        构建播放光标时间线 - 将每个拍映射到其音频时间和屏幕位置
        
        原理:
          遍历所有页面的布局数据(PageLayout→SystemLayout→MeasureLayout→BeatLayout)，
          结合 GTP 歌曲的 BPM 和时间签名，计算每个拍对应的音频时间位置(ms)，
          生成一个按时间排序的时间线索引。
          
        核心改进:
          每个拍同时记录 scroll_y（在总内容中的Y偏移），
          使滚动位置可以由音乐时间驱动，而非线性恒速滚动。
          这样在音符密集区(16/32分音符)播放条自动加快，
          在稀疏区(全/二分音符)自动减慢，与实际音乐节奏同步。
          
        参数:
            page_layouts: TabRenderer 生成的布局数据(List[PageLayout])
            images:       渲染后的页面图像列表(List[QPixmap])
            display_width: 显示区域宽度(px)，用于计算缩放比例
            
        返回:
          List[dict], 每个元素包含:
            - time_ms:   该拍的起始音频时间(毫秒)
            - scroll_y:  该拍在总内容中的Y位置(像素)
            - page_idx:  所在页面索引
            - sys_idx:   所在系统(行)索引
            - meas_idx:  所在小节索引
            - beat_idx:  该小节内的拍索引
            - x_center:  该拍的中心X坐标
            - x_start:   该拍的起始X坐标
            - x_end:     该拍的结束X坐标
            - y_top:     系统顶部Y坐标
            - y_bottom:  系统底部Y坐标
            
        性能优化:
          - 预提取 _timeline_times 和 _timeline_scroll_ys 排序列表
          - 后续 _update_playhead/time_to_scroll_pos 直接复用，避免每帧O(n)分配
        """
        self._playhead_timeline = []
        self._timeline_times = []
        self._timeline_scroll_ys = []
        
        if not page_layouts or not self._song:
            return self._playhead_timeline
        
        # 获取BPM(取第一个tempo标记, 默认120)
        bpm = 120
        if hasattr(self._song, 'tempo_changes') and self._song.tempo_changes:
            bpm = self._song.tempo_changes[0].value
            if bpm <= 0:
                bpm = 120
        elif hasattr(self._song, 'tempo') and self._song.tempo > 0:
            bpm = self._song.tempo
        
        # MIDI 时间参数
        ticks_per_beat = 480  # MIDI 标准分辨率
        ms_per_tick = 60000.0 / (bpm * ticks_per_beat)
        
        current_time_ticks = 0
        current_time_ms = 0.0

        # 计算每页的缩放高度和缩放比(用于累计scroll_y，与paintEvent一致)
        draw_w = display_width - 20  # 减去左右边距
        page_heights = []  # 每页缩放后的高度
        page_scales = []   # 每页的缩放比(width_ratio)
        
        for img in images:
            if img and not img.isNull():
                ratio = draw_w / img.width() if img.width() > 0 else 1
                page_heights.append(img.height() * ratio + 5)
                page_scales.append(ratio)
            else:
                page_heights.append(0)
                page_scales.append(1)
        
        cumulative_y = 0.0  # 累计Y偏移(所有之前页面的总高度)
        global_meas_idx = 0  # [v2.0.6修复] 全局唯一小节ID(跨系统/页不重复)
        
        # 遍历所有页面布局
        for page_idx, page in enumerate(page_layouts):
            page_base_y = cumulative_y  # 当前页的起始Y
            page_scale_ratio = page_scales[page_idx] if page_idx < len(page_scales) else 1
            
            if page_idx < len(page_heights):
                cumulative_y += page_heights[page_idx]
            
            # 遍历该页的所有系统(行)
            for sys_idx, system in enumerate(page.systems):
                # 遍历该系统的所有小节
                for meas_idx, m_layout in enumerate(system.measures):
                    measure = m_layout.measure
                    
                    # 获取该小节的拍号信息
                    if hasattr(measure, 'time_signature'):
                        ts = measure.time_signature
                        numerator = getattr(ts, 'numerator', 4)
                        denominator = getattr(ts, 'denominator', 4)
                    else:
                        numerator, denominator = 4, 4
                    
                    # 计算该小节的总tick数
                    measure_ticks = int(numerator * ticks_per_beat * 4 / max(denominator, 1))
                    
                    beats_in_measure = m_layout.beats
                    if not beats_in_measure:
                        # [v2.0.6修复] 空白小节(无拍/音符): 生成单个占位条目
                        # 原代码用continue跳过 → 该小节无timeline条目 → 点击时定位到相邻有音符的小节(误差大)
                        # 新方案: 记录空白小节的起始scroll_y和time_ms，使点击可正确定位
                        n_measures_in_system = len(system.measures)
                        rel_pos = meas_idx / max(n_measures_in_system, 1)
                        sys_h_render = max(system.y_tab_bottom - system.y_tab_top, 1)
                        
                        scroll_y = (
                            page_base_y
                            + (system.y_tab_top + rel_pos * sys_h_render) * page_scale_ratio
                        )
                        
                        placeholder = {
                            'time_ms': current_time_ms,
                            'scroll_y': scroll_y,
                            'page_idx': page_idx,
                            'sys_idx': sys_idx,
                            'meas_idx': meas_idx,
                            'global_meas_idx': global_meas_idx,
                            'beat_idx': -1,  # 标记为空白小节占位条目
                            'x_center': 0, 'x_start': 0, 'x_end': 0,
                            'y_top': system.y_tab_top,
                            'y_bottom': system.y_tab_bottom,
                        }
                        self._playhead_timeline.append(placeholder)
                        
                        # 累加时间后递增全局ID
                        current_time_ticks += measure_ticks
                        current_time_ms = current_time_ticks * ms_per_tick
                        global_meas_idx += 1
                        continue
                    
                    # 遍历该小节的所有拍
                    # [v1.3.2修复] 记录小节起始 tick，用于末尾对齐 measure_ticks
                    measure_start_ticks = current_time_ticks
                    # [v1.3.3修复] 记录最后一拍的 scroll_y 和结束 tick，用于哨兵 entry
                    last_beat_scroll_y = page_base_y
                    last_beat_end_ticks = current_time_ticks
                    for beat_idx, b_layout in enumerate(beats_in_measure):
                        # === 计算 scroll_y: 每个拍有独立递增的Y位置 ===
                        sys_beats_count = sum(len(m.beats) for m in system.measures)
                        beats_before = (
                            sum(len(m2.beats) for m2 in system.measures[:meas_idx]) 
                            + beat_idx
                        )
                        rel_pos = beats_before / max(sys_beats_count, 1)
                        sys_h_render = max(system.y_tab_bottom - system.y_tab_top, 1)

                        scroll_y = (
                            page_base_y
                            + (system.y_tab_top + rel_pos * sys_h_render) * page_scale_ratio
                        )
                        last_beat_scroll_y = scroll_y  # 记住最后一拍位置
                        
                        # 构建时间线索引条目
                        entry = {
                            'time_ms': current_time_ms,
                            'scroll_y': scroll_y,
                            'page_idx': page_idx,
                            'sys_idx': sys_idx,
                            'meas_idx': meas_idx,
                            'global_meas_idx': global_meas_idx,  # [v2.0.6] 全局唯一ID
                            'beat_idx': beat_idx,
                            'x_center': b_layout.x_center,
                            'x_start': b_layout.x_start,
                            'x_end': b_layout.x_end,
                            'y_top': system.y_tab_top,
                            'y_bottom': system.y_tab_bottom,
                        }
                        self._playhead_timeline.append(entry)
                        
                        # [v1.3.1修复] 按拍的实际时值累加 tick, 与 MidiConverter 保持一致
                        # 原理: 原代码用 measure_ticks // n_beats 把每拍均摊,
                        #       导致四分音符和八分音符占用相同时间, 与 MIDI 事件不同步。
                        #       现在使用 beat.duration_value (已含附点/连音修正),
                        #       确保播放条速度与真实音符时值一致。
                        beat_duration_value = getattr(b_layout.beat, 'duration_value', 1.0)
                        beat_duration_ticks = int(ticks_per_beat * beat_duration_value)
                        current_time_ticks += max(beat_duration_ticks, 1)
                        current_time_ms = current_time_ticks * ms_per_tick
                        last_beat_end_ticks = current_time_ticks
                    
                    # [v1.3.3修复] 在"最后一拍结束时刻"添加哨兵 entry
                    # 原理: timeline 中只有"拍起始"entry, 最后一拍 entry 的 time 是该拍
                    #       **起始**时刻。线性插值会从最后一拍起始直接插值到下一小节第一拍,
                    #       导致最后一音还在响时播放条就已经向下一小节移动了。
                    # 修复: 在"最后一拍结束时刻"添加哨兵 entry (scroll_y = 最后一拍位置)。
                    #       这样插值分成两段:
                    #       1. 最后一拍起始 → 哨兵: scroll_y 不变 (最后一音持续期间停留)
                    #       2. 哨兵 → 下一小节第一拍: 平滑滚动 (空白期自然过渡)
                    # 哨兵 time 用 last_beat_end_ticks (各拍累加结束, v1.3.2 对齐前的值),
                    #       略早于下一小节第一拍 time, 避免 bisect_right 跳过哨兵。
                    if beats_in_measure:
                        sentinel_time_ms = last_beat_end_ticks * ms_per_tick
                        measure_end_time = (measure_start_ticks + measure_ticks) * ms_per_tick
                        # 无截断时两者相等, 需要让哨兵 time 略早以避免 bisect_right 跳过
                        if sentinel_time_ms >= measure_end_time:
                            sentinel_time_ms = measure_end_time - 0.01
                        measure_end_entry = {
                            'time_ms': sentinel_time_ms,
                            'scroll_y': last_beat_scroll_y,
                            'page_idx': page_idx,
                            'sys_idx': sys_idx,
                            'meas_idx': meas_idx,
                            'global_meas_idx': global_meas_idx,
                            'beat_idx': len(beats_in_measure),
                            'x_center': 0, 'x_start': 0, 'x_end': 0,
                            'y_top': system.y_tab_top,
                            'y_bottom': system.y_tab_bottom,
                            '_is_measure_end': True,
                        }
                        self._playhead_timeline.append(measure_end_entry)
                    
                    # [v1.3.2修复] 将 current_time_ticks 对齐到拍号计算的 measure_ticks
                    # 原理: 各拍 beat_duration_ticks 之和可能因 int() 浮点截断（如三连音
                    #       int(480*0.3333)=159）而小于 measure_ticks，导致下一小节提前开始
                    #       （即"最后一音时值还没走完就到下小节"）。
                    #       对齐后确保下一小节第一拍 time 与 MIDI 事件中该小节起始 tick 一致。
                    # measure_ticks 已在第1455行由拍号计算得到
                    current_time_ticks = measure_start_ticks + measure_ticks
                    current_time_ms = current_time_ticks * ms_per_tick
                    
                    # [v2.0.6修复] 每处理完一个小节，全局ID递增(确保跨系统/页唯一)
                    global_meas_idx += 1
        
        # === 后处理: 确保scroll_y严格单调递增 ===
        if self._playhead_timeline:
            # 强制单调递增(防止布局数据异常导致回退)
            # [v1.3.3修复] 跳过哨兵 entry (_is_measure_end=True),
            # 它的 scroll_y 与最后一拍相同, 不应被强制递增
            prev_y = -1.0
            for entry in self._playhead_timeline:
                if entry.get('_is_measure_end', False):
                    prev_y = entry['scroll_y']
                    continue
                if entry['scroll_y'] <= prev_y:
                    entry['scroll_y'] = prev_y + 0.5  # 最小增量保证递增
                prev_y = entry['scroll_y']
            
            # 更新总时长
            self._total_audio_duration_ms = self._playhead_timeline[-1]['time_ms']

            # 总内容高度(与 _calculate_total_distance 一致)
            total_content_h = max(cumulative_y - 5, 0)
            
            last_entry = self._playhead_timeline[-1]
            last_scroll_y = last_entry['scroll_y']
            last_time_ms = last_entry['time_ms']
            remaining_h = total_content_h - last_scroll_y

            # 仅当剩余空白区域超过50px时才添加哨兵点
            if remaining_h > 50:
                first = self._playhead_timeline[0]
                elapsed_y = last_scroll_y - first['scroll_y']
                elapsed_t = max(last_time_ms - first['time_ms'], 1)
                avg_speed = elapsed_y / elapsed_t
                estimated_remaining_ms = remaining_h / max(avg_speed, 0.01)

                sentinel = {
                    'time_ms': last_time_ms + estimated_remaining_ms,
                    'scroll_y': float(total_content_h),
                    'page_idx': len(page_layouts) - 1,
                    # [v0.3.8修复] 哨兵点继承最后1个真实条目的小节索引
                    # 旧代码硬编码meas_idx=0，导致B点在谱子末尾时_find_measure_at_time返回0
                    'sys_idx': last_entry.get('sys_idx', 0),
                    'meas_idx': last_entry.get('meas_idx', 0),
                    'global_meas_idx': last_entry.get('global_meas_idx', 0),  # [v2.0.6] 全局唯一ID
                    'beat_idx': last_entry.get('beat_idx', 0),
                    'x_center': 0, 'x_start': 0, 'x_end': 0,
                    'y_top': 0, 'y_bottom': 0,
                }
                self._playhead_timeline.append(sentinel)
                self._total_audio_duration_ms = sentinel['time_ms']

        # ===== [v0.4.0] 反复记号展开: 同步时间线与MIDI事件 =====
        # 原理: MIDI事件已按 expand_measure_indices 展开, 时间线也必须同步展开,
        #       否则播放光标位置会与音频不同步(声音到了第2遍但光标还在第1遍的位置)。
        #
        # 实现方式:
        #   1. 先正常构建基础时间线(每个原始小节出现一次)
        #   2. 按 global_meas_idx 分组条目
        #   3. 按展开序列重新排列+复制条目
        #   4. 复制的条目保持相同 scroll_y(视觉位置不变), time_ms 按 running_time 累加
        #
        # 关键: 反复时播放光标会"跳回"之前的 scroll_y 位置(因为反复段视觉上在同一位置),
        #       但 time_ms 持续增长(反映实际播放进度)。
        if (self._expanded_measure_indices 
            and len(self._expanded_measure_indices) > 0):
            
            track = self._song.tracks[self._current_track]
            has_expansion = len(self._expanded_measure_indices) > len(track.measures)
            
            if has_expansion and self._playhead_timeline:
                # Step 1: 建立 measure.number → global_meas_idx 映射
                # (GTPMeasure.number 是1-based序号, 与track.measures列表索引+1对应)
                num_to_global = {}
                for entry in self._playhead_timeline:
                    gidx = entry.get('global_meas_idx', -1)
                    midx = entry.get('meas_idx', -1)  # 系统内局部索引(暂时用)
                    num_to_global.setdefault(gidx, gidx)
                
                # 更精确的映射: 遍历原始measures获取 number → global 映射
                # 因为 build_timeline 的 global_meas_idx 是按渲染顺序递增的,
                # 而 track.measures 也是按文件顺序排列的, 所以 i → global 映射是一致的
                idx_to_global = {}
                tmp_global = 0
                for i, m in enumerate(track.measures):
                    idx_to_global[i] = tmp_global
                    tmp_global += 1
                
                # Step 2: 按 global_meas_idx 分组时间线条目
                groups = {}  # global_meas_idx -> [entries]
                for entry in self._playhead_timeline:
                    gidx = entry.get('global_meas_idx', -1)
                    groups.setdefault(gidx, []).append(entry)
                
                # Step 3: 计算每个小节的时长(ms)
                meas_duration = {}  # global_meas_idx -> 时长
                for gidx, entries in groups.items():
                    if len(entries) >= 2:
                        meas_duration[gidx] = entries[-1]['time_ms'] - entries[0]['time_ms']
                    elif len(entries) == 1:
                        meas_duration[gidx] = 100  # 单条目默认100ms
                    else:
                        meas_duration[gidx] = 0
                
                # Step 4: 按展开序列重建时间线
                expanded_timeline = []
                running_time_ms = 0.0  # 累计运行时间(模拟播放器的时间推进)
                
                for seq_pos, orig_idx in enumerate(self._expanded_measure_indices):
                    gidx = idx_to_global.get(orig_idx, orig_idx)
                    entries = groups.get(gidx, [])
                    
                    if not entries:
                        continue
                    
                    # 该小节的时长
                    seg_dur = meas_duration.get(gidx, 100)
                    base_time = entries[0]['time_ms']  # 原始起始时间
                    
                    # 为该小节的每个拍创建新条目
                    for entry in entries:
                        new_entry = dict(entry)
                        
                        # 计算相对时间(在该小节内的位置比例 0~1)
                        rel_pos = 0.0
                        if seg_dur > 0:
                            rel_pos = (entry['time_ms'] - base_time) / seg_dur
                        
                        # 新绝对时间 = 当前运行时间 + 该小节内的相对偏移
                        new_entry['time_ms'] = running_time_ms + rel_pos * seg_dur
                        expanded_timeline.append(new_entry)
                    
                    # 运行时间推进一个小节的时长
                    running_time_ms += seg_dur
                
                # Step 5: 替换时间线并更新派生数据
                if expanded_timeline:
                    self._playhead_timeline = expanded_timeline
                    self._total_audio_duration_ms = running_time_ms

        # === 后处理: 预提取bisect关键排序列表 ===
        # [v0.4.0注意] 展开后的时间线 scroll_y 不再保证单调递增!
        #   反复段会跳回之前的视觉位置, scroll_y 会回退, 这是正确行为。
        #   _timeline_times 仍然单调递增(用于 time_to_scroll_pos 的二分查找)。
        #   _timeline_scroll_ys 可能非单调(用于 scroll_pos_to_time, 反复段内结果可能不精确)。
        if self._playhead_timeline:
            # === 性能优化: 预提取bisect关键排序列表 ===
            self._timeline_times = [e['time_ms'] for e in self._playhead_timeline]
            self._timeline_scroll_ys = [e['scroll_y'] for e in self._playhead_timeline]
        else:
            self._total_audio_duration_ms = 0.0
            self._timeline_times = []
            self._timeline_scroll_ys = []
        
        return self._playhead_timeline
    
    # ================================================================
    # 时间 ↔ 位置 双向映射
    # ================================================================
    
    def update_playhead(self, time_ms: float = None) -> Optional[tuple]:
        """
        根据当前播放时间更新光标位置
        
        参数:
            time_ms: 当前音频时间(毫秒)。None则从 synth_engine 获取。
            
        返回:
          光标信息元组或None:
          (page_idx, sys_idx, meas_idx, beat_idx, x_in_page, progress_in_beat)
          - progress_in_beat ∈ [0,1) 表示当前拍内的进度
          - beat_idx=-1 表示还未到第一个拍
        """
        if not self._playhead_timeline:
            return None
        
        # 获取当前时间
        if time_ms is None and self._synth_engine:
            time_ms = self._synth_engine.current_time_ms
        if time_ms is None:
            return None
        
        # 二分查找: 找到 time_ms 对应的拍(使用预提取的排序列表)
        idx = bisect.bisect_right(self._timeline_times, time_ms) - 1
        
        if idx < 0:
            # 还没到第一个拍
            entry = self._playhead_timeline[0]
            return (
                entry['page_idx'], entry['sys_idx'], entry['meas_idx'],
                -1, entry['x_start'], 0.0
            )
        elif idx >= len(self._playhead_timeline) - 1:
            # 已超过最后一个拍
            entry = self._playhead_timeline[-1]
            return (
                entry['page_idx'], entry['sys_idx'], entry['meas_idx'],
                entry['beat_idx'], entry['x_end'], 1.0
            )
        else:
            # 在两个拍之间 → 插值计算精确X坐标
            curr = self._playhead_timeline[idx]
            next_e = self._playhead_timeline[idx + 1]
            
            dt = next_e['time_ms'] - curr['time_ms']
            progress = (time_ms - curr['time_ms']) / dt if dt > 0 else 0.0
            
            # X坐标线性插值
            x_pos = curr['x_center'] + progress * (next_e['x_center'] - curr['x_center'])
            
            return (
                curr['page_idx'], curr['sys_idx'], curr['meas_idx'],
                curr['beat_idx'], x_pos, progress
            )
    
    def time_to_scroll_pos(self, time_ms: float, 
                           total_scroll_distance: float,
                           display_height: int) -> float:
        """
        根据音频时间计算对应的滚动Y位置
        
        原理:
          在 playhead_timeline 中二分查找当前时间对应的拍，
          通过线性插值获取精确的 scroll_y 值。
          这使得滚动位置随音符时值自动变化：
          - 音符密集区(16/32分音符): 相同时间→更大scroll_y变化 → 滚动更快
          - 音符稀疏区(全/二分音符): 相同时间→更小scroll_y变化 → 滚动更慢
          
        参数:
            time_ms:             当前音频播放时间(毫秒)
            total_scroll_distance: 总滚动距离(像素)
            display_height:       显示区域高度(像素)，用于居中计算
            
        返回:
            对应的滚动Y位置(像素)，范围 [0, total_scroll_distance]
        """
        if not self._playhead_timeline or total_scroll_distance <= 0:
            return 0.0

        # 边界处理
        if time_ms <= 0:
            return 0.0
        if time_ms >= self._playhead_timeline[-1]['time_ms']:
            return float(total_scroll_distance)

        # 二分查找时间位置(使用预提取的排序列表)
        idx = bisect.bisect_right(self._timeline_times, time_ms) - 1

        if idx < 0:
            return 0.0
        if idx >= len(self._playhead_timeline) - 1:
            return float(total_scroll_distance)

        # 线性插值
        curr = self._playhead_timeline[idx]
        next_e = self._playhead_timeline[idx + 1]
        dt = next_e['time_ms'] - curr['time_ms']
        if dt <= 0:
            scroll_y = curr['scroll_y']
        else:
            t = (time_ms - curr['time_ms']) / dt
            scroll_y = curr['scroll_y'] + t * (next_e['scroll_y'] - curr['scroll_y'])

        # 映射到显示区域(减去可视区域高度的一半，让光标居中)
        centered_pos = max(0, scroll_y - display_height / 2)

        return min(centered_pos, float(total_scroll_distance))
    
    def scroll_pos_to_time(self, scroll_pos: float,
                           total_scroll_distance: float,
                           display_height: int) -> float:
        """
        根据滚动Y位置反推对应的音频时间 — time_to_scroll_pos 的逆运算
        
        原理:
          在 playhead_timeline 中对 scroll_y 做二分查找，
          通过线性插值获取精确的 time_ms 值。
          这是 time_to_scroll_pos() 的完全对称逆操作。
        
        为什么不能用线性比例?
          因为 scroll_y 与 time_ms 的关系是非线性的：
          - 密集区: 相同时间内scroll_y变化大 → 每像素对应少时间
          - 稀疏区: 相同时间内scroll_y变化小 → 每像素对应多时间
          用线性比例会在密集区高估时间(音频跳到后面)，导致"提前"感
          
        参数:
            scroll_pos:          滚动位置(像素)，已减去display_h/2的居中值
            total_scroll_distance: 总滚动距离(像素)
            display_height:      显示区域高度(像素)
            
        返回:
            对应的音频时间(毫秒)
        """
        if not self._playhead_timeline or total_scroll_distance <= 0:
            return 0.0
        
        # 边界处理
        if scroll_pos <= 0:
            return 0.0
        if scroll_pos >= total_scroll_distance:
            return self._total_audio_duration_ms
        
        # 将居中位置还原为原始scroll_y(与time_to_scroll_pos中的操作相反)
        raw_scroll_y = scroll_pos + display_height / 2
        
        # 对scroll_y做二分查找(使用预提取的排序列表，与time_to_scroll_pos对称)
        idx = bisect.bisect_right(self._timeline_scroll_ys, raw_scroll_y) - 1
        
        if idx < 0:
            return self._playhead_timeline[0]['time_ms']
        if idx >= len(self._playhead_timeline) - 1:
            return self._playhead_timeline[-1]['time_ms']
        
        # 线性插值(与time_to_scroll_pos对称)
        curr = self._playhead_timeline[idx]
        next_e = self._playhead_timeline[idx + 1]
        dy = next_e['scroll_y'] - curr['scroll_y']
        if dy <= 0:
            return curr['time_ms']
        
        t = (raw_scroll_y - curr['scroll_y']) / dy
        time_ms = curr['time_ms'] + t * (next_e['time_ms'] - curr['time_ms'])
        
        return max(0.0, time_ms)
    
    # ================================================================
    # 工具方法
    # ================================================================
    
    def create_error_image(self, message: str, width: int = 800, 
                           height: int = 500) -> QPixmap:
        """
        创建错误/信息展示图（当GTP引擎不可用时回退显示）
        
        参数:
            message: 显示的错误/信息文本
            width:   图片宽度(px)
            height:  图片高度(px)
            
        返回:
            包含错误信息的 QPixmap 图像
        """
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor('#252536'))  # 使用深色主题背景
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # 标题
        painter.setPen(QColor('#3B82F6'))
        title_font = QFont("Microsoft YaHei", 26, QFont.Bold)
        painter.setFont(title_font)
        filename = "" if not self._file_path else self._file_path
        painter.drawText(QRect(50, 40, width - 100, 60), 
                        Qt.AlignCenter, f"Guitar Pro 文件: {filename}")

        # 信息文本
        painter.setPen(QColor('#E2E8F0'))
        info_font = QFont("Microsoft YaHei", 13)
        painter.setFont(info_font)
        
        lines = message.split('\n')
        y = 130
        for line in lines:
            if line.strip():
                painter.drawText(QRect(50, y, width - 100, 32), Qt.AlignLeft, line)
            y += 30

        # 边框
        painter.setPen(QPen(QColor('#3B82F6'), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(30, 30, width - 60, height - 60, 15, 15)
        painter.end()
        
        return pixmap
    
    def __del__(self):
        """析构时确保释放音频资源"""
        self.shutdown()


# ============================================================
# 便捷函数
# ============================================================

def create_gtp_player(gain: float = 0.7) -> GTPPlayer:
    """
    便捷工厂函数：创建并返回 GTPPlayer 实例
    
    参数:
        gain: 主音量(0.0-1.0), 调整效果: 0.7=适中音量
        
    返回:
        GTPPlayer 实例
        
    示例:
        >>> from gtp_engine.player import create_gtp_player
        >>> player = create_gtp_player(gain=0.8)
        >>> player.load("song.gp5")
        >>> images = player.render_track(0)
    """
    return GTPPlayer(gain=gain)


def render_gtp_to_images(file_path: str, track_index: int = 0,
                         config: RenderConfig = None) -> List[QPixmap]:
    """
    便捷函数：一键渲染GTP文件为图像列表
    
    参数:
        file_path:   .gp3/.gp4/.gp5/.gpx 文件路径
        track_index: 音轨索引（默认第1条）
        config:      自定义渲染配置（可选）
        
    返回:
        QPixmap列表，每元素对应一页乐谱图像
        
    示例:
        >>> from gtp_engine.player import render_gtp_to_images
        >>> pages = render_gtp_to_images("my_song.gp5", track_index=0)
        >>> pages[0].save("output.png", "PNG")
    """
    player = GTPPlayer()
    return player.render_track(track_index, config)
