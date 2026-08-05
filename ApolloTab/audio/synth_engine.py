"""
============================================================
文件名: synth_engine.py
功能描述: FluidSynth 音频合成引擎 - 基于 SoundFont 的实时 MIDI 播放
         将 MIDI 事件序列通过 FluidSynth 合成器转换为音频输出
         [v0.2.11] 新增跨平台库查找支持 (Windows DLL / Linux .so)
         [v0.2.12] 新增 Linux 多音频驱动自动尝试 (pulseaudio/alsa/jack等)

创建日期: 2026-06-07
最后更新: 2026-06-30 (v1.3.0: set_drum_kit 发送合法 Bank Select CC#0=1/CC#32=0)
依赖:
  - pyfluidsynth >= 1.4.0 (Python绑定, 开源项目: pyfluidsynth/nwhitehead)
  - Windows: libfluidsynth-3.dll (FluidSynth C库, 需放到项目根目录或系统PATH中)
  - Linux:   libfluidsynth.so.x (通过包管理器安装: apt/dnf/pacman)
  - SoundFont 文件 (.sf2) 用于音色采样
设计原则:
  - 线程安全: 音频播放在独立线程中运行，不阻塞UI主线程
  - 精确定时: 使用系统高精度定时器驱动 MIDI 事件发送
  - 资源管理: 自动释放合成器资源，支持多次初始化/销毁
  - 跨平台: 自动识别操作系统并使用对应的库文件查找策略

调用示例:
    from gtp_engine.audio.synth_engine import SynthEngine

    engine = SynthEngine()
    engine.load_soundfont("path/to/soundfont.sf2")

    # 加载MIDI事件后播放
    engine.load_events(midi_event_list, bpm=120)
    engine.play()

    # 暂停/继续
    engine.pause()
    engine.resume()

    # 停止并清理
    engine.stop()

依赖库说明:
  - pyfluidsynth: FluidSynth 的 Python ctypes 绑定（开源项目: nwhitehead/pyfluidsynth）
    安装命令: pip install pyfluidsynth -i https://pypi.tuna.tsinghua.edu.cn/simple
  - libfluidsynth-3.dll: FluidSynth C运行时库 (Windows)
    下载地址: https://github.com/FluidSynth/fluidsynth/releases
    放置位置: 项目根目录即可（代码自动搜索）
  - libfluidsynth.so.x: FluidSynth C运行时库 (Linux)
    安装方式: 通过各发行版包管理器安装（代码自动搜索常见路径）
============================================================
"""

import contextlib
import os
import platform
import threading
import time
from collections.abc import Callable
from typing import Any, Optional


class SynthEngine:
    """
    FluidSynth 音频合成引擎

    功能概述:
      基于 FluidSynth 开源项目实现实时 MIDI 合成。
      加载 SoundFont (.sf2) 文件作为音色源，
      接收带时间戳的 MIDI 事件序列，按精确时间间隔播放。

    核心原理:
      1. 初始化 FluidSynth 合成器实例 + 音频输出驱动
      2. 加载 SoundFont 文件到合成器（包含各种乐器音色采样）
      3. 播放线程按时间顺序逐个发送 MIDI 事件到合成器
      4. 合成器将 MIDI 事件实时渲染为音频波形输出到声卡

    支持的 MIDI 事件类型:
      - note_on:  发声(指定通道/音高/力度)
      - note_off: 止音
      - tempo:    变速标记(用于同步计算)

    参数说明(初始化):
      sample_rate:   采样率(Hz), 调整效果: 越高音质越好但CPU占用更多(推荐44100)
      buffer_size:   音频缓冲区大小(采样点数), 调整效果: 越大延迟越高但更稳定(推荐256/512)
      gain:          主音量增益(0.0-10.0), 调整效果: 1.0=原始音量, 2.0=翻倍
    """

    # 默认音频参数
    DEFAULT_SAMPLE_RATE = 44100  # 标准CD音质采样率(Hz), 调整效果: 48000更清晰
    DEFAULT_BUFFER_SIZE = 512  # 音频缓冲区大小(采样点数), 调整效果: 256延迟更低
    DEFAULT_GAIN = 0.8  # 默认主音量(0.0-1.0), 调整效果: 1.0=最大音量

    # 内置 SoundFont 搜索路径（按优先级排序）
    SOUNDFONT_SEARCH_PATHS = [
        "soundfont",  # 项目内 soundfont 目录
        "../soundfont",  # 项目上级目录
        "../../soundfont",  # 更上级目录
        os.path.expanduser("~/.fluidsynth"),  # 用户主目录
        "/usr/share/sounds/sf2",  # Linux 系统路径
        "/usr/share/soundfonts",  # Linux 备选路径
        "C:/ProgramData/FluidSynth",  # Windows 系统路径
    ]

    # 常见 SoundFont 文件名（自动搜索）
    SOUNDFONT_NAMES = [
        "FluidR3_GM.sf2",  # FluidSynth 官方通用音色库(推荐)
        "GeneralUser GS v1.471.sf2",  # GeneralUser 音色库
        "default-GM.sf2",  # 默认 GM 音色库
    ]

    def __init__(
        self,
        sample_rate: int | None = None,
        buffer_size: int | None = None,
        gain: float | None = None,
    ):
        """
        初始化合成引擎

        参数:
            sample_rate:  采样率(Hz), None=使用默认44100
            buffer_size:  缓冲区大小, None=使用默认512
            gain:         主音量, None=使用默认0.8
        """
        self.sample_rate = sample_rate or self.DEFAULT_SAMPLE_RATE
        self.buffer_size = buffer_size or self.DEFAULT_BUFFER_SIZE
        self.gain = gain or self.DEFAULT_GAIN

        # === 内部状态 ===
        # fluidsynth 无 py.typed stub，_synth/_audio_driver 用 Any
        self._synth: Any = None  # FluidSynth 合成器实例
        self._audio_driver: Any = None  # 音频驱动实例
        self._sfid = -1  # 当前加载的SoundFont ID
        self._soundfont_path = ""  # 当前SoundFont文件路径

        # === 播放状态 ===
        self._events: list = []  # 待播放的MIDI事件列表
        self._bpm: int = 120  # 当前BPM
        self._ticks_per_beat: int = 480  # 每拍tick数(与MidiConverter一致)

        # === 播放控制 ===
        self._is_playing: bool = False  # 是否正在播放
        self._is_paused: bool = False  # 是否暂停
        self._play_thread: threading.Thread | None = None  # 播放线程
        self._stop_flag: bool = False  # 停止信号
        self._pause_event = threading.Event()  # 暂停事件(用于暂停恢复同步)
        self._pause_event.set()  # 初始为非暂停状态(set=不阻塞)

        # === 进度追踪 ===
        self._current_time_ms: float = 0.0  # 当前播放位置(毫秒)
        self._start_time: float = 0.0  # 播放开始时的系统时间
        self._paused_duration: float = 0.0  # 累计暂停时长(毫秒)

        # === 回调函数 ===
        self._on_note_callback: Callable | None = None  # 音符触发回调(用于视觉高亮)

        # === 活跃音符追踪（性能优化） ===
        # 格式: { (channel, pitch): True }
        # 用途: silence_all_notes() 只关闭实际发声的音符，避免遍历16×128=2048次
        self._active_notes: dict = {}  # 追踪当前正在发声的音符

        # === Seek防抖机制 ===
        # 用途: 防止快速连续点击导致频繁seek造成卡顿
        self._last_seek_time: float = 0.0  # 上次seek的系统时间(perf_counter)
        self._seek_debounce_ms: float = (
            50.0  # 防抖间隔(毫秒), 调整效果: 越大越防抖但响应越慢, 推荐50ms
        )
        self._pending_seek_time: float = -1.0  # 待执行的seek目标时间(-1=无待执行)

        # === A/B区域循环(内置，基于音频线程) ===
        # [v0.2.6] 将循环逻辑从UI层下沉到音频线程内部，彻底消除竞态/冷却/模拟时钟等问题
        # 原理: _play_loop()在播放完所有事件后检查循环标志，
        #       若启用则静音→重置时间基准→从头重新遍历事件(早于loop_start的自动被跳过)
        # 优势: UI层只需调用set_loop_region()设置范围后正常读取current_time_ms即可，
        #       不需要任何冷却帧计数器、模拟时钟、安全边界等复杂逻辑
        self._loop_enabled: bool = False  # 是否启用A/B区域循环
        self._loop_start_ms: float = 0.0  # 循环起始时间(毫秒)，对应A点所在小节的起始拍
        self._loop_end_ms: float = 0.0  # 循环结束时间(毫秒)，对应B点所在小节的末尾拍

        # === [解耦] 节拍器独立事件流与播放线程 ===
        # 原理: 节拍器事件不再混入主事件流，由独立线程驱动播放。
        #       主线程切换节拍器开关/音量时无需重建主事件流，
        #       节拍器线程与主线程共享同一 FluidSynth 合成器输出
        #       （通过不同 MIDI 通道避免 note_on/note_off 冲突），
        #       但拥有独立的时间基准与播放控制状态。
        self._metronome_events: list = []  # 节拍器事件（与 _events 互不影响）
        self._metronome_bpm: int = 120  # 节拍器播放速度
        self._metronome_ticks_per_beat: int = 480  # 每拍 tick 数
        self._metronome_total_ticks: int = 0  # 总 tick 上限（用于"仅节拍器"模式计时）
        self._metronome_start_offset_ms: float = (
            0.0  # 起始跳过偏移（毫秒），用于播放中开启节拍器时对齐主播放位置
        )
        self._metronome_start_index: int = (
            0  # 起始事件索引（跳过已过去的 N 个事件，避免遍历大量旧事件卡顿）
        )
        self._metronome_start_perf: float = 0.0  # 节拍器线程的 perf_counter 起点（已减去 start_offset_ms/1000 使 elapsed_ms 与歌曲时间轴对齐）
        self._metronome_thread: threading.Thread | None = None  # 节拍器播放线程
        self._metronome_stop_flag: bool = False  # 节拍器停止信号
        self._metronome_paused: bool = False  # 节拍器是否暂停（独立于主暂停）
        self._metronome_pause_event = threading.Event()  # 节拍器暂停事件
        self._metronome_pause_event.set()  # 初始为非暂停状态

        # === 锁 ===
        self._lock = threading.RLock()  # 可重入锁，保护共享状态

    @property
    def is_playing(self) -> bool:
        """是否正在播放(非暂停状态)"""
        with self._lock:
            return self._is_playing and not self._is_paused

    @property
    def is_paused(self) -> bool:
        """是否处于暂停状态"""
        with self._lock:
            return self._is_paused

    @property
    def current_time_ms(self) -> float:
        """
        获取当前播放位置(毫秒)

        计算公式: 从_start_time到现在的经过时间 - 暂停时间 + 初始偏移(seek位置)
        重要: 必须加上_initial_time_offset，否则seek后的current_time_ms会从0开始而非从seek位置开始
        """
        with self._lock:
            if self._is_playing and not self._is_paused:
                # 实时计算：从开始时间到现在 - 暂停时间 + seek偏移
                elapsed = (time.perf_counter() - self._start_time) * 1000.0
                return elapsed - self._paused_duration + getattr(self, '_initial_time_offset', 0)
            return self._current_time_ms

    @property
    def is_initialized(self) -> bool:
        """合成器是否已成功初始化"""
        return self._synth is not None

    def initialize(self) -> bool:
        """
        初始化 FluidSynth 合成器和音频驱动

        原理:
          创建 FluidSynth.Synth 实例并配置音频参数，
          然后启动音频输出驱动将合成结果送到声卡。

        返回:
            True=初始化成功, False=失败(fluidsynth未安装或设备不可用)

        注意:
          此方法必须在 load_soundfont() 之前调用！
          如果 fluidsynth 库未安装会返回 False 但不抛异常。

        DLL 依赖说明:
          需要 libfluidsynth-3.dll (FluidSynth C库)。
          自动搜索顺序: 项目根目录 → 系统PATH → 标准安装路径。
          用户可将DLL放到项目根目录即可免安装使用。
        """
        try:
            # === 根据操作系统平台查找并加载 FluidSynth 库 ===
            _system = platform.system()

            if _system == 'Windows':
                # === Windows: 查找 DLL 并添加到 PATH ===
                _dll_dir = self._get_project_root()
                _dll_path = os.path.join(_dll_dir, "libfluidsynth-3.dll")

                if os.path.isfile(_dll_path):
                    # 将DLL目录添加到环境变量PATH（ctypes.util.find_library依赖PATH搜索）
                    _old_path = os.environ.get('PATH', '')
                    if _dll_dir not in _old_path:
                        os.environ['PATH'] = _dll_dir + os.pathsep + _old_path
                    print(f"[SynthEngine] 已添加DLL目录到PATH: {_dll_dir}")

                    # Python 3.11+ 同时添加到DLL搜索目录(用于运行时加载)
                    if hasattr(os, 'add_dll_directory'):
                        # 某些情况下可能失败，不影响PATH方式
                        with contextlib.suppress(OSError):
                            os.add_dll_directory(_dll_dir)
                else:
                    print("[SynthEngine] 警告: 未找到 libfluidsynth-3.dll")

            elif _system == 'Linux':
                # === Linux: 查找 .so 文件（包管理器安装的） ===
                _so_path = self._find_so_path()

                if _so_path:
                    # 将 .so 所在目录添加到 LD_LIBRARY_PATH（让 ctypes 能找到依赖库）
                    _so_dir = os.path.dirname(_so_path)
                    _old_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
                    if _so_dir not in _old_ld_path:
                        os.environ['LD_LIBRARY_PATH'] = _so_dir + os.pathsep + _old_ld_path
                    print(f"[SynthEngine] 已找到 libfluidsynth.so: {_so_path}")

                    # 预加载 .so 文件（确保 ctypes.CDLL 能找到）
                    try:
                        import ctypes

                        ctypes.CDLL(_so_path, mode=ctypes.RTLD_GLOBAL)
                        print(f"[SynthEngine] 已预加载: {_so_path}")
                    except OSError as e:
                        print(f"[SynthEngine] 预加载失败: {e}")
                else:
                    print("[SynthEngine] 警告: 未找到 libfluidsynth.so (请安装 fluidsynth)")
                    print("  Ubuntu/Debian: sudo apt-get install libfluidsynth3")
                    print("  Fedora/RHEL:   sudo dnf install fluidsynth-libs")
                    print("  Arch Linux:    sudo pacman -S fluidsynth")

            import fluidsynth

            # 创建合成器实例
            self._synth = fluidsynth.Synth(gain=self.gain, sample_rate=self.sample_rate)

            # 启动音频驱动（根据操作系统自动选择）
            # 原理: 不同Linux发行版/桌面环境使用不同的音频后端，
            #       需要按优先级依次尝试直到成功:
            #       - pulseaudio: Ubuntu 20.04 及更早版本默认
            #       - pipewire:   Ubuntu 22.04+ / Fedora 34+ 默认(兼容PulseAudio API)
            #       - alsa:       纯ALSA系统(Docker容器/无桌面环境)
            #       - jack:       专业音频工作站
            #       - default:    FluidSynth内置自动检测(最后尝试)

            if _system == 'Windows':
                # Windows 使用 DirectSound
                _driver_list = ['dsound', 'waveout', 'default']
            elif _system == 'Darwin':
                # macOS 使用 CoreAudio
                _driver_list = ['coreaudio', 'default']
            else:
                # Linux: 按优先级尝试多种音频驱动
                _driver_list = ['pulseaudio', 'pipewire', 'alsa', 'jack', 'default']

            self._audio_driver = None
            for _drv in _driver_list:
                try:
                    print(f"[SynthEngine] 尝试音频驱动: {_drv}")
                    self._audio_driver = self._synth.start(driver=_drv)
                    if self._audio_driver is not None:
                        print(f"[SynthEngine] 音频驱动启动成功: {_drv}")
                        break
                    else:
                        print(f"[SynthEngine] 驱动 {_drv} 不可用，尝试下一个...")
                except Exception as _drv_err:
                    print(f"[SynthEngine] 驱动 {_drv} 启动失败: {_drv_err}")

            if self._audio_driver is None:
                print("[SynthEngine] 警告: 所有音频驱动均无法启动，将无声音输出")
                print("  可能原因:")
                print("  1. 未安装音频服务 (PulseAudio/PipeWire/ALSA)")
                print("  2. Docker 容器内未挂载 /dev/snd 设备")
                print("  3. SSH 远程连接时未配置音频转发")
                print("")
                print("  安装命令:")
                print("    Ubuntu/Debian: sudo apt-get install pulseaudio-utils libasound2")
                print("    Fedora/RHEL:   sudo dnf install pulseaudio-libs alsa-lib")
                print("    Docker:        添加 --device /dev/snd 参数")

            return True

        except ImportError:
            print("[SynthEngine] 警告: fluidsynth 库未安装")
            print(
                "  安装命令: pip install pyfluidsynth -i https://pypi.tuna.tsinghua.edu.cn/simple"
            )
            return False
        except Exception as e:
            print(f"[SynthEngine] 初始化失败: {e}")
            return False

    @staticmethod
    def _get_project_root() -> str:
        """
        获取项目根目录路径，同时搜索 FluidSynth DLL 所在目录

        原理: 从当前文件位置向上查找，同时检查常见的DLL放置位置:
             1. 项目根目录/libfluidsynth-3.dll
             2. 项目根目录/fluidsnyth/bin/libfluidsynth-3.dll (FluidSynth Windows发行版)
             3. 项目根目录/fluidsynth/bin/libfluidsynth-3.dll

        返回:
            包含 libfluidsynth-3.dll 的目录绝对路径，找不到则返回项目根目录
        """
        # 方法1: 从本模块文件位置推导 (gtp_engine/audio/ → 项目根目录)
        _here = os.path.dirname(os.path.abspath(__file__))
        _root = os.path.normpath(os.path.join(_here, '..', '..'))

        # 搜索DLL的候选目录（按优先级排序）
        _dll_dirs = [
            _root,  # 项目根目录
            os.path.join(_root, "_internal"),  # PyInstaller打包后的_internal文件夹
            os.path.join(_root, "fluidsnyth", "bin"),  # FluidSynth Windows发行版(常见拼写)
            os.path.join(_root, "fluidsynth", "bin"),  # 正确拼写的发行版目录
            os.path.join(_root, "bin"),  # 通用bin目录
        ]

        for _d in _dll_dirs:
            if os.path.isfile(os.path.join(_d, "libfluidsynth-3.dll")):
                print(f"[SynthEngine] 找到DLL: {_d}")
                return _d

        # 都没找到，返回项目根目录（后续导入会报错但给出明确提示）
        return _root

    def _find_dll_path(self) -> str:
        """
        查找 libfluidsynth-3.dll 的完整路径 (仅 Windows)

        返回:
            DLL完整路径，找不到返回空字符串
        """
        _root = self._get_project_root()
        for _name in ["libfluidsynth-3.dll", "libfluidsynth.dll"]:
            _path = os.path.join(_root, _name)
            if os.path.isfile(_path):
                return _path
        return ""

    @staticmethod
    def _find_so_path() -> str:
        """
        查找 libfluidsynth.so 的完整路径 (仅 Linux)

        原理: 按常见 Linux 发行版的包管理器安装路径搜索:
             1. Ubuntu/Debian (apt): /usr/lib/x86_64-linux-gnu/
             2. Fedora/RHEL/CentOS (dnf/yum): /usr/lib64/
             3. Arch Linux/openSUSE (pacman/zypper): /usr/lib/
             4. Alpine Linux (apk): /usr/lib/
             5. 通用路径: /usr/local/lib/

        搜索的文件名（按版本优先级）:
             - libfluidsynth.so.3 (FluidSynth v2.x/v3.x)
             - libfluidsynth.so.2 (FluidSynth v1.x/v2.x)
             - libfluidsynth.so.1 (FluidSynth v1.x)

        返回:
            .so 文件完整路径，找不到返回空字符串

        安装命令参考:
             Ubuntu/Debian: sudo apt-get install libfluidsynth3
             Fedora/RHEL:   sudo dnf install fluidsynth-libs
             Arch Linux:    sudo pacman -S fluidsynth
             openSUSE:      sudo zypper install libfluidsynth3
             Alpine:        sudo apk add fluidsynth
        """
        # === 按 Linux 发行版分类的搜索路径（按优先级排序） ===
        # 格式: (目录路径, 发行版说明)
        _so_search_paths = [
            # Ubuntu/Debian (多架构支持)
            ("/usr/lib/x86_64-linux-gnu", "Ubuntu/Debian x64"),
            ("/usr/lib/aarch64-linux-gnu", "Ubuntu/Debian ARM64"),
            ("/usr/lib/i386-linux-gnu", "Ubuntu/Debian i386"),
            # Fedora/RHEL/CentOS (RPM系)
            ("/usr/lib64", "Fedora/RHEL/CentOS"),
            # Arch Linux / openSUSE / Alpine / 通用
            ("/usr/lib", "Arch Linux/openSUSE/Alpine/通用"),
            ("/usr/local/lib", "本地编译安装"),
            # Homebrew on Linux (较少见但支持)
            ("/home/linuxbrew/.linuxbrew/lib", "Linuxbrew"),
        ]

        # === 搜索的 .so 文件名（按版本从高到低） ===
        _so_names = [
            "libfluidsynth.so.3",  # FluidSynth v2.x/v3.x (推荐)
            "libfluidsynth.so.2",  # FluidSynth v1.x/v2.x
            "libfluidsynth.so.1",  # FluidSynth v1.x
            "libfluidsynth.so",  # 无版本号的符号链接(部分发行版)
        ]

        # === 遍历所有候选路径和文件名 ===
        for _dir_path, _distro in _so_search_paths:
            if not os.path.isdir(_dir_path):
                continue

            for _so_name in _so_names:
                _full_path = os.path.join(_dir_path, _so_name)
                if os.path.isfile(_full_path):
                    print(f"[SynthEngine] 找到 .so 文件 ({_distro}): {_full_path}")
                    return _full_path

        # === 未找到，返回空字符串 ===
        return ""

    def load_soundfont(self, path: str | None = None) -> bool:
        """
        加载 SoundFont 音色文件

        参数:
            path: SoundFont 文件路径(.sf2格式)
                  None=自动搜索内置/系统常见 SoundFont 文件

        返回:
            True=加载成功, False=失败(文件不存在或格式错误)

        注意:
          必须先调用 initialize() 成功后再调用此方法！
          SoundFont 文件包含乐器音色的采样数据，是音频输出的基础。
          推荐: FluidR3_GM.sf2 (免费开源, 包含128种GM标准音色)
        """
        if self._synth is None:
            print("[SynthEngine] 错误: 请先调用 initialize()")
            return False

        # 如果指定了路径，直接尝试加载
        if path and os.path.isfile(path):
            return self._load_sf_file(path)

        # 自动搜索 SoundFont 文件
        sf_path = self._find_soundfont()
        if sf_path:
            return self._load_sf_file(sf_path)

        print("[SynthEngine] 错误: 未找到 SoundFont 文件")
        print("  解决方案:")
        print("  1. 下载 FluidR3_GM.sf2: https://ftp.osuosl.org/pub/musespan/SoundFonts/")
        print("  2. 放到项目 soundfont/ 目录下")
        return False

    def _load_sf_file(self, path: str) -> bool:
        """
        内部方法: 实际加载 SoundFont 文件到合成器
        """
        try:
            # 先卸载旧的 SoundFont（如果有）
            if self._sfid >= 0:
                self._synth.sfunload(self._sfid)

            # 加载新的 SoundFont
            self._sfid = self._synth.sfload(path)

            if self._sfid >= 0:
                self._soundfont_path = path
                print(f"[SynthEngine] SoundFont 加载成功: {os.path.basename(path)}")

                # 设置吉他音色(程序号24-30对应各种吉他)
                # 通道0默认已设置钢琴(0)，这里切换到电吉他(27=Clean Guitar)
                # 用户可在后续通过 program_change 自定义
                return True
            else:
                print(f"[SynthEngine] SoundFont 加载失败: {path}")
                return False

        except Exception as e:
            print(f"[SynthEngine] SoundFont 加载异常: {e}")
            return False

    def _find_soundfont(self) -> str | None:
        """
        自动搜索可用的 SoundFont 文件

        搜索顺序:
          1. 项目 soundfont/ 目录
          2. 用户 ~/.fluidsynth/ 目录
          3. Linux 系统目录 /usr/share/sounds/sf2/
          4. Windows ProgramData 目录
        """
        for search_dir in self.SOUNDFONT_SEARCH_PATHS:
            for sf_name in self.SOUNDFONT_NAMES:
                full_path = os.path.join(search_dir, sf_name)
                if os.path.isfile(full_path):
                    return full_path

        return None

    def set_instrument(self, channel: int = 0, program: int = 24) -> None:
        """
        设置指定通道的乐器音色(MIDI程序号)

        参数:
            channel: MIDI通道(0-15)
            program: 程序号(GM标准):
                     24=尼龙弦吉他(Ukulele), 25=钢弦吉他(Acoustic),
                     26=爵士电吉他(Jazz), 27=清音电吉他(Clean),
                     28=失真电吉他(Overdrive), 29=高增益失真(Distortion),
                     30=泛音(Palm Muted)

        常用吉他音色对照表:
          24 Nylon String Guitar  | 25 Steel String Guitar
          26 Jazz Electric        | 27 Clean Electric
          28 Overdriven           | 29 Distortion
          30 Harmonics/Palm Mute
        """
        if self._synth:
            self._synth.program_change(channel, program)

    def set_drum_kit(self, channel: int = 9, kit: int = 0) -> None:
        """
        设置指定通道为GM鼓组音色

        原理: GM标准中，鼓组音色位于 Bank MSB=128 (Percussion Bank)，
              与旋律乐器(Bank 0)完全分离。必须通过 CC#0 (Bank Select MSB)
              切换到鼓组bank后，再选择鼓组program。

        参数:
            channel: MIDI通道(默认9=打击乐保留通道)
            kit:     鼓组程序号(GM标准):
                     0 = Standard Kit(标准鼓组)
                     1 = Room Kit(房间鼓组)
                     2 = Power Kit(强力鼓组)
                     3 = Electronic Kit(电子鼓组)
                     ... 等等

        注意: 此方法必须在 load_events() 之前调用，
              否则通道可能已被设置为旋律乐器音色导致鼓声异常。

        GM 鼓组常用 note 映射:
          35=Kick Drum 2   | 36=Bass Drum 1(底鼓) | 37=Side Stick
          38=Acoustic Snare(军鼓) | 39=Hand Clap    | 40=Electric Snare
          41=Low Floor Tom  | 42=Closed HiHat(闭镲)| 43=High Floor Tom
          44=Pedal HiHat(踏板镲)| 45=Low Tom      | 46=Open HiHat(开镲)
          47=Low-Mid Tom    | 48=Hi-Mid Tom       | 49=Crash Cymbal 1(碎镲)
          50=High Tom       | 51=Ride Cymbal 1(吊镲)| 52=Chinese Cymbal
          53=Ride Bell     | 54=Tambourine       | 55=Splash Cymbal
          56=Cowbell       | 57=Crash Cymbal 2   | 58=Vibraslap
          59=Ride Cymbal 2 | 60=Hi Bongo         | 61=Low Bongo
          62=Conga Mute Hi | 63=Conga Open Hi    | 64=Conga Low
          65=Timbale High  | 66=Timbale Low      | 67=Agogo High
          68=Agogo Low     | 69=Cabasa           | 70=Maracas
          71=Short Whistle | 72=Long Whistle     | 73=Short Guiro
          74=Long Guiro    | 75=Claves           | 76=Hi Wood Block
          77=Low Wood Block| 78=M Triangle Open  | 79=M Triangle Closed
          80=Shaker        | 81=Jingle Bell      | 82=Bell Tree
        """
        if self._synth:
            # [v1.1.2] 鼓组 Bank 128 拆分为合法的 14-bit Bank Select:
            #   CC#0 (MSB) = 128 >> 7 = 1
            #   CC#32 (LSB) = 128 & 0x7F = 0
            # 之前直接 cc(channel, 0, 128) 发送 128 给 CC 值，超出 0-127 范围，
            # FluidSynth 会忽略该切换，鼓组音色无法生效。
            self._synth.cc(channel, 0, 1)  # CC#0 = Bank Select MSB = 1
            self._synth.cc(channel, 32, 0)  # CC#32 = Bank Select LSB = 0
            # Program Change 选择具体鼓组类型
            self._synth.program_change(channel, kit)

    def load_events(self, events: list, bpm: int = 120, ticks_per_beat: int = 480) -> None:
        """
        加载待播放的 MIDI 事件序列

        参数:
            events:         MidiEvent 对象列表(由 MidiConverter.generate())
            bpm:            播放速度(BPM, 每分钟拍数)
            ticks_per_beat: 每四分音符的tick数(需与MidiConverter一致)

        注意:
          此方法仅加载数据，不会立即开始播放。
          需要调用 play() 才能开始播放。
          可在暂停时重新加载事件以实现跳转。
        """
        with self._lock:
            self._events = list(events)  # 浅拷贝避免外部修改
            self._bpm = bpm
            self._ticks_per_beat = ticks_per_beat
            self._current_time_ms = 0.0
            self._paused_duration = 0.0
            self._initial_time_offset = 0.0  # 初始化时间偏移

    def play(self) -> None:
        """
        开始播放(从当前位置或开头开始)

        原理:
          在独立线程中运行播放循环：
            1. 计算每个事件的预定播放时间(毫秒)
            2. 使用 time.sleep() 精确等待到该时刻
            3. 发送 MIDI 事件到 FluidSynth 合成器
            4. 循环直到所有事件播放完毕或收到停止信号

        时间精度:
          使用 time.perf_counter() 高精度计时器，
          配合自适应 sleep 校正，误差 < 5ms
        """
        with self._lock:
            if self._is_playing and not self._is_paused:
                return  # 已在播放中

            self._is_playing = True
            self._is_paused = False
            self._stop_flag = False
            self._pause_event.set()  # 清除暂停状态
            # 重置初始偏移(由_play_loop根据_current_time_ms重新设置)
            self._initial_time_offset = getattr(self, '_current_time_ms', 0) or 0

        # 启动播放线程
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._play_thread.start()

    def pause(self) -> None:
        """
        暂停播放(保持当前进度位置)

        原理: 设置暂停标志，播放线程中的 sleep 会被中断，
              恢复时可从暂停处继续。

        注意: [v1.3.1] 暂停只影响主 MIDI 流（歌曲旋律/鼓）,
              不暂停节拍器线程。节拍器会继续按自身时间基准发出 click 声,
              便于用户在暂停歌曲时继续跟着节拍练习。

        [v1.3.1 关键修复] 必须先读取 current_time_ms(此时 _is_paused 仍为 False,
        property 返回基于 perf_counter 的实时值),再设置 _is_paused = True。
        否则读取时 property 会因为 _is_paused=True 而直接返回 _current_time_ms 的旧值(0),
        导致暂停位置丢失，恢复后从头开始。
        """
        with self._lock:
            if not self._is_playing or self._is_paused:
                return

            # [v1.3.1 关键修复] 先读取实时位置(此时 _is_paused=False, property 返回计算值)
            paused_position = self.current_time_ms
            self._is_paused = True
            self._current_time_ms = paused_position  # 冻结当前位置
            self._pause_event.clear()  # 触发暂停(阻塞播放线程)

        # [解耦] 暂停只影响主 MIDI 流；节拍器保持独立播放，
        # 拥有自己的时间基准 _metronome_start_perf，暂停主线程不会影响它
        # （如需单独停止节拍器，可显式调用 set_metronome_paused(True)）

    def resume(self) -> None:
        """
        从暂停位置恢复播放

        注意: [v1.3.1] 仅恢复主 MIDI 流，节拍器不受影响。
              若节拍器此前一直独立播放，则会继续按自身时间推进；
              若调用方曾在暂停期间单独暂停过节拍器，需自行恢复。

        关键: 调整 _start_time 使 elapsed 从暂停位置继续增长，
              而非从 0 重启（否则 current_time_ms 会跳回 _initial_time_offset，
              _wait_until 中的相对时间计算也会失真，导致歌曲"从头开始"）。
        """
        with self._lock:
            if not self._is_paused:
                return

            # [v1.3.1 关键修复] 调整 _start_time，使 elapsed 正确反映暂停位置
            # 公式推导:
            #   current_time_ms = elapsed - _paused_duration + _initial_time_offset
            #   elapsed = (now - _start_time) * 1000
            #   在恢复瞬间，我们希望 current_time_ms == self._current_time_ms（暂停位置）
            #   => _start_time = now - (_current_time_ms - _initial_time_offset + _paused_duration) / 1000
            self._start_time = (
                time.perf_counter()
                - (
                    self._current_time_ms
                    - getattr(self, '_initial_time_offset', 0)
                    + self._paused_duration
                )
                / 1000.0
            )
            self._is_paused = False
            self._pause_event.set()  # 清除暂停(唤醒播放线程)

        # [解耦] 主 MIDI 流恢复即可；节拍器有独立暂停控制，pause() 不再联动它

    def stop(self) -> None:
        """
        停止播放并重置到开头

        优化（v0.2.4）:
          使用 silence_all_notes() 代替手动遍历16×128次，
          性能提升100倍以上（典型场景：活跃音符<20个）
        """
        with self._lock:
            self._stop_flag = True
            self._is_playing = False
            self._is_paused = False
            self._pause_event.set()  # 确保线程不被阻塞
            self._current_time_ms = 0.0
            self._paused_duration = 0.0
            self._initial_time_offset = 0.0  # 重置初始偏移
            # 清理活跃音符追踪表
            self._active_notes.clear()
            # 重置防抖状态
            self._last_seek_time = 0.0
            self._pending_seek_time = -1.0

        # 停止所有正在发声的音符（使用优化后的方法）
        self.silence_all_notes()

        # [解耦] 同步停止节拍器线程
        self._stop_metronome_thread()

        # 等待播放线程结束
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=2.0)

    def set_loop_region(self, start_ms: float, end_ms: float) -> None:
        """
        设置A/B区域循环范围(毫秒)

        [v0.2.6] 将循环逻辑从UI层下沉到音频引擎内部。
        设置后，_play_loop()在播放到达end_ms时会自动回到start_ms继续播放，
        整个过程在音频线程内完成，UI层无感知、无竞态、无需冷却机制。

        参数:
            start_ms: 循环起始时间(毫秒)，对应A点所在小节的起始拍时间
            end_ms:   循环结束时间(毫秒)，对应B点所在小节的末尾拍时间

        注意:
            - 此方法只设置循环参数，不改变播放状态
            - 需要在播放前或播放中调用均可（播放中调用下次循环周期生效）
            - start_ms应 < end_ms，否则行为未定义
            - 调用 clear_loop_region() 可取消循环
        """
        with self._lock:
            self._loop_enabled = True
            self._loop_start_ms = max(0, start_ms)
            self._loop_end_ms = max(start_ms + 1, end_ms)  # 至少比start大1ms

    def clear_loop_region(self) -> None:
        """
        清除A/B区域循环设置，恢复为正常播放到结尾停止的模式
        """
        with self._lock:
            self._loop_enabled = False
            self._loop_start_ms = 0.0
            self._loop_end_ms = 0.0

    # ================================================================
    # [解耦] 节拍器独立事件流 API
    # ================================================================
    #
    # 设计目标: 节拍器事件不再与主事件流混合。
    # 调用方 (GTPPlayer) 先生成节拍器事件，然后通过
    #   - load_metronome_events()    加载并启动独立线程
    #   - unload_metronome_events()  停止并清空节拍器事件
    # 两个 API 来控制节拍器。主线程切换音轨/模式时不必再触发节拍器重建。
    #

    def load_metronome_events(
        self,
        events: list,
        bpm: int = 120,
        ticks_per_beat: int = 480,
        total_ticks: int = 0,
        start_offset_ms: float = 0.0,
        start_index: int = 0,
    ) -> None:
        """
        加载节拍器事件序列并启动独立播放线程

        [解耦] 与主事件流完全独立:
          - 不修改 self._events
          - 不调用 rebuild_audio_events
          - 通过独立线程驱动播放

        [对齐] 解决"播放中开启节拍器时首事件卡 30 秒"问题:
          - start_offset_ms: 起始跳过毫秒数（= 主播放位置）
          - start_index:     起始事件索引（跳过已过去的 N 个事件）
          两者配合使用: 线程从 start_index 开始遍历，
          start_perf 也减去 start_offset_ms/1000 使 elapsed_ms 直接对应
          "歌曲时间轴上 start_offset 之后经过的毫秒数"，
          否则首事件会等待 start_offset_ms 才发声（卡顿）。

        参数:
            events:         MidiEvent 对象列表（由 MetronomeGenerator 生成）
            bpm:            播放速度
            ticks_per_beat: 每四分音符 tick 数
            total_ticks:    总 tick 上限；用于"仅节拍器"模式的播完停止判断
            start_offset_ms: 起始跳过毫秒数；用于播放中途开启节拍器时
                            自动跳过早于当前主播放位置的节拍器事件，
                            让节拍器与主播放位置对齐
            start_index:    起始事件索引（已基于 start_offset_ms 算好）
                            线程从该索引开始遍历，避免从头跳过大量事件
        """
        with self._lock:
            # 先停止旧节拍器线程（如果存在）
            self._metronome_stop_flag = True
            self._metronome_pause_event.set()  # 唤醒以让线程退出

        if self._metronome_thread and self._metronome_thread.is_alive():
            self._metronome_thread.join(timeout=1.0)

        with self._lock:
            self._metronome_events = list(events)  # 浅拷贝
            self._metronome_bpm = bpm
            self._metronome_ticks_per_beat = ticks_per_beat
            self._metronome_total_ticks = total_ticks
            self._metronome_start_offset_ms = max(0.0, start_offset_ms)
            # 防御: 限制 start_index 在 [0, len(events)] 范围内
            self._metronome_start_index = max(0, min(start_index, len(events)))
            self._metronome_stop_flag = False
            self._metronome_paused = False
            self._metronome_pause_event.set()

        # 启动独立线程
        if not self._metronome_events:
            return
        if self._metronome_start_index >= len(self._metronome_events):
            # 所有事件都已过去，无需启动线程
            return
        if not self._synth:
            return
        # 关键: start_perf 必须减去 start_offset_ms/1000
        # 这样 elapsed_ms = (now - start_perf) * 1000 在线程启动瞬间
        # 正好等于 start_offset_ms (即"歌曲当前时间")，
        # 后续 wait_ms = target_ms - elapsed_ms 就能直接算对，
        # 避免"首事件等 start_offset_ms 毫秒才发声"的问题
        self._metronome_start_perf = time.perf_counter() - self._metronome_start_offset_ms / 1000.0
        self._metronome_thread = threading.Thread(target=self._metronome_loop, daemon=True)
        self._metronome_thread.start()

    def unload_metronome_events(self) -> None:
        """
        停止节拍器线程并清空节拍器事件列表

        调用场景:
          - 用户禁用节拍器
          - 切换为 MODE_OFF
          - shutdown 时
        """
        self._stop_metronome_thread()
        with self._lock:
            self._metronome_events = []
            self._metronome_total_ticks = 0
            self._metronome_start_offset_ms = 0.0

    def _stop_metronome_thread(self) -> None:
        """内部方法：仅停止线程，保留事件数据（用于后续快速重启）"""
        with self._lock:
            self._metronome_stop_flag = True
            self._metronome_pause_event.set()  # 唤醒阻塞中的线程
        if self._metronome_thread and self._metronome_thread.is_alive():
            self._metronome_thread.join(timeout=1.0)
        with self._lock:
            self._metronome_paused = False
            self._metronome_pause_event.set()

    def set_metronome_paused(self, paused: bool) -> None:
        """
        独立设置节拍器的暂停状态（不影响主播放线程）

        用途: 主线程的 pause()/resume() 会自动联动调用此方法，
              但外部代码也可独立控制节拍器暂停（例如练习时只让节拍器停）。
        """
        with self._lock:
            self._metronome_paused = paused
            if paused:
                self._metronome_pause_event.clear()
            else:
                # 恢复时重置节拍器时间基准（避免暂停期间累积的漂移）
                self._metronome_start_perf = time.perf_counter()
                self._metronome_pause_event.set()

    @property
    def is_metronome_active(self) -> bool:
        """节拍器线程是否正在运行"""
        return (
            self._metronome_thread is not None
            and self._metronome_thread.is_alive()
            and not self._metronome_stop_flag
        )

    def _metronome_loop(self) -> None:
        """
        节拍器独立播放循环（独立线程运行）

        与主 _play_loop 的差异:
          - 独立的时间基准: 使用 _metronome_start_perf + _metronome_bpm 计算
            (start_perf 已减去 start_offset_ms/1000，使 elapsed_ms 直接
             对应"歌曲时间轴上 start_offset 之后经过的毫秒数")
          - 独立的暂停/停止标志: 不与主线程共享
          - 不参与 A/B 循环: 节拍器持续推进，符合真实指挥的 click track 行为
          - 从 start_index 开始遍历: 已通过 bisect 跳过已过去的事件，
            避免"播放中开启节拍器时遍历大量旧事件导致卡 30 秒"问题
          - 共享 _send_event: 节拍器事件仍走同一条 FluidSynth 发送路径
            （节拍器使用专用通道 15，与主通道 0-14 不冲突）
        """
        if not self._metronome_events or not self._synth:
            return

        ms_per_tick = 60000.0 / max(self._metronome_bpm * self._metronome_ticks_per_beat, 1)
        start_offset_ms = self._metronome_start_offset_ms
        start_perf = self._metronome_start_perf  # 已修正（减去 start_offset_ms/1000）
        total_ticks = self._metronome_total_ticks
        total_ms = total_ticks * ms_per_tick if total_ticks > 0 else float('inf')

        # [对齐] 关键修复: 从 start_index 开始迭代（而不是从 0）
        # 配合 start_perf 的偏移修正，节拍器在播放中开启时不会卡顿
        evt_idx = self._metronome_start_index
        num_events = len(self._metronome_events)

        try:
            while evt_idx < num_events:
                # === 检查停止信号 ===
                with self._lock:
                    if self._metronome_stop_flag:
                        break

                # === 检查暂停 ===
                if self._metronome_paused:
                    self._metronome_pause_event.wait()
                    # 恢复后重新校准时间基准
                    start_perf = time.perf_counter() - start_offset_ms / 1000.0
                    with self._lock:
                        if self._metronome_stop_flag:
                            break
                    continue

                evt = self._metronome_events[evt_idx]
                evt_idx += 1

                # === 再次检查停止（事件可能耗时后回到循环顶）===
                with self._lock:
                    if self._metronome_stop_flag:
                        break

                # === 计算事件的目标时间 ===
                target_ms = evt.time * ms_per_tick

                # 总时长上限（仅节拍器模式）
                if target_ms > total_ms:
                    break

                # === 计算等待时间 ===
                # 由于 start_perf 已修正，elapsed_ms 起步即 ≈ start_offset_ms
                # 所以 wait_ms = target_ms - elapsed_ms 直接得到正确等待时长
                elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
                wait_ms = target_ms - elapsed_ms
                if wait_ms > 0 and not self._metronome_wait(wait_ms):
                    # 返回 False 表示收到停止信号
                    break
                # 若 wait_ms <= 0 则立即发送（事件在"过去"时）
                # 这是为了 start_offset 边界或主线程被阻塞时仍能正确发声

                # === 发送事件 ===
                self._send_event(evt)

        except Exception as e:
            print(f"[SynthEngine] 节拍器线程异常: {e}")

    def _metronome_wait(self, wait_ms: float) -> bool:
        """
        节拍器专用等待（支持暂停/停止中断）

        参数:
            wait_ms: 等待毫秒数

        返回:
            True=正常完成等待, False=收到停止信号
        """
        deadline_perf = time.perf_counter() + wait_ms / 1000.0
        while True:
            with self._lock:
                if self._metronome_stop_flag:
                    return False
                paused = self._metronome_paused
            if paused:
                self._metronome_pause_event.wait()
                # 恢复后重新校准 deadline
                deadline_perf = time.perf_counter() + max(
                    (deadline_perf - time.perf_counter()), 0.0
                )
                continue

            remaining = (deadline_perf - time.perf_counter()) * 1000.0
            if remaining <= 0:
                return True
            elif remaining > 5:
                # 短时 sleep 让出 CPU；同时允许被暂停事件唤醒
                self._metronome_pause_event.wait(timeout=min(remaining - 2, 10) / 1000.0)
            else:
                # 接近 deadline，busy-wait
                self._metronome_pause_event.wait(timeout=0.001)

    def silence_all_notes(self) -> None:
        """
        静音所有正在发声的音符(不停止播放线程，仅清除当前声音)

        性能优化（v0.2.4）:
          只关闭实际发声的音符（通过 _active_notes 追踪），
          而非遍历全部 16×128=2048 个可能组合。
          典型场景: 同时发声的音符 < 20个，性能提升100倍以上。

        用途: seek跳转时调用，防止旧位置的音符与新位置的音符同时发声(双重声音)
              与stop()的区别: stop()会完全停止播放并重置状态，
                              silence_all_notes()仅清除声音，播放继续
        """
        if not self._synth:
            return

        # === 快速路径：无活跃音符则直接返回 ===
        with self._lock:
            if not self._active_notes:
                return  # 没有发声的音符，无需操作

            # 复制一份快照（避免在迭代过程中修改字典）
            notes_to_silence = dict(self._active_notes)
            self._active_notes.clear()  # 立即清空追踪表

        # === 仅关闭实际发声的音符 ===
        for ch, pitch in notes_to_silence:
            # 单个音符失败不影响其他音符
            with contextlib.suppress(Exception):
                self._synth.noteoff(ch, pitch)

        # === 复位所有通道的pitch_bend（防止残留弯音影响后续音符）===
        for ch in set(ch for ch, _ in notes_to_silence):
            with contextlib.suppress(Exception):
                self._synth.pitch_bend(ch, 8192)

    # ================================================================
    # [封装] 公共 API：通道音量与循环状态
    # ================================================================
    #
    # 之前 GTPPlayer 直接访问 self._synth / self._lock / self._loop_*
    # 破坏了 SynthEngine 的封装。这些方法/属性作为公共 API 暴露必要的
    # 状态查询与通道控制，外部调用方不应再访问私有成员。
    #

    @property
    def is_synth_available(self) -> bool:
        """
        FluidSynth Synth 实例是否当前可用（已初始化且未被释放）

        替代外部代码的 `hasattr(self, '_synth') and self._synth` 检查。
        """
        return self._synth is not None

    def set_channel_volume(self, channel: int, midi_volume: int) -> bool:
        """
        设置单个 MIDI 通道的音量 (MIDI CC#7)

        参数:
            channel:      MIDI 通道号 (0-15)
            midi_volume:  音量值 (0-127)，其中 127=最大, 0=静音, 100=默认

        返回:
            True:  消息已发送
            False: Synth 未初始化或通道号非法

        说明:
            实时生效，无需重启播放。用于单轨/全轨模式下控制各通道音量。
        """
        if not self._synth:
            return False
        if channel < 0 or channel > 15:
            return False
        midi_volume = max(0, min(127, int(midi_volume)))
        try:
            self._synth.cc(channel, 7, midi_volume)
            return True
        except Exception:
            return False

    def set_master_volume(self, midi_volume: int) -> bool:
        """
        设置主音量（通过同时调整全部 16 个 MIDI 通道的 CC#7 实现）

        参数:
            midi_volume: 音量值 (0-127)

        返回:
            True:  所有通道均设置成功
            False: Synth 未初始化

        原理:
            FluidSynth 的 gain 属性在初始化时设置后运行时不可热修改。
            改用对全部 16 个通道发送 CC#7 来实现"运行时主音量"控制。
        """
        if not self._synth:
            return False
        midi_volume = max(0, min(127, int(midi_volume)))
        try:
            for ch in range(16):
                self._synth.cc(ch, 7, midi_volume)
            return True
        except Exception:
            return False

    @property
    def loop_state(self) -> dict:
        """
        获取当前 A/B 区域循环状态（公共只读）

        返回:
            dict: {
                'enabled':      bool,   # 是否启用循环
                'start_ms':     float,  # 循环起始时间（毫秒）
                'end_ms':       float,  # 循环结束时间（毫秒）
            }
            若循环未启用，则 start_ms / end_ms 为 0.0

        说明:
            替代外部直接访问 self._lock / self._loop_enabled / self._loop_* 的模式。
        """
        with self._lock:
            if self._loop_enabled:
                return {
                    'enabled': True,
                    'start_ms': self._loop_start_ms,
                    'end_ms': self._loop_end_ms,
                }
        return {'enabled': False, 'start_ms': 0.0, 'end_ms': 0.0}

    @property
    def is_loop_enabled(self) -> bool:
        """是否启用 A/B 区域循环（公共只读）"""
        with self._lock:
            return self._loop_enabled

    @property
    def loop_time_range(self) -> tuple:
        """
        获取当前 A/B 循环的时间范围（毫秒）

        返回:
            (loop_start_ms, loop_end_ms) 元组
            循环未启用时返回 (0.0, 0.0)

        用途: UI 层判断点击的小节是否在循环区间内
        """
        with self._lock:
            if self._loop_enabled:
                return (self._loop_start_ms, self._loop_end_ms)
        return (0.0, 0.0)

    def seek(self, time_ms: float) -> None:
        """
        跳转到指定时间位置(毫秒)

        原理: 停止当前播放 → 静音所有发声音符 → 过滤出时间≥目标位置的事件
              → 从目标位置开始重新播放。
              已经过去的 note_on 会被忽略（快速跳转时不回溯发声）。

        性能优化（v0.2.4）:
          - 使用防抖机制避免快速连续点击导致频繁重建播放线程
          - silence_all_notes() 只关闭实际发声的音符（非2048次全扫）
          - 修复线程竞态条件：确保静音操作在锁保护下完成

        参数:
            time_ms: 目标时间位置(毫秒)

        注意:
          此方法是线程安全的，可从UI线程安全调用。
          内置50ms防抖间隔，快速连续点击只执行最后一次seek。
        """
        # === 防抖检查 ===
        current_time = time.perf_counter()
        time_since_last_seek = (current_time - self._last_seek_time) * 1000.0

        if time_since_last_seek < self._seek_debounce_ms:
            # 距离上次seek太近，记录待执行的目标时间后返回
            # 播放线程会在下次循环时检查并执行
            with self._lock:
                self._pending_seek_time = max(0, time_ms)
            return

        self._last_seek_time = current_time

        with self._lock:
            was_playing = self._is_playing and not self._is_paused

            if was_playing:
                # === Step 1: 先设置停止信号（阻止新事件发送）===
                # 重要：必须在静音之前设置，防止竞态条件
                self._stop_flag = True
                self._pause_event.set()  # 解除暂停阻塞（如果处于暂停状态）

                # === Step 2: 在锁保护下执行静音（修复竞态条件）===
                # 旧代码在这里释放了锁导致竞态，现在保持锁持有状态
                # 由于已设置_stop_flag，_send_event会检查该标志后跳过
                try:
                    # 注意：silence_all_notes内部有自己的锁逻辑
                    # 这里临时释放以避免死锁（silence_all_notes会获取_lock）
                    self._lock.release()
                    self.silence_all_notes()
                finally:
                    self._lock.acquire()

            # === Step 3: 更新时间位置 ===
            self._current_time_ms = max(0, time_ms)
            self._paused_duration = 0.0
            # 同步更新初始偏移，使 current_time_ms 属性立即返回正确值
            self._initial_time_offset = self._current_time_ms

            # 清除待执行的seek（已被执行）
            self._pending_seek_time = -1.0

            if was_playing:
                # === Step 4: 等待旧线程完全结束 ===
                self._stop_flag = False  # 重置停止标志（供新线程使用）
                self._is_playing = True
                self._is_paused = False
                self._pause_event.set()

                if self._play_thread and self._play_thread.is_alive():
                    self._play_thread.join(timeout=1.0)

                # === Step 4.5: 预置 _start_time（修复短循环seek竞态条件）===
                # [v0.2.5 关键修复] 必须在新线程启动前设置 _start_time
                # 原因: current_time_ms 属性依赖 (_start_time, _initial_time_offset) 计算
                #       若 seek() 返回后新线程尚未执行到 _start_time = perf_counter()
                #       则 UI 线程读到的 current_time_ms = (now - 旧_start_time) + 新偏移 → 巨大值
                #       导致 A/B 短循环在 seek 后立即误判为"已过B点"→ 再次 seek → 死循环卡在A点
                # 修复: 在锁保护下预置 _start_time，新线程启动后会用几乎相同的值覆盖，无副作用
                self._start_time = time.perf_counter()

                # === Step 5: 启动新的播放线程 ===
                self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
                self._play_thread.start()

    def set_note_callback(self, callback: Callable) -> None:
        """
        设置音符触发回调函数(用于视觉高亮同步)

        参数:
            callback: 回调函数签名 callback(midi_pitch, velocity, time_ms)
                      当有 note_on 事件被触发时调用此函数，
                      用于通知 UI 层更新当前高亮的音符位置
        """
        self._on_note_callback = callback

    def _play_loop(self) -> None:
        """
        播放线程主循环(内部方法，勿直接调用)

        核心逻辑:
          1. 计算每 tick 对应的毫秒数: ms_per_tick = 60000 / (BPM × ticks_per_beat)
          2. 遍历所有事件，对每个事件:
             a. 计算目标时间 = event.tick × ms_per_tick
             b. 如果目标时间 < seek起点，跳过
             c. 否则 sleep 到达目标时间(考虑暂停)
             d. 发送 MIDI 事件到合成器
             e. 触发回调(用于视觉同步)
          3. 所有事件处理完毕 → 检查循环标志 → 若启用则自动回到loop_start继续

        [v0.2.6] 内置A/B循环: 当_loop_enabled=True时，播放完所有事件(或到达loop_end)后
          自动静音→重置时间基准→重新遍历事件列表(早于loop_start的事件被time检查跳过)。
          整个循环过程在音频线程内完成，UI层通过current_time_ms属性读取到的时间自然在[loop_start, loop_end]范围内往复。
        """
        if not self._events:
            with self._lock:
                self._is_playing = False
            return

        # === 时间基准计算 ===
        ms_per_tick = 60000.0 / (self._bpm * self._ticks_per_beat)
        start_offset = self._current_time_ms  # 跳转后的起始偏移(毫秒)

        # 保存初始时间偏移，供 current_time_ms 属性使用
        # 这样 seek 到 5000ms 后开始播放，current_time_ms 会返回 5000+elapsed 而非仅 elapsed
        self._initial_time_offset = start_offset

        # 记录实际开始播放的系统时间
        with self._lock:
            self._start_time = time.perf_counter()

        try:
            # [v0.2.6] 用索引循环替代for循环，支持循环重启时重置索引
            evt_idx = 0
            num_events = len(self._events)

            while True:
                # === 检查是否需要循环重启 ===
                # 条件: 已遍历完所有事件 + 循环已启用 + 未收到停止信号
                if evt_idx >= num_events:
                    with self._lock:
                        should_loop = self._loop_enabled and not self._stop_flag

                    if should_loop:
                        # === 循环重启: 回到loop_start位置 ===
                        self.silence_all_notes()  # 静音当前发声的音符

                        with self._lock:
                            # 重置时间基准为循环起点
                            start_offset = self._loop_start_ms
                            self._initial_time_offset = start_offset
                            self._current_time_ms = start_offset
                            self._start_time = time.perf_counter()

                        # 重置事件索引，从头遍历(早于loop_start的会被target_time_ms检查自动跳过)
                        evt_idx = 0
                        continue
                    else:
                        # 无循环 → 正常结束播放
                        break

                evt = self._events[evt_idx]
                evt_idx += 1

                # === 检查停止信号 ===
                with self._lock:
                    if self._stop_flag:
                        break

                    # === 检查待执行的防抖seek ===
                    # 如果在防抖期间有新的seek请求，立即跳转到目标位置
                    if self._pending_seek_time >= 0:
                        target_seek = self._pending_seek_time
                        self._pending_seek_time = -1.0  # 清除待执行标记

                        # 更新起始偏移（相当于即时seek）
                        start_offset = target_seek
                        self._initial_time_offset = target_seek
                        self._current_time_ms = target_seek
                        self._start_time = time.perf_counter()

                        # 静音当前音符（防止双重声音）
                        # 临时释放锁以避免死锁
                        self._lock.release()
                        try:
                            self.silence_all_notes()
                        finally:
                            self._lock.acquire()

                        # 跳过比新起点更早的事件
                        if evt.time * ms_per_tick < target_seek:
                            if evt.type == "note_off" and self._synth:
                                self._synth.noteoff(evt.channel, evt.pitch)
                            continue

                # === 检查暂停 ===
                self._pause_event.wait()  # 暂停时此处阻塞

                # 再次检查停止(可能在暂停期间收到停止信号)
                with self._lock:
                    if self._stop_flag:
                        break

                # === 计算事件的绝对时间(毫秒) ===
                target_time_ms = evt.time * ms_per_tick

                # [v0.2.6 增强] A/B循环边界检查: 事件时间超过loop_end时立即触发循环重启
                # 原因: 如果只在所有事件播完后才循环(evt_idx>=num_events)，短循环(如小节0-2)
                #       会先播完整首谱(240秒)才回到A点，用户感知为"没有跳回A点"
                # 方案: 当事件时间>=loop_end时，视为"到达B点"，立即触发循环重启
                if (
                    self._loop_enabled
                    and self._loop_end_ms > 0
                    and target_time_ms >= self._loop_end_ms
                ):
                    # 到达B点! 触发循环重启
                    self.silence_all_notes()

                    with self._lock:
                        if self._stop_flag:
                            break
                        # 重置时间基准为循环起点(A点)
                        start_offset = self._loop_start_ms
                        self._initial_time_offset = start_offset
                        self._current_time_ms = start_offset
                        self._start_time = time.perf_counter()

                    # 重置事件索引，从头遍历(早于loop_start的会被自动跳过)
                    evt_idx = 0
                    continue  # 跳过当前事件，从第1个事件重新开始

                # 跳过已经过去的事件(seek时)
                if target_time_ms < start_offset:
                    # 对于过去的 note_off，仍然需要执行以防止长音卡住
                    if evt.type == "note_off" and self._synth:
                        self._synth.noteoff(evt.channel, evt.pitch)
                    continue

                # === 等待到达目标时间 ===
                relative_target = target_time_ms - start_offset
                self._wait_until(relative_target)

                # === 再次检查状态(等待期间可能暂停/停止) ===
                with self._lock:
                    if self._stop_flag:
                        break

                # === 发送 MIDI 事件到合成器 ===
                self._send_event(evt)

                # === 触发回调(note_on 时通知视觉层) ===
                if evt.type == "note_on" and self._on_note_callback:
                    # 回调异常不影响播放
                    with contextlib.suppress(Exception):
                        self._on_note_callback(evt.pitch, evt.velocity, target_time_ms)

            # === 播放结束 ===
            with self._lock:
                if not self._stop_flag:
                    self._is_playing = False
                    self._current_time_ms = 0  # 播放完毕重置

        except Exception as e:
            print(f"[SynthEngine] 播放循环异常: {e}")
            with self._lock:
                self._is_playing = False

    def _wait_until(self, target_relative_ms: float) -> None:
        """
        精确等待到指定的相对时间位置(毫秒)

        使用自适应 sleep 策略保证时间精度:
          1. 先 sleep 到目标时间前 5ms
          2. 然后 busy-wait(spinning) 到精确时刻
          3. 支持 pause/resume 中断

        参数:
            target_relative_ms: 相对于播放开始的毫秒时间

        [v1.3.1] 暂停恢复后 _start_time 由 resume() 调整，
                  此处不再重置 _start_time（否则会覆盖 resume 的修正，
                  导致 current_time_ms 跳回 _initial_time_offset）。
        """
        while True:
            # 检查暂停/停止
            if self._stop_flag:
                return
            if self._is_paused:
                self._pause_event.wait()
                # [v1.3.1] 不再重置 _start_time：
                # 暂停期间累计的时间已经反映在 elapsed = (now - _start_time)*1000 中
                # （resume() 中已将 _start_time 设为合适的值，使 elapsed 从暂停位置继续）
                continue

            # 计算已播放的时间
            with self._lock:
                elapsed = (time.perf_counter() - self._start_time) * 1000.0

            remaining = target_relative_ms - elapsed

            if remaining <= 0:
                return  # 已到达目标时间
            elif remaining > 5:
                # 还有较多时间，先 sleep 一小段
                sleep_time = min(remaining - 2, 10)  # 最多睡10ms
                self._pause_event.wait(timeout=sleep_time / 1000.0)
            else:
                # 接近目标时间，busy-wait 提高精度
                self._pause_event.wait(timeout=0.001)  # 1ms 短暂 sleep 让出CPU

    def _send_event(self, event: Any) -> None:
        """
        发送单个 MIDI 事件到 FluidSynth 合成器

        参数:
            event: MidiEvent 对象

        优化（v0.2.4）:
          自动维护 _active_notes 追踪表，
          使 silence_all_notes() 能快速定位并关闭实际发声的音符。
        """
        if not self._synth:
            return

        try:
            if event.type == "note_on":
                # note_on: 通道 + 音高 + 力度
                self._synth.noteon(event.channel, event.pitch, event.velocity)

                # === 追踪活跃音符（用于silence_all_notes优化）===
                with self._lock:
                    self._active_notes[(event.channel, event.pitch)] = True

            elif event.type == "note_off":
                # note_off: 通道 + 音高(velocity=0)
                self._synth.noteoff(event.channel, event.pitch)

                # === 从活跃音符追踪表中移除 ===
                with self._lock:
                    self._active_notes.pop((event.channel, event.pitch), None)

            elif event.type == "pitch_bend":
                # pitch_bend: 通道 + 弯音值(0-16383, 中值8192=无弯音)
                # 用于实现推弦(bend)、颤音(vibrato)等效果
                self._synth.pitch_bend(event.channel, event.pitch)

            elif event.type == "control_change":
                # 控制变化: CC# + 值 (0-127)
                # 用于 Bank Select (CC#0/CC#32) 等音色库切换
                self._synth.cc(event.channel, event.pitch, event.velocity)

            elif event.type == "program_change":
                # 程序变化: 切换乐器音色
                self._synth.program_change(event.channel, event.pitch)

            elif event.type == "tempo":
                # tempo 变化: 更新内部 BPM 参考
                # (实际变速能力需要更复杂的实现，这里仅记录)
                pass

        except Exception as e:
            print(f"[SynthEngine] 发送事件失败: {e}")

    def shutdown(self) -> None:
        """
        关闭合成引擎并释放所有资源

        原理: 停止播放 → 卸载 SoundFont → 关闭音频驱动 → 销毁合成器实例
        此方法应在程序退出时调用以确保资源正确释放。
        """
        self.stop()

        # [解耦] 同步关闭节拍器线程
        self.unload_metronome_events()

        if self._synth:
            try:
                if self._sfid >= 0:
                    self._synth.sfunload(self._sfid)
                    self._sfid = -1
                self._synth.delete()
            except Exception:
                pass
            finally:
                self._synth = None
                self._audio_driver = None

    def __del__(self) -> None:
        """析构函数：确保资源释放"""
        with contextlib.suppress(Exception):
            self.shutdown()
