"""
============================================================
文件名: tab_renderer.py
功能描述: 六线谱渲染引擎 - 使用 QPainter 将 GTP 数据模型绘制为
         可视化的吉他六线谱图像（QPixmap），支持多页输出

原理:
  1. 接收 GTPTrack 数据和布局计算结果
  2. 使用 QPainter 在 QPixmap 上绑制:
     - 六线谱线(6条横线，代表6根弦)
     - 品格数字(在对应弦线的正确位置显示品格数)
     - 小节线(分隔各小节)
     - 符干/符尾(表示音符时值，含附点标记和优化休止符符号)
     - 技巧标记(图形化+文字混合渲染):
       * 泛音菱形、滑音连线、推弦弧线箭头、颤音波浪线
       * P.M./Let Ring虚线延长线
       * 击弦/勾弦/滑音等缩写文字标签
     - 调号/拍号标记(每行系统开头显示非标准拍号和升降号)
  3. 输出多页 QPixmap 列表，供 DisplayWidget 直接使用

核心方法:
  - render_from_file(): 从GTP文件路径直接渲染（便捷入口）
  - render(): 主渲染入口，返回多页QPixmap列表
  - _draw_system(): 绘制一行系统(弦线 + 调拍号 + 所有小节)
  - _draw_measure(): 绘制单个小节(小节线 + 段落标记 + 所有拍 + P.M.虚线)
  - _draw_beat(): 绘制单个拍(品格数字 + 技巧标记 + 符干符尾, 含休止符过滤)
  - _draw_technique_marks(): 技巧标记分发器，按类型调用对应绘制方法
  - _draw_dashed_extension_line(): P.M./Let Ring GP5格式虚线(strict参数控制断开策略)

渲染模式扩展接口 (v0.4.0 新增):
  - render_mode: RenderMode 枚举，控制渲染哪些谱表
  - _draw_standard_notation(): 五线谱渲染钩子(预留，子类重写)
  - _draw_numbered_notation(): 简谱渲染钩子(预留，子类重写)
  - _draw_slash_notation():    斜线谱渲染钩子(预留，子类重写)
  当前版本仅实现 TAB_ONLY 模式，其他模式需通过子类扩展

依赖库:
  - PyQt5 (QPainter, QPixmap, QFont, QPen, QColor, QPainterPath等)
  - 内部依赖: gtp_engine.models.*, layout_engine.*, utils.constants

创建日期: 2026-06-06
最后更新: 2026-06-28 (v0.4.0: 新增 RenderMode 渲染模式扩展接口)
============================================================
"""

import copy
from typing import Any, Optional

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
)

from ..models.beat import GTPBeat
from ..models.measure import GTPMeasure
from ..models.note import GTPNote
from ..models.song import GTPSong
from ..models.track import GTPTrack
from ..utils.constants import (
    DURATION_RATIO,
    TECHNIQUE_ABBREVIATION,
    NoteDuration,
    RenderConfig,
    RenderMode,  # v0.4.0新增: 渲染模式枚举
    TechniqueType,
    get_string_name,
)
from .layout_engine import BeatLayout, MeasureLayout, PageLayout, SystemLayout, TabLayoutEngine


class TabRenderer:
    """
    六线谱渲染引擎

    功能: 将解析后的GTP乐谱数据渲染为可视化的六线谱图像

    主题支持（v0.2.4新增）:
      - 内置黑白(light)和深色(dark)两套预设主题
      - 支持运行时动态切换主题
      - 支持自定义扩展新主题

    用法:
        renderer = TabRenderer()
        pixmaps = renderer.render(song, track_index=0)
        # pixmaps: list[QPixmap] - 每页一张图片

        # 切换到黑白主题
        renderer.set_theme("light")
        pixmaps_light = renderer.render(song, track_index=0)

        # 或使用自定义配置
        from ApolloTab.utils.constants import ThemeConfig
        custom_theme = ThemeConfig(colors={"COLOR_BG": "#FFFFCC", ...})
        renderer.set_theme(custom_theme)
    """

    def __init__(self, config: RenderConfig | None = None):
        self.cfg = config or RenderConfig()
        self._layout_engine = TabLayoutEngine(self.cfg)
        # 上次渲染的布局数据(由render()自动填充)
        # 类型: list[PageLayout], 每页包含SystemLayout→MeasureLayout→BeatLayout坐标
        self.last_layouts: list = []
        # 渲染模式(v0.4.0新增) - 控制渲染哪些谱表
        # 调整效果: 设为 RenderMode.TAB_ONLY 仅渲染六线谱(当前唯一支持)
        #           其他模式需通过子类重写 _draw_standard_notation 等钩子方法实现
        self.render_mode: RenderMode = RenderMode.TAB_ONLY
        # [a11y/cvd] CVD 模拟状态 (v1.6.0 新增)
        # _base_theme: 纯净主题 (无 CVD 变换), 用于 cvd='none' 时还原 + set_theme 切换时重新叠加 CVD
        # _cvd_type:   当前 CVD 类型 ('none' / 'protanopia' / ...), 'none' 表示不变换
        # 所有颜色读取都走 self.cfg.theme.COLOR_*, set_cvd/set_theme 只需替换 cfg.theme 为
        # (可能经 CVD 变换的) ThemeConfig, 30+ 读点自动生效, 无需逐点改.
        self._base_theme = self.cfg.theme
        self._cvd_type: str = "none"

    def set_theme(self, theme: Any) -> None:
        """
        切换渲染主题（核心接口）

        功能:
          动态切换六线谱的配色方案，无需重新创建渲染器实例。
          切换后需要重新调用 render() 才能生成使用新主题的图像。

        参数:
            theme: 可以是以下三种形式之一:
              1. 字符串: 预设主题名称 ("light" | "dark")
              2. ThemeConfig 实例: 自定义或预定义的主题对象

        使用示例:
            # 方式1: 通过名称字符串切换（推荐）
            renderer.set_theme("light")   # 黑白主题
            renderer.set_theme("dark")    # 深色主题

            # 方式2: 通过 ThemeConfig 实例切换
            from ApolloTab.utils.constants import ThemeConfig
            my_theme = ThemeConfig.get_theme("light")
            renderer.set_theme(my_theme)

            # 方式3: 自定义主题
            custom = ThemeConfig(
                colors={
                    "COLOR_BG": "#FFFDE7",      # 米黄色背景
                    "COLOR_TEXT": "#212121",     # 近黑色文字
                    # ... 其他颜色参数
                },
                theme_name="sepia"
            )
            renderer.set_theme(custom)

        注意:
          - 此方法仅修改配置，不会自动重新渲染已有图像
          - 调用后需重新执行 render() 才能看到效果
          - 布局引擎会同步更新主题（确保一致性）

        异常:
          ValueError: 当传入未知的主题名称时抛出
          TypeError: 当 theme 参数类型不支持时抛出

        性能:
          切换主题是 O(1) 操作（仅替换引用），
          不会触发任何计算或 I/O 操作。
        """
        # 导入 ThemeConfig（延迟导入避免循环依赖）
        from ..utils.constants import ThemeConfig

        # === 根据 input 类型处理 ===
        if isinstance(theme, str):
            # 字符串：从预设主题中获取
            new_theme = ThemeConfig.get_theme(theme)

        elif isinstance(theme, ThemeConfig):
            # ThemeConfig 实例：直接使用
            new_theme = theme

        else:
            raise TypeError(
                f"不支持的 theme 类型: {type(theme).__name__}\n"
                f"支持的类型:\n"
                f"  1. str: 预设主题名称 (可用: {', '.join(ThemeConfig.list_themes())})\n"
                f"  2. ThemeConfig: 主题配置实例\n\n"
                f"示例:\n"
                f"  renderer.set_theme('light')\n"
                f"  renderer.set_theme(ThemeConfig.get_theme('dark'))"
            )

        # === 应用新主题到配置 ===
        # [a11y/cvd] 先记住纯净主题 (base), 再按当前 _cvd_type 决定 cfg.theme 用纯净还是 CVD 变换副本.
        # 这样 set_theme 与 set_cvd 可任意顺序叠加: 切主题保留 CVD, 切 CVD 作用于新主题.
        self._base_theme = new_theme
        applied = self._build_cvd_theme(new_theme, self._cvd_type)
        self.cfg.theme = applied

        # === 同步更新布局引擎的主题（保持一致性）===
        self._layout_engine.cfg.theme = applied

    def set_cvd(self, cvd_type: str) -> None:
        """
        设置 CVD (色觉缺陷) 模拟类型 (v1.6.0 新增, a11y).

        功能:
          对当前主题颜色套 CVD 矩阵变换, 让设计师/开发者看到 CVD 用户视角的谱面.
          切换后需要重新调用 render() 才能生成使用新配色的图像 (与 set_theme 语义一致).

        参数:
            cvd_type: CVD 类型标识, 取值:
              "none"          - 不变换 (还原主题原色)
              "protanopia"    - 红色盲
              "deuteranopia"  - 绿色盲
              "tritanopia"    - 蓝色盲
              "protanomaly"   - 红色弱
              "deuteranomaly" - 绿色弱
              "tritanomaly"   - 蓝色弱

        叠加顺序:
          主题色 (ThemeConfig) → CVD 矩阵变换 → 最终渲染色.
          cvd_type='none' 时还原 _base_theme 的原色.

        使用示例:
            renderer.set_cvd("protanopia")   # 模拟红色盲视角
            pixmaps = renderer.render(song)  # 重新渲染才生效
            renderer.set_cvd("none")         # 还原

        注意:
          - 此方法仅修改配置, 不会自动重新渲染已有图像 (同 set_theme).
          - 与 set_theme 可任意顺序调用: set_theme 后 CVD 仍生效, set_cvd 后切主题会重新叠加 CVD.
          - 无效 cvd_type 静默忽略 (记日志), 不抛异常, 不阻塞渲染.

        性能:
          O(11) — 对当前主题的 11 个 COLOR_* 键各做一次矩阵乘法, 无 I/O.
        """
        from ..utils.cvd import is_valid_cvd

        if not is_valid_cvd(cvd_type):
            import logging
            logging.getLogger(__name__).warning(
                "[cvd] 无效的 CVD 类型: %s, 保持原值 %s", cvd_type, self._cvd_type
            )
            return
        self._cvd_type = cvd_type
        applied = self._build_cvd_theme(self._base_theme, cvd_type)
        self.cfg.theme = applied
        # 同步布局引擎主题 (保持与 set_theme 一致, 虽然 layout_engine 不读颜色)
        self._layout_engine.cfg.theme = applied

    def _build_cvd_theme(self, base: Any, cvd_type: str) -> Any:
        """
        根据 base 主题 + cvd_type 生成最终 ThemeConfig.

        - cvd_type='none' 或未知: 返回 base 本身 (零拷贝)
        - 否则: 对 base._colors 的每个 COLOR_* 值套 apply_cvd_to_hex,
                用 ThemeConfig(colors=new_colors, theme_name=base.name) 构造新实例.

        参数:
            base:     纯净 ThemeConfig (无 CVD)
            cvd_type: CVD 类型标识

        返回:
            最终用于 cfg.theme 的 ThemeConfig (none 时即 base)
        """
        if cvd_type == "none" or cvd_type is None:
            return base
        from ..utils.cvd import apply_cvd_to_hex
        from ..utils.constants import ThemeConfig

        # base._colors 是 ThemeConfig.__init__ 设置的真实属性 (dict[str, hex]).
        # 直接访问: 若 base 不是 ThemeConfig (缺少 _colors), 应该 fail-fast 而非静默吞错.
        new_colors = {
            k: apply_cvd_to_hex(v, cvd_type) for k, v in base._colors.items()
        }
        return ThemeConfig(colors=new_colors, theme_name=base.name)

    @property
    def current_cvd_type(self) -> str:
        """
        获取当前激活的 CVD 类型标识 (供测试/app 查询).

        返回:
            'none' / 'protanopia' / 'deuteranopia' / ...
        """
        return self._cvd_type

    @property
    def current_theme_name(self) -> str:
        """
        获取当前主题名称

        返回:
            当前使用的主题标识字符串，如 "light", "dark", "custom" 等
        """
        return self.cfg.theme.name

    def get_available_themes(self) -> list[str]:
        """
        获取所有可用的预设主题名称列表

        返回:
            主题名称列表，如 ["light", "dark"]

        使用示例:
            >>> themes = renderer.get_available_themes()
            >>> print(f"可用主题: {themes}")
            可用主题: ['light', 'dark']
        """
        from ..utils.constants import ThemeConfig

        return ThemeConfig.list_themes()

    def render(
        self,
        song: GTPSong,
        track_index: int = 0,
        page_width: int | None = None,
        page_height: int | None = None,
    ) -> list[QPixmap]:
        """
        渲染指定音轨的完整六线谱

        参数:
            song:          完整歌曲数据
            track_index:   要渲染的音轨索引（默认第1条）
            page_width:    每页宽度(px)，None则用配置默认值
            page_height:   每页高度(px)，None则用配置默认值

        返回:
            QPixmap列表，每元素对应一页乐谱图像

        注意:
            布局数据会同时存储在 self.last_layouts 属性中，
            可用于播放光标(Playhead)等需要坐标信息的后续功能。
        """
        # 获取目标音轨
        if track_index >= len(song.tracks):
            track_index = 0
        track = song.tracks[track_index]

        pw = page_width or self.cfg.PAGE_WIDTH_PX
        ph = page_height or self.cfg.PAGE_HEIGHT_PX

        # 执行布局计算
        pages_layout = self._layout_engine.layout(track, pw, ph)

        # 存储布局数据供外部使用(播放光标等功能依赖此数据)
        self.last_layouts = pages_layout

        # 为每页生成图像
        pixmaps: list[QPixmap] = []
        for page_layout in pages_layout:
            pixmap = self._render_page(song, track, page_layout, pw, ph)
            pixmaps.append(pixmap)

        return pixmaps

    def render_from_file(
        self,
        file_path: str,
        track_index: int = 0,
        page_width: int | None = None,
        page_height: int | None = None,
    ) -> list[QPixmap]:
        """
        便捷方法：从文件路径直接渲染（解析+渲染一步完成）

        参数:
            file_path:    .gp3/.gp4/.gp5/.gpx/.gp 文件路径
                          v0.4.0新增: 支持 .gp (GP7/GP8) 文件
            track_index:  要渲染的音轨索引（默认0=第一条）
            page_width:   每页宽度(px)，None则用配置默认值
            page_height:  每页高度(px)，None则用配置默认值

        返回:
            QPixmap列表，每元素对应一页乐谱图像
        """
        from ..parser import parse_score

        song = parse_score(file_path)
        return self.render(
            song, track_index=track_index, page_width=page_width, page_height=page_height
        )

    # ============================================================
    # 渲染模式扩展钩子方法 (v0.4.0 新增)
    # ============================================================
    # 以下方法为预留接口，当前版本为空实现。
    # 子类可通过重写这些方法实现五线谱/简谱/斜线谱的渲染。
    # 主渲染流程会在 _draw_system 中根据 self.render_mode 调用对应钩子。

    def _draw_standard_notation(
        self, painter: QPainter, track: GTPTrack, system: SystemLayout
    ) -> None:
        """
        五线谱渲染钩子（预留接口，v0.4.0 新增）

        功能:
          在六线谱上方/下方绘制五线谱(标准乐谱)。
          当前版本为空实现，需通过子类重写来启用。

        参数:
            painter: QPainter 绘图对象
            track:   GTPTrack 音轨数据
            system:  SystemLayout 系统布局(含坐标信息)

        扩展示例:
            class ExtendedRenderer(TabRenderer):
                def _draw_standard_notation(self, painter, track, system):
                    # 在 system.y_tab_top 上方绘制五线谱
                    staff_top = system.y_tab_top - 60
                    for i in range(5):
                        y = staff_top + i * 8
                        painter.drawLine(system.x_start, y, system.x_end, y)
                    # ... 绘制音符头/符干/符尾等
        """
        pass

    def _draw_numbered_notation(
        self, painter: QPainter, track: GTPTrack, system: SystemLayout
    ) -> None:
        """
        简谱渲染钩子（预留接口，v0.4.0 新增 - GP8 新功能）

        功能:
          在六线谱上方/下方绘制简谱(数字记谱法)。
          当前版本为空实现，需通过子类重写来启用。

        参数:
            painter: QPainter 绘图对象
            track:   GTPTrack 音轨数据
            system:  SystemLayout 系统布局(含坐标信息)

        说明:
          简谱使用数字 1-7 表示 do-re-mi-fa-sol-la-ti，
          GP8 引入了简谱显示功能，本程序预留接口以便未来扩展。
        """
        pass

    def _draw_slash_notation(
        self, painter: QPainter, track: GTPTrack, system: SystemLayout
    ) -> None:
        """
        斜线谱渲染钩子（预留接口，v0.4.0 新增）

        功能:
          在六线谱上方/下方绘制斜线谱(节奏记谱法)。
          当前版本为空实现，需通过子类重写来启用。

        参数:
            painter: QPainter 绘图对象
            track:   GTPTrack 音轨数据
            system:  SystemLayout 系统布局(含坐标信息)

        说明:
          斜线谱用斜线(/)表示节拍，常用于节奏吉他和鼓谱。
        """
        pass

    def _render_page(
        self, song: GTPSong, track: GTPTrack, page: PageLayout, width: int, height: int
    ) -> QPixmap:
        """渲染单页乐谱图像"""
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(self.cfg.theme.COLOR_BG))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        try:
            # 1. 绘制页面头部信息区（仅第1页显示标题/调弦/BPM，后续页省略以节省空间）
            if page.page_number == 1:
                self._draw_header(painter, song, track, width)

            # 2. 绘制每行系统(六线谱行)
            #    第1页额外留白：第一行系统下移，给标题区域更多呼吸空间
            for sys_idx, system in enumerate(page.systems):
                if page.page_number == 1 and sys_idx == 0:
                    # 第一页第一行：整体向下偏移15px留白
                    # 性能优化: 用copy替代deepcopy(仅修改4个int属性，无需深拷贝嵌套的measures列表)
                    shifted = copy.copy(system)
                    shifted.y_top += 15
                    shifted.y_tab_top += 15
                    shifted.y_tab_bottom += 15
                    shifted.y_bottom += 15
                    self._draw_system(painter, track, shifted)
                else:
                    self._draw_system(painter, track, system)

            # 3. 绘制页码
            self._draw_page_number(painter, f"第 {page.page_number} 页", width, height)

        finally:
            painter.end()

        return pixmap

    # ============================================================
    # 头部信息区绘制
    # ============================================================

    def _draw_header(
        self, painter: QPainter, song: GTPSong, track: GTPTrack, page_width: int
    ) -> None:
        """绘制页面顶部信息区（标题、轨道名、调弦、BPM、调号拍号）"""
        y = 15

        # 歌曲标题 + 右侧调号拍号
        painter.setPen(QColor(self.cfg.theme.COLOR_TEXT))
        title_font = QFont(self.cfg.NOTE_FONT_FAMILY, self.cfg.TRACK_NAME_FONT_SIZE, QFont.Bold)
        painter.setFont(title_font)
        title_text = song.title or "Untitled"
        if len(title_text) > 50:
            title_text = title_text[:47] + "..."

        # 标题居中绘制（页首中间位置）
        painter.drawText(QRect(10, y, page_width - 200, 30), Qt.AlignCenter, title_text)

        # 调号拍号绘制在标题右侧: 格式为 "1=C (4/4)"
        kt_str = self._format_key_time_signature(song)
        if kt_str:
            kt_font = QFont(self.cfg.NOTE_FONT_FAMILY, self.cfg.INFO_FONT_SIZE)
            painter.setFont(kt_font)
            painter.setPen(QColor("#888888"))
            painter.drawText(
                QRect(page_width - 180, y, 170, 25), int(Qt.AlignRight | Qt.AlignVCenter), kt_str
            )

        y += 35  # 标题行与信息行之间的行距（增大以增加呼吸空间）

        # 第二行：轨道名 | 调弦(简化显示) | BPM
        info_font = QFont(self.cfg.NOTE_FONT_FAMILY, self.cfg.INFO_FONT_SIZE)
        painter.setFont(info_font)
        painter.setPen(QColor(self.cfg.theme.COLOR_TRACK_NAME))

        # 使用简化调弦名称（标准/降半调/Drop D等），不再显示原始MIDI音高
        tuning_str = track.get_tuning_name()
        info_line = f"{track.name}  |  {tuning_str}  |  {song.tempo} BPM"
        painter.drawText(QRect(10, y, page_width - 20, 20), Qt.AlignLeft, info_line)

    @staticmethod
    def _format_key_time_signature(song: GTPSong) -> str:
        """
        格式化调号和拍号为 "1=X (A/B)" 字符串，用于标题右侧显示

        原理:
          - 调号(key_signature) 是 MIDI 音高值(0=C, 1=G, 2=D, ... -1=F, -2=Bb 等)
            通过查表转换为调性名称
          - 拍号(time_signature) 是 (分子, 分母) 元组，直接格式化为分数形式

        参数:
            song: GTPSong 对象（含 key_signature 和第一个小节的 time_signature）

        返回:
            格式化字符串，如 "1=C (4/4)"、"1=G (3/4)"、"1=Bb (6/8)"
            无法获取时返回空字符串

        示例（10条 case）:
            key_val=0,  time_sig=(4,4) → "1=C (4/4)"
            key_val=1,  time_sig=(4,4) → "1=G (4/4)"
            key_val=-1, time_sig=(4,4) → "1=F (4/4)"
            key_val=-2, time_sig=(3,4) → "1=Bb (3/4)"
            key_val=5,  time_sig=(2,2) → "1=B (2/2)"
            key_val=7,  time_sig=(4,4) → "1=C# (4/4)"
            key_val=-7, time_sig=(4,4) → "1=Cb (4/4)"
            key_val=2,  time_sig=(6,8) → "1=D (6/8)"
            key_val=-4, time_sig=(4,4) → "1=Ab (4/4)"
            key_val=3,  time_sig=(3,4) → "1=A (3/4)"
        """
        # 调号值→调性名称映射表（Circle of Fifths: 纯五度循环）
        # key_val 为正数表示升号调（#），负数表示降号调（b）
        KEY_NAMES = {
            0: 'C',
            1: 'G',
            2: 'D',
            3: 'A',
            4: 'E',
            5: 'B',
            6: 'F#',
            7: 'C#',
            8: 'G#',
            9: 'D#',
            10: 'A#',
            11: 'F##',
            -1: 'F',
            -2: 'Bb',
            -3: 'Eb',
            -4: 'Ab',
            -5: 'Db',
            -6: 'Gb',
            -7: 'Cb',
            -8: 'Fb',
        }

        try:
            # 提取调号值（GTPSong 字段名为 key，不是 key_signature）
            key_val = getattr(song, 'key', None)
            if key_val is None:
                # 回退: 尝试 key_signature 属性
                key_val = getattr(song, 'key_signature', 0)
                if isinstance(key_val, (list, tuple)):
                    key_val = key_val[0] if len(key_val) > 0 else 0
            else:
                key_val = int(key_val)

            # 提取拍号（取第一个音轨第一个小节的拍号）
            if song.tracks and song.tracks[0].measures:
                ts = song.tracks[0].measures[0].time_signature
                if isinstance(ts, (list, tuple)) and len(ts) >= 2:
                    num, den = int(ts[0]), int(ts[1])
                else:
                    num, den = 4, 4  # 默认 4/4 拍
            else:
                num, den = 4, 4

            # 确保 key_val 为 int（防止 None 传入 dict.get）
            if key_val is None:
                key_val = 0
            key_name = KEY_NAMES.get(key_val, f'?{key_val}')
            return f"1={key_name} ({num}/{den})"

        except Exception:
            return ""

    @staticmethod
    def _midi_to_note_name(midi: int) -> str:
        """将MIDI音高值转换为音符名称"""
        note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        octave = (midi // 12) - 1
        note = note_names[midi % 12]
        return f"{note}{octave}"

    # ============================================================
    # 系统(行)绘制
    # ============================================================

    def _draw_system(self, painter: QPainter, track: GTPTrack, system: SystemLayout) -> None:
        """
        绘制一行完整的六线谱系统（含所有小节）

        绘制顺序:
          1. 六条弦线
          2. 信息栏（TAB标识 + 谱号 + 拍号 + 调号）— 仅每行第一系统绘制
          3. 每个小节的内容（小节线 + 音符 + 技巧标记）

        信息栏设计（参照 Guitar Pro 标准布局）:
          - 最左侧: "T A B" 竖排文字（标识这是六线谱）
          - 中部: 拍号(如4/4上下堆叠) + 调号升降号数
          - 上方: 调性名称(如C5, G3等)
          - 用竖线与后续小节内容分隔
        """

        # 1. 绘制六条弦线
        self._draw_tab_lines(painter, system)

        # 2. 在该系统第一个小节前绘制独立信息栏
        if system.measures:
            first_measure = system.measures[0].measure
            self._draw_info_bar(painter, first_measure, system)

        # 3. 绘制每个小节的内容
        for m_layout in system.measures:
            self._draw_measure(painter, track, system, m_layout)

        # 3.5 绘制歌词（六线谱下方的歌词带，等价 alphaTab lyrics effect band）
        self._draw_lyrics(painter, system)

        # 4. 系统级技巧延长虚线（P.M./Let Ring/N.H./A.H./T.H./P.H.）
        #    在所有小节绘制完成后统一收集并画在弦线区域上方
        self._draw_system_technique_extensions(painter, system)

    def _draw_lyrics(self, painter: QPainter, system: SystemLayout) -> None:
        """
        绘制歌词带 - 移植自 alphaTab LyricsGlyph/LyricsEffectInfo

        布局规则（与 alphaTab 一致）:
          - 歌词绘制在六线谱下方（y_lyrics_top 起），居中对齐到拍中心
          - 多行歌词垂直堆叠，每行间距 LYRICS_LINE_PITCH
          - 仅绘制非空歌词片段；beat.lyrics 为 None/空则跳过
          - 歌词为消色差文本，使用主题 COLOR_TEXT，不参与 CVD 颜色变换
        """
        if system.lyrics_line_count <= 0 or system.y_lyrics_top <= 0:
            return

        painter.setPen(QColor(self.cfg.theme.COLOR_TEXT))
        font = QFont(self.cfg.LYRICS_FONT_FAMILY, self.cfg.LYRICS_FONT_SIZE)
        painter.setFont(font)
        fm = QFontMetrics(font)
        pitch = self.cfg.LYRICS_LINE_PITCH

        for m_layout in system.measures:
            for bl in m_layout.beats:
                beat = bl.beat
                if not beat.lyrics:
                    continue
                cx = bl.x_center
                for i, line_text in enumerate(beat.lyrics):
                    if not line_text:
                        continue  # 空片段不绘制（多行中该拍此行无歌词）
                    tw = fm.horizontalAdvance(line_text)
                    # 第 i 行基线 = 歌词带顶 + i*pitch + ascent，保证落在预留带内
                    baseline = system.y_lyrics_top + i * pitch + fm.ascent()
                    painter.drawText(QPoint(cx - tw // 2, baseline), line_text)

    def _draw_info_bar(self, painter: QPainter, measure: GTPMeasure, system: SystemLayout) -> None:
        """
        绘制独立信息栏（每行系统开头，类似 Guitar Pro 的谱号区域）

        布局结构（从左到右）:
          ┌────┬─────┬──────┬────┐
          │    │调性 │      │    │
          │ T │拍号 │      │ 小│
          │ A │4/4 │ 竖线 │ 节 │
          │ B │     │      │ 内│
          │    │     │      │ 容│
          └────┴─────┴──────┴────┘

        参数:
            painter: QPainter绑制对象
            measure: 该行第一个小节（含拍号/调号信息）
            system:  系统布局（含Y坐标）
        """
        # --- 布局参数（信息栏总宽度约55px）---
        tab_x = self.cfg.PAGE_MARGIN_LEFT - 38  # TAB文字X坐标
        clef_x = self.cfg.PAGE_MARGIN_LEFT - 24  # 谱号线X坐标
        ts_x = self.cfg.PAGE_MARGIN_LEFT - 6  # 拍号X坐标
        divider_x = self.cfg.PAGE_MARGIN_LEFT + 18  # 分隔线X坐标

        y_top = system.y_tab_top
        y_bot = system.y_tab_bottom
        (y_top + y_bot) // 2

        # ===== 1. "T A B" 竖排文字（最左侧）=====
        painter.setPen(QColor(self.cfg.theme.COLOR_TEXT))
        font = QFont(self.cfg.NOTE_FONT_FAMILY, 8, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        # 竖向排列 T / A / B，均匀分布在六线谱高度范围内
        tab_letters = ['T', 'A', 'B']
        tab_spacing = (y_bot - y_top) // (len(tab_letters) + 1)
        for i, letter in enumerate(tab_letters):
            ly = y_top + tab_spacing * (i + 1) + 3  # +3使视觉居中
            tw = fm.horizontalAdvance(letter)
            painter.drawText(QPoint(tab_x - tw // 2, int(ly)), letter)

        # ===== 2. 谱号竖线（类似高音谱号的简化表示）=====
        pen = QPen(QColor(self.cfg.theme.COLOR_TEXT), 1.5)
        painter.setPen(pen)

        # 根据实际弦数动态计算谱号线位置
        string_count = getattr(system, 'string_count', 6) or 6
        # 画一条从第2弦到倒数第2弦的竖线（模拟谱号主体）
        line_y1 = y_top + int(self.cfg.TAB_LINE_SPACING * 1)  # 第2弦位置
        line_y2 = y_top + int(self.cfg.TAB_LINE_SPACING * (string_count - 1.5))  # 接近最后弦底部
        painter.drawLine(clef_x, line_y1, clef_x, line_y2)
        # 底部小弯钩（模拟谱号尾部曲线）
        from PyQt5.QtGui import QPainterPath

        hook_path = QPainterPath()
        hook_path.moveTo(clef_x, line_y2)
        hook_path.cubicTo(clef_x + 5, line_y2 - 3, clef_x + 6, line_y2 + 2, clef_x + 2, line_y2 + 6)
        painter.drawPath(hook_path)

        # ===== 3. 拍号（分子在上，分母在下）=====
        numerator, denominator = measure.time_signature

        # 始终显示拍号（Guitar Pro 风格：每行开头都显示）
        font_ts = QFont(self.cfg.NOTE_FONT_FAMILY, 11, QFont.Bold)
        painter.setFont(font_ts)
        fm_ts = QFontMetrics(font_ts)

        # 分子（上数字）：位于弦线区域上1/3处
        num_text = str(numerator)
        num_w = fm_ts.horizontalAdvance(num_text)
        num_y_pos = y_top + int(self.cfg.TAB_LINE_SPACING * (string_count * 0.25)) + 9
        painter.drawText(QPoint(ts_x - num_w // 2, int(num_y_pos)), num_text)

        # 分母（下数字）：位于弦线区域下1/3处
        den_text = str(denominator)
        den_w = fm_ts.horizontalAdvance(den_text)
        den_y_pos = y_top + int(self.cfg.TAB_LINE_SPACING * (string_count * 0.7)) + 4
        painter.drawText(QPoint(ts_x - den_w // 2, int(den_y_pos)), den_text)

        # ===== 4. 调号（在拍号上方显示调性名称或升降号数）=====
        key_sig = measure.key_signature
        if isinstance(key_sig, (list, tuple)):
            key_val = key_sig[0] if len(key_sig) > 0 else 0
        else:
            key_val = key_sig if key_sig else 0

        font_key = QFont(self.cfg.NOTE_FONT_FAMILY, 7)
        painter.setFont(font_key)
        painter.setPen(QColor("#888888"))  # 灰色次要信息

        if key_val > 0:
            key_text = f"#{key_val}"
        elif key_val < 0:
            key_text = f"b{abs(key_val)}"
        else:
            key_text = ""  # C大调不显示或显示"C"

        if key_text:
            fm_key = QFontMetrics(font_key)
            kw = fm_key.horizontalAdvance(key_text)
            painter.drawText(QPoint(ts_x - kw // 2, int(y_top) - 4), key_text)

        # ===== 5. 右侧分隔线（双细线，类似GP的小节线风格）=====
        pen_div = QPen(QColor(self.cfg.theme.COLOR_BARLINE), 1)
        painter.setPen(pen_div)
        painter.drawLine(divider_x, y_top - 2, divider_x, y_bot + 2)
        painter.drawLine(divider_x + 3, y_top - 2, divider_x + 3, y_bot + 2)

    def _draw_time_signature(
        self, painter: QPainter, measure: GTPMeasure, system: SystemLayout
    ) -> None:
        """
        [已废弃] 拍号绘制已合并到 _draw_info_bar() 中统一处理。
        此方法保留仅作向后兼容。
        """

    def _draw_tab_lines(self, painter: QPainter, system: SystemLayout) -> None:
        """
        绘制水平弦线（六线谱/四线谱等的基础线）

        弦数量从GTP文件动态读取(system.string_count)，支持:
          - 4弦(贝斯/尤克里里)
          - 5弦(5弦贝斯)
          - 6弦(标准吉他, 默认)
          - 7弦(7弦吉他)
        """
        pen = QPen(QColor(self.cfg.theme.COLOR_TAB_LINE), self.cfg.TAB_LINE_THICKNESS)
        painter.setPen(pen)

        x_start = self.cfg.PAGE_MARGIN_LEFT - 5
        x_end = self.cfg.PAGE_MARGIN_LEFT + 800  # 行宽度估算

        # 找到该行最右边的小节结束位置
        if system.measures:
            x_end = system.measures[-1].x_end + 10

        # 根据实际弦数绘制弦线
        string_count = getattr(system, 'string_count', 6) or 6
        for i in range(string_count):
            y = system.y_tab_top + i * self.cfg.TAB_LINE_SPACING
            painter.drawLine(x_start, y, x_end, y)

    # ============================================================
    # 小节绘制
    # ============================================================

    def _draw_measure(
        self, painter: QPainter, track: GTPTrack, system: SystemLayout, m_layout: MeasureLayout
    ) -> None:
        """绘制单个小节（包含小节线和所有拍）"""
        measure = m_layout.measure

        # 1. 绘制小节线（左侧）
        self._draw_barline(
            painter,
            m_layout.x_start - 2,
            system.y_tab_top - self.cfg.BARLINE_HEIGHT_EXTEND,
            system.y_tab_bottom + self.cfg.BARLINE_HEIGHT_EXTEND,
            is_open=measure.is_repeat_open,
        )

        # 2. 绘制重复记号
        if measure.is_repeat_open:
            self._draw_repeat_dots(
                painter, m_layout.x_start - 2, system.y_tab_top, system.y_tab_bottom, side='left'
            )
        if measure.repeat_close > 0:
            # 右侧重复线 + 点
            self._draw_barline(
                painter,
                m_layout.x_end + 2,
                system.y_tab_top - self.cfg.BARLINE_HEIGHT_EXTEND,
                system.y_tab_bottom + self.cfg.BARLINE_HEIGHT_EXTEND,
                is_double=True,
            )
            self._draw_repeat_dots(
                painter, m_layout.x_end + 2, system.y_tab_top, system.y_tab_bottom, side='right'
            )
            # 重复次数标记
            painter.setPen(QColor(self.cfg.theme.COLOR_REPEAT))
            painter.setFont(QFont(self.cfg.NOTE_FONT_FAMILY, 8))
            painter.drawText(
                int(m_layout.x_end + 8), int(system.y_tab_bottom + 14), str(measure.repeat_close)
            )

        # 3. 绘制段落标记
        if measure.marker:
            painter.setPen(QColor(self.cfg.theme.COLOR_TECHNIQUE))
            painter.setFont(QFont(self.cfg.NOTE_FONT_FAMILY, 9, QFont.Bold))
            painter.drawText(int(m_layout.x_start), int(system.y_tab_top - 8), measure.marker)

        # 4. 绘制每个拍（音符）
        for b_layout in m_layout.beats:
            self._draw_beat(painter, system, b_layout, m_layout)

        # 4.5. 绘制和弦名 (v1.4.0 新增: 谱表顶部上方, 自动过滤连续相同)
        #     音轨中有 chord 就画, 没有不管
        self._draw_chord_names(painter, system, m_layout)

        # 5. 绘制小节线
        self._draw_barline(painter, m_layout.x_end, system.y_tab_top, system.y_tab_bottom)

    def _draw_barline(
        self,
        painter: QPainter,
        x: int,
        y_top: int,
        y_bottom: int,
        is_open: bool = False,
        is_double: bool = False,
    ) -> None:
        """绘制小节线"""
        pen = QPen(QColor(self.cfg.theme.COLOR_BARLINE), self.cfg.BARLINE_THICKNESS)
        painter.setPen(pen)
        painter.drawLine(x, y_top, x, y_bottom)

        if is_double:
            painter.drawLine(x + 4, y_top, x + 4, y_bottom)

        if is_open:
            # 反复起始加粗线
            thick_pen = QPen(QColor(self.cfg.theme.COLOR_BARLINE), 3)
            painter.setPen(thick_pen)
            painter.drawLine(x + 4, y_top, x + 4, y_bottom)

    def _draw_repeat_dots(
        self, painter: QPainter, x: int, y_top: int, y_bottom: int, side: str = 'left'
    ) -> None:
        """绘制反复记号的两个点"""
        painter.setPen(QPen(QColor(self.cfg.theme.COLOR_BARLINE), 3))
        dot_y1 = y_top + (y_bottom - y_top) // 3
        dot_y2 = y_bottom - (y_bottom - y_top) // 3
        offset = 6 if side == 'right' else -8
        painter.drawEllipse(x + offset, dot_y1 - 2, 4, 4)
        painter.drawEllipse(x + offset, dot_y2 - 2, 4, 4)

    # ============================================================
    # 拍(Beat)绘制 - 核心渲染逻辑
    # ============================================================

    def _draw_beat(
        self,
        painter: QPainter,
        system: SystemLayout,
        b_layout: BeatLayout,
        m_layout: MeasureLayout | None = None,
    ) -> None:
        """
        绘制单个拍的所有内容：
          1. 品格数字（在对应弦线上）
          2. 符干（根据音符位置决定上下方向）
          3. 符尾（连接同组短时值音符）
          4. 技巧标记缩写

        参数:
            m_layout: 可选，传入时支持滑音连线查找下一个音符
        """
        beat = b_layout.beat
        cx = b_layout.x_center  # 该拍的中心X坐标

        # --- 休止符处理 ---
        # 休止符渲染规则（严格参照 Guitar Pro 5 的六线谱行为）:
        #   规则1: 完全空的小节（所有拍都是休止符，无任何音符）→ 不画任何休止符
        #          GP5中空小节完全空白，只有小节线
        #   规则2: 有音符的小节中的休止拍 → 仅绘制有意义的休止符
        #          （排除末尾填充空拍：最后1个拍且时值≥四分音符）
        #   规则3: 全休止符/二分休止符 → 改用细线框样式（避免填充矩形看起来像□）
        if beat.is_rest and not beat.notes:
            # === 规则1: 检查整个小节是否完全为空（无任何音符）===
            is_empty_measure = True
            if m_layout and m_layout.beats:
                for other_bl in m_layout.beats:
                    if other_bl.beat.notes:  # 只要有一个拍包含音符
                        is_empty_measure = False
                        break

            # 空小节完全不画休止符
            if is_empty_measure:
                return

            # === 规则2: 检查是否为末尾填充空拍 ===
            is_meaningful_rest = True
            if m_layout and m_layout.beats:
                try:
                    beat_idx = m_layout.beats.index(b_layout)
                    # 如果是小节最后一个拍，且时值为四分或更长 → 可能是填充
                    if (
                        beat_idx == len(m_layout.beats) - 1
                        and beat.duration.value <= NoteDuration.QUARTER.value
                    ):
                        is_meaningful_rest = False
                except ValueError:
                    pass

            if is_meaningful_rest:
                self._draw_rest_symbol(painter, beat, cx, system)
            return

        # --- 按弦分组绘制品格数字 ---
        # 同一拍内不同弦上的音符垂直排列
        for note in beat.notes:
            self._draw_note_fret(painter, note, cx, system)

        # --- 绘制技巧标记（传入m_layout以支持滑音连线查找下一个音符）---
        for note in beat.notes:
            # 记录父拍引用，供滑音查找用（动态属性，用 setattr 绕过 mypy attr-defined 检查）
            setattr(note, '_parent_beat', beat)  # noqa: B010
            self._draw_technique_marks(painter, note, cx, system, m_layout)

        # --- 绘制符干 ---
        self._draw_stem(painter, beat, b_layout, system)

    def _draw_note_fret(
        self, painter: QPainter, note: GTPNote, cx: int, system: SystemLayout
    ) -> None:
        """
        在对应弦线上绘制品格数字

        坐标计算:
          Y坐标 = y_tab_top + string_index × TAB_LINE_SPACING
                 （string_index: 0=1弦顶线, 5=6弦底线）
          X坐标 = cx（拍的中心位置）

        文字居中策略: 让文字的视觉中心对齐到弦线位置，
                     避免品格数字超出六线谱区域（尤其是底部弦）。
                     公式: baseline = line_y - height/2 + ascent
        """
        # 计算Y坐标：弦索引越大越靠下（1弦在最上面）
        y = system.y_tab_top + note.string * self.cfg.TAB_LINE_SPACING

        # 字体设置
        font = QFont(self.cfg.NOTE_FONT_FAMILY, self.cfg.NOTE_FONT_SIZE)
        painter.setFont(font)

        # 颜色：幽灵音用灰色，正常音符用主文字色
        if note.is_ghost:
            painter.setPen(QColor("#666666"))
        else:
            painter.setPen(QColor(self.cfg.theme.COLOR_TEXT))

        # 获取显示文本
        display_text = note.get_display_fret()

        # 居中绘制：文字中心对齐到弦线Y位置
        fm = QFontMetrics(font)
        text_width = fm.horizontalAdvance(display_text)
        text_x = cx - text_width // 2
        # 垂直居中：文字视觉中心对齐弦线，避免底部弦文字溢出
        text_y = y - fm.height() // 2 + fm.ascent()

        painter.drawText(QPoint(text_x, text_y), display_text)

    def _draw_technique_marks(
        self,
        painter: QPainter,
        note: GTPNote,
        cx: int,
        system: SystemLayout,
        m_layout: MeasureLayout | None = None,
    ) -> None:
        """
        绘制技巧标记（增强版：根据技巧类型选择不同的可视化方式）

        渲染策略（按优先级）:
          1. 泛音 → 菱形包围品格数字 + 缩写文字
          2. 滑音 → 斜线连接到下一个同弦音符 + 缩写文字
          3. 推弦 → 弧线箭头 + 文字
          4. 颤音 → 波浪线(~~~)在音符上方
          5. P.M. / Let Ring → 文字标记（虚线延长在小节级绘制）
          6. 其他 → 缩写文字显示在品格数字右侧

        参数:
            painter:   QPainter绑制对象
            note:      音符数据(含techniques列表)
            cx:        该拍的中心X坐标
            system:    系统布局(含Y坐标信息)
            m_layout:  小节布局(可选，用于滑音查找下一个音符位置)
        """
        if not note.techniques:
            return

        y_base = system.y_tab_top + note.string * self.cfg.TAB_LINE_SPACING

        # 按类型分别处理每种技巧的图形化渲染
        for tech in note.techniques:
            # --- 泛音：菱形包围品格数字 ---
            if tech in (
                TechniqueType.NATURAL_HARMONIC,
                TechniqueType.ARTIFICIAL_HARMONIC,
                TechniqueType.TAPPED_HARMONIC,
                TechniqueType.PINCH_HARMONIC,
            ):
                self._draw_harmonic_diamond(painter, cx, y_base)

            # --- 滑音：斜线连接到下一个同弦音符 ---
            elif tech in (TechniqueType.SLIDE_UP, TechniqueType.SLIDE_DOWN):
                self._draw_slide_line(painter, note, cx, y_base, system, m_layout, tech)

            # --- 击弦/勾弦：弧线连接两个音符 + H/P在弧线上 ---
            elif tech in (TechniqueType.HAMMER_ON, TechniqueType.PULL_OFF):
                self._draw_hammer_on_arc(painter, note, cx, y_base, system, m_layout, tech)

            # --- 推弦：弧线箭头 ---
            elif tech == TechniqueType.BEND:
                self._draw_bend_indicator(painter, note, cx, y_base, system)

            # --- 颤音：波浪线 ---
            elif tech == TechniqueType.VIBRATO:
                self._draw_vibrato_wave(painter, cx, y_base, system)

        # --- 绘制文字缩写标签（所有非图形化的技巧）---
        self._draw_technique_text_labels(painter, note, cx, system)

    def _draw_harmonic_diamond(self, painter: QPainter, cx: int, y_base: int) -> None:
        """
        绘制泛音菱形标记：在品格数字位置画一个菱形框

        原理: 泛音在标准记谱中用菱形音符头表示，
              六线谱中用菱形包围品格数字来模拟此效果。

        参数:
            cx:      品格数字中心X坐标
            y_base:  弦线Y坐标
        """
        painter.setPen(QPen(QColor(self.cfg.theme.COLOR_TECHNIQUE), 1))
        # 菱形大小：约等于品格数字的大小
        size = 8  # 菱形半宽(px)，调整效果: 越大菱形越明显
        # 画菱形: 上→右→下→左→上
        diamond = [
            QPoint(cx, y_base - size),  # 顶点
            QPoint(cx + size, y_base),  # 右点
            QPoint(cx, y_base + size),  # 底点
            QPoint(cx - size, y_base),  # 左点
        ]
        painter.drawPolygon(QPolygon(diamond))

    def _draw_slide_line(
        self,
        painter: QPainter,
        note: GTPNote,
        cx: int,
        y_base: int,
        system: SystemLayout,
        m_layout: MeasureLayout | None,
        slide_type: TechniqueType,
    ) -> None:
        """
        绘制滑音连线：从当前音符画斜线到下一个同弦的音符

        原理: 滑音(slide)表示手指从当前品滑到目标品而不离弦。
              用斜线连接两个音符位置直观表达滑音方向：
              - 上滑(Slide Up): 线向右上方倾斜（品位升高）
              - 下滑(Slide Down): 线向右下方倾斜（品位降低）

        参数:
            note:       当前音符
            cx:         当前拍中心X
            y_base:     当前弦线Y
            system:     系统布局
            m_layout:   小节布局（用于查找下一个同弦音符的位置）
            slide_type: SLIDE_UP 或 SLIDE_DOWN
        """
        if not m_layout:
            return

        # 在同一小节的后续拍中查找下一个同弦音符
        target_cx = None
        found_current = False
        # _parent_beat 是渲染时动态附加的属性，用 getattr 安全访问
        parent_beat = getattr(note, '_parent_beat', None)

        for b in m_layout.beats:
            if b.beat is parent_beat:
                found_current = True
                continue
            if not found_current:
                continue
            # 找到了后续拍，检查是否有同弦音符
            for n in b.beat.notes:
                if n.string == note.string:
                    target_cx = b.x_center
                    break
            if target_cx is not None:
                break

        if target_cx is None:
            return  # 没找到目标音符，不画连线

        # 设置滑线样式
        pen = QPen(QColor(self.cfg.theme.COLOR_TECHNIQUE), 1)
        painter.setPen(pen)

        # 计算终点Y坐标（目标音符所在弦线）
        target_y = system.y_tab_top + note.string * self.cfg.TAB_LINE_SPACING

        # 根据滑音方向决定倾斜方向（严格参照GP5格式）
        #   上滑(SLIDE_UP)   → 斜线向上 / (从左下到右上，表示音高上升)
        #   下滑(SLIDE_DOWN) → 斜线向下 \ (从左上到右下，表示音高下降)
        #   斜率基于目标音符位置+方向偏移，使斜线清晰可见
        # 上滑(SLIDE_UP): 终点Y比起点高(屏幕坐标Y更小=视觉上方)
        # 下滑(SLIDE_DOWN): 终点Y比起点低(屏幕坐标Y更大=视觉下方)
        end_y = target_y - 8 if slide_type == TechniqueType.SLIDE_UP else target_y + 8

        # 从品格数字右侧画斜线到目标音符左侧
        start_x = cx + 6
        painter.drawLine(start_x, y_base, target_cx - 4, end_y)

        # === 在斜杠中点绘制 "s" 或 "S" 文字 ===
        slide_label = "s" if slide_type == TechniqueType.SLIDE_UP else "S"
        font_s = QFont(self.cfg.NOTE_FONT_FAMILY, 7)
        painter.setFont(font_s)
        fm_s = QFontMetrics(font_s)
        sw = fm_s.horizontalAdvance(slide_label)
        mid_x = (start_x + target_cx - 4) // 2 - sw // 2
        mid_y = (y_base + end_y) // 2 - 3
        painter.drawText(QPoint(int(mid_x), int(mid_y)), slide_label)

    def _draw_hammer_on_arc(
        self,
        painter: QPainter,
        note: GTPNote,
        cx: int,
        y_base: int,
        system: SystemLayout,
        m_layout: MeasureLayout | None,
        tech_type: TechniqueType,
    ) -> None:
        """
        绘制击弦(Hammer On)/勾弦(Pull Off)弧线 - 参照GP5格式

        GP5格式: 在两个同弦音符之间画一条向上凸的圆弧，
                弧线上方中央显示 "H"(击弦) 或 "P"(勾弦)

        原理: 击弦表示用左手手指敲击指板发出音(不拨弦)，
              勾弦表示手指从高品滑向低品并勾出声音。
              用弧线连接两个音符直观表达这种"连奏"关系。

        参数:
            note:      当前音符
            cx:        当前拍中心X
            y_base:    当前弦线Y
            system:    系统布局
            m_layout:  小节布局（用于查找下一个同弦音符）
            tech_type: HAMMER_ON 或 PULL_OFF
        """
        if not m_layout:
            return

        # 查找下一个同弦音符的位置
        target_cx = None
        found_current = False
        # _parent_beat 是渲染时动态附加的属性，用 getattr 安全访问
        parent_beat = getattr(note, '_parent_beat', None)

        for b in m_layout.beats:
            if b.beat is parent_beat:
                found_current = True
                continue
            if not found_current:
                continue
            for n in b.beat.notes:
                if n.string == note.string:
                    target_cx = b.x_center
                    break
            if target_cx is not None:
                break

        if target_cx is None:
            return  # 没有目标音符，不画弧线

        pen = QPen(QColor(self.cfg.theme.COLOR_TECHNIQUE), 1.0)
        painter.setPen(pen)

        # === 绘制向上凸的圆弧（类似符尾连线）===
        from PyQt5.QtGui import QPainterPath

        arc_path = QPainterPath()

        arc_start_x = cx + 6  # 起点：品格数字右侧
        arc_end_x = target_cx - 4  # 终点：目标品格数字左侧
        arc_mid_x = (arc_start_x + arc_end_x) / 2  # 弧线中点X

        # 弧线高度（固定值，视觉上清晰可见）
        arc_h = 8  # px，调整效果: 增大则弧线更弯

        arc_path.moveTo(arc_start_x, y_base - 1)
        # 二次贝塞尔曲线：控制点在起止点中点的上方
        ctrl_y = y_base - 1 - arc_h
        arc_path.quadTo(arc_mid_x, ctrl_y, arc_end_x, y_base - 1)
        painter.drawPath(arc_path)

        # === 在弧线顶部绘制 H 或 P 文字 ===
        label = "H" if tech_type == TechniqueType.HAMMER_ON else "P"
        font_h = QFont(self.cfg.NOTE_FONT_FAMILY, 7)
        painter.setFont(font_h)
        fm_h = QFontMetrics(font_h)
        lw = fm_h.horizontalAdvance(label)

        label_x = int(arc_mid_x - lw // 2)
        label_y = int(ctrl_y - 2)  # 文字在弧线顶点上方
        painter.drawText(QPoint(label_x, label_y), label)

    def _draw_bend_indicator(
        self, painter: QPainter, note: GTPNote, cx: int, y_base: int, system: SystemLayout
    ) -> None:
        """
        绘制推弦指示器 - 严格参照 Guitar Pro 5 的推弦记谱格式

        GP5 推弦格式（3种类型）:
          类型1(完整推+释放):  "1/4"文字 + 上弧线↑ + 下弧线↓ 回到原位
          类型2(仅推弦):      "1/4"文字 + 上弧线↑ (不回)
          类型3(推+保持+释放): "1/2"文字 + 上弧线↑ + 虚线横线 + ↓

        视觉特征:
          - 度数文字("1/4"/"1/2"/"Full")在弧线上方居中显示
          - 弧线从品格数字下方开始，向上弯曲
          - 峰值处有向上箭头(▲)
          - 有释放时末端有向下箭头(▼)或虚线段

        参数:
            note:    音符对象(含bend属性:BendData)
            cx:      拍中心X坐标
            y_base:  弦线Y坐标
            system:  系统布局

        数据来源: note.bend (BendData对象, 从GTP文件解析的BendEffect)
        """
        # 获取推弦数据
        bend = getattr(note, 'bend', None)
        if not bend or not bend.points:
            # 无详细数据时用简单渲染(向后兼容)
            self._draw_bend_simple(painter, cx, y_base, system)
            return

        pen = QPen(QColor(self.cfg.theme.COLOR_TECHNIQUE), 1.5)
        painter.setPen(pen)

        # === 布局参数 ===
        text = bend.get_display_text()  # "1/4", "1/2", "Full"

        # 弧线起点：品格数字下方偏左
        start_x = cx - 6
        start_y = y_base + 6

        # === 关键: 确保推弦箭头超出六线谱最顶线(y_tab_top) ===
        # GP5标准: 推弦弧线的峰值(箭头尖端)必须在第1弦线之上
        # 最小超出距离: 14px（箭头尖+度数文字都要在顶线外，参照GP5截图）
        min_above_top = 14

        # 基础弧线高度（根据推弦量）
        base_arc_map = {25: 12, 50: 16, 75: 20, 100: 24}  # px基础高度
        base_arc_h = base_arc_map.get(bend.max_value, 18)

        # 计算需要的最小弧高: 从start_y到顶线外min_above_top的距离
        required_min_h = (start_y - system.y_tab_top) + min_above_top
        # 取基础值和最小值中较大的一个
        arc_h = max(base_arc_h, required_min_h)

        # 弧线总宽度
        total_w = 16  # 基础宽度(px)，调整效果: 增大则弧线更宽更平缓

        # 峰值点坐标（确保在顶线之上）
        peak_x = start_x + total_w * 0.45
        peak_y = start_y - arc_h

        # 终点坐标
        end_x = start_x + total_w
        end_y = start_y if bend.has_release else peak_y  # 有释放则回到起点Y

        # === 1. 绘制度数文字（在弧线上方）===
        font = QFont(self.cfg.NOTE_FONT_FAMILY, 7)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        text_x = peak_x - tw // 2
        text_y = peak_y - 8  # 文字在峰值上方
        painter.drawText(QPoint(int(text_x), int(text_y)), text)

        # === 2. 绘制上弯弧线（从起点到峰值）===
        from PyQt5.QtGui import QPainterPath

        path = QPainterPath()
        path.moveTo(start_x, start_y)
        # 二次贝塞尔曲线：控制点在起止点连线的上方中点
        ctrl_x = (start_x + peak_x) / 2
        ctrl_y = min(start_y, peak_y) - arc_h * 0.3  # 控制点略高于峰值
        path.quadTo(ctrl_x, ctrl_y, peak_x, peak_y)
        painter.drawPath(path)

        # === 3. 在峰值处画向上箭头 ▲ ===
        arr_size = 4  # 箭头大小(px)
        painter.drawLine(
            int(peak_x), int(peak_y), int(peak_x - arr_size), int(peak_y + arr_size * 0.8)
        )
        painter.drawLine(
            int(peak_x), int(peak_y), int(peak_x + arr_size), int(peak_y + arr_size * 0.8)
        )

        # === 4. 如果有释放段，绘制下弯弧线或虚线+下箭头 ===
        if bend.has_release:
            # 判断释放方式：如果终点接近起点Y值 → 用平滑回曲线(Image1风格)
            # 否则用虚线横线+下箭头(Image3风格)
            release_path = QPainterPath()
            release_path.moveTo(peak_x, peak_y)

            # 用二次贝塞尔曲线绘制释放段（从峰值回落到终点）
            r_ctrl_x = (peak_x + end_x) / 2
            r_ctrl_y = min(peak_y, end_y) - arc_h * 0.15  # 释放段控制点较平
            release_path.quadTo(r_ctrl_x, r_ctrl_y, end_x, end_y)
            painter.drawPath(release_path)

            # 在终点处画向下箭头 ▼
            painter.drawLine(
                int(end_x), int(end_y), int(end_x - arr_size), int(end_y - arr_size * 0.8)
            )
            painter.drawLine(
                int(end_x), int(end_y), int(end_x + arr_size), int(end_y - arr_size * 0.8)
            )

    def _draw_bend_simple(
        self, painter: QPainter, cx: int, y_base: int, system: SystemLayout
    ) -> None:
        """
        简单推弦渲染（无详细数据时的后备方案）
        仅画一个基础弧线箭头，不显示度数文字
        """
        pen = QPen(QColor(self.cfg.theme.COLOR_TECHNIQUE), 1.5)
        painter.setPen(pen)

        arc_start_x = cx - 4
        arc_start_y = y_base - 10
        arc_end_x = cx + 8
        arc_end_y = y_base - 4

        ctrl_x = cx + 2
        ctrl_y = y_base - 18

        from PyQt5.QtGui import QPainterPath

        path = QPainterPath()
        path.moveTo(arc_start_x, arc_start_y)
        path.quadTo(ctrl_x, ctrl_y, arc_end_x, arc_end_y)
        painter.drawPath(path)

        arrow_size = 3
        painter.drawLine(arc_end_x, arc_end_y, arc_end_x - arrow_size, arc_end_y + arrow_size)
        painter.drawLine(arc_end_x, arc_end_y, arc_end_x - arrow_size, arc_end_y - arrow_size)

    def _draw_vibrato_wave(
        self, painter: QPainter, cx: int, y_base: int, system: SystemLayout
    ) -> None:
        """
        绘制颤音波浪线：在音符上方画 "~~~" 形状的波浪线

        原理: 颤音(vibrato)通过快速小幅摇动按弦手指产生音高波动。
              记谱法中使用波浪线(~)表示。六线谱中画在品格数字正上方。

        参数:
            cx:      拍中心X坐标
            y_base:  弦线Y坐标
            system:  系统布局
        """
        pen = QPen(QColor(self.cfg.theme.COLOR_TECHNIQUE), 1)
        painter.setPen(pen)

        # 波浪线参数
        wave_y = y_base - 14  # 波浪线基线Y（品格数字上方）
        wave_width = 5  # 每个波的宽度(px)
        wave_height = 2  # 波浪振幅(px)
        wave_count = 3  # 波的数量（~~~ = 3个波）

        start_x = cx - (wave_width * wave_count) // 2

        # 用小线段拼接出波浪效果
        points = []
        for i in range(wave_count * 2 + 1):
            px = start_x + i * (wave_width // 2)
            py = wave_y if i % 2 == 0 else wave_y + wave_height
            points.append(QPoint(px, py))

        for i in range(len(points) - 1):
            painter.drawLine(points[i], points[i + 1])

    def _draw_technique_text_labels(
        self, painter: QPainter, note: GTPNote, cx: int, system: SystemLayout
    ) -> None:
        """
        绘制技巧文字标签（用于不需要图形化或已有图形化但还需补充文字的技巧）

        显示规则:
          - 单字符标记(H/P/s/B/>/.等): 显示在品格数字右侧
          - 多字符标记(P.M./Let Ring/N.H.等): 显示在六线谱下方区域
          - 已有图形化的技巧(泛音/滑音/推弦/颤音): 也同时显示简短文字辅助说明
          - P.M. 和 Let Ring 不在此处绘制（由 _draw_pm_letring_extensions 统一画虚线+标签）

        参数:
            painter: QPainter绑制对象
            note:    音符数据
            cx:      拍中心X坐标
            system:  系统布局
        """
        if not note.techniques:
            return

        # P.M.和Let Ring由虚线延长方法统一处理，此处跳过避免重复标注
        # BEND由_draw_bend_indicator图形化处理(弧线+度数文字)，不再重复画"B"
        # SLIDE_UP/DOWN由_draw_slide_line图形化处理(斜线+s/S)，不再重复画文字
        # HAMMER_ON/PULL_OFF由_draw_hammer_on_arc图形化处理(弧线+H/P)，不再重复画文字
        # 泛音类(NH/AH/TH/PH)由虚线延长方法统一处理
        SKIP_TECHS = {
            TechniqueType.PALM_MUTE,
            TechniqueType.LET_RING,
            TechniqueType.BEND,
            TechniqueType.SLIDE_UP,
            TechniqueType.SLIDE_DOWN,
            TechniqueType.HAMMER_ON,
            TechniqueType.PULL_OFF,
            TechniqueType.NATURAL_HARMONIC,
            TechniqueType.ARTIFICIAL_HARMONIC,
            TechniqueType.TAPPED_HARMONIC,
            TechniqueType.PINCH_HARMONIC,
        }

        painter.setPen(QColor(self.cfg.theme.COLOR_TECHNIQUE))
        font = QFont(self.cfg.NOTE_FONT_FAMILY, 8)
        painter.setFont(font)

        # 收集需要显示文字的技巧（排除纯图形化和P.M./Let Ring）
        text_techs = []
        for tech in note.techniques:
            if tech in SKIP_TECHS:
                continue  # 跳过P.M.和Let Ring，由虚线方法处理
            abbr = TECHNIQUE_ABBREVIATION.get(tech, tech.value)
            text_techs.append(abbr)

        if not text_techs:
            return

        tech_text = ''.join(text_techs)
        y_base = system.y_tab_top + note.string * self.cfg.TAB_LINE_SPACING

        # 长标记(>3)放在六线谱下方（P.M., Let Ring 等），避免遮挡品格数字;
        # 短标记放在品格数字右下方
        tech_y = system.y_tab_bottom + 4 if len(tech_text) > 3 else y_base + 12

        fm = QFontMetrics(font)
        fm.horizontalAdvance(tech_text)
        text_x = cx + (self.cfg.NOTE_FONT_SIZE // 2) + 2

        painter.drawText(QPoint(text_x, tech_y), tech_text)

    def _draw_system_technique_extensions(self, painter: QPainter, system: SystemLayout) -> None:
        """
        系统级技巧延长虚线绘制 - 在每行谱子(系统)的弦线区域上方统一绘制

        处理以下6种技巧的虚线+标签（参照GP5格式，画在弦线上方）:
          - P.M.   (闷音)
          - let ring (延音)
          - N.H.   (自然泛音)
          - A.H.   (人工泛音)
          - T.H.   (点弦泛音)
          - P.H.   (拾音泛音/泛音)

        绘制位置: y_tab_top 上方 4px（不遮挡品格数字）
        算法: 使用与P.M.相同的跨拍连线算法(_draw_dashed_extension_line)
        """
        # === 收集整个系统中所有有对应技巧的拍 ===
        tech_beats: dict[TechniqueType, list[tuple[int, int, int]]] = {
            TechniqueType.PALM_MUTE: [],  # P.M.
            TechniqueType.LET_RING: [],  # let ring
            TechniqueType.NATURAL_HARMONIC: [],  # N.H.
            TechniqueType.ARTIFICIAL_HARMONIC: [],  # A.H.
            TechniqueType.TAPPED_HARMONIC: [],  # T.H.
            TechniqueType.PINCH_HARMONIC: [],  # P.H.
        }

        for m_layout in system.measures:
            for b_layout in m_layout.beats:
                for note in b_layout.beat.notes:
                    if not note.techniques:
                        continue
                    beat_pos = (b_layout.x_start, b_layout.x_end, b_layout.x_center)
                    for tech in note.techniques:
                        if tech in tech_beats and beat_pos not in tech_beats[tech]:
                            tech_beats[tech].append(beat_pos)

        # === 在弦线区域上方绘制各技巧的虚线 ===
        base_y = system.y_tab_top - 4  # 弦线顶线上方4px

        # 定义每种技巧的标签和颜色
        tech_config = [
            (TechniqueType.PALM_MUTE, "P.M.", QColor(self.cfg.theme.COLOR_TECHNIQUE), False),
            (TechniqueType.LET_RING, "let ring", QColor("#60A5FA"), True),
            (TechniqueType.NATURAL_HARMONIC, "N.H.", QColor("#F59E0B"), False),
            (TechniqueType.ARTIFICIAL_HARMONIC, "A.H.", QColor("#F59E0B"), False),
            (TechniqueType.TAPPED_HARMONIC, "T.H.", QColor("#F59E0B"), False),
            (TechniqueType.PINCH_HARMONIC, "P.H.", QColor("#F59E0B"), False),
        ]

        row_offset = 0  # 每种技巧占一行，避免重叠
        for tech_type, label, color, strict in tech_config:
            if tech_beats[tech_type]:
                self._draw_dashed_extension_line(
                    painter,
                    tech_beats[tech_type],
                    system,
                    label,
                    color,
                    strict=strict,
                    base_y=base_y - row_offset * 12,  # 每行向上偏移12px
                )
                row_offset += 1

    def _draw_pm_letring_extensions(
        self, painter: QPainter, system: SystemLayout, m_layout: MeasureLayout
    ) -> None:
        """
        绘制P.M.(闷音)和Let Ring(延音)的延长虚线

        原理: 当连续多个音符都有P.M.或Let Ring技巧时，
              在这些音符下方画一条水平虚线表示该技巧持续有效，
              避免每个音符都重复标注"P.M."文字造成视觉混乱。

        参数:
            painter:  QPainter绑制对象
            system:  系统布局
            m_layout: 小节布局（包含该小节所有拍的列表）
        """
        # 收集所有有P.M.或Let Ring的**拍**及其完整位置信息
        # 使用 BeatLayout 的 x_start/x_end/x_center 确保虚线长度跟随时值
        pm_beats = []  # (x_start, x_end, x_center) 列表 — 每个有P.M.的拍
        lr_beats = []

        for b_layout in m_layout.beats:
            has_pm = any(
                note.has_technique(TechniqueType.PALM_MUTE) for note in b_layout.beat.notes
            )
            has_lr = any(note.has_technique(TechniqueType.LET_RING) for note in b_layout.beat.notes)
            if has_pm:
                pm_beats.append((b_layout.x_start, b_layout.x_end, b_layout.x_center))
            if has_lr:
                lr_beats.append((b_layout.x_start, b_layout.x_end, b_layout.x_center))

        # 为P.M.画虚线（基于拍的时值宽度）
        self._draw_dashed_extension_line(
            painter, pm_beats, system, "P.M.", QColor(self.cfg.theme.COLOR_TECHNIQUE)
        )

        # 为Let Ring画虚线（GP5格式：小写 + 跨拍连线，更严格断开）
        self._draw_dashed_extension_line(
            painter, lr_beats, system, "let ring", QColor("#60A5FA"), strict=True
        )

    def _draw_dashed_extension_line(
        self,
        painter: QPainter,
        beats_info: list,
        system: SystemLayout,
        label: str,
        color: QColor,
        strict: bool = False,
        base_y: int | None = None,
    ) -> None:
        """
        绘制通用虚线延长线（P.M. / Let Ring / 泛音 共用方法）

        严格参照 Guitar Pro 5 的渲染格式:
          - P.M.: "P.M.----|" (大写 + 虚线跨拍连接 + 停止竖线)
          - Let Ring: "let ring ----|" (小写 + 虚线跨拍连接 + 停止竖线)
          - N.H./A.H./T.H./P.H.: 同上格式，不同颜色
          - 每个**有技巧的拍**独立绘制: 标签文字 + 虚线
          - 虚线长度 = 该拍的 x_start → x_end（严格跟随时值）
          - **连续技巧拍**之间用虚线贯通连接（不重复画标签）
          - 每段连续技巧**末尾画竖线 |** 表示技巧停止

        参数:
            beats_info: [(x_start, x_end, x_center), ...] 有技巧的拍的位置列表
            system:     系统布局
            label:      文字标签("P.M." / "let ring" / "N.H." 等)
            color:      线条颜色
            strict:     是否使用严格断开模式(Let Ring=True, P.M.=False)
                        True: 间距 > 1.3倍标准拍间距就断开
                        False: 用相对阈值(1.3倍平均间距)，适合密集的P.M.
            base_y:     绘制Y坐标(None=默认在弦线下方y_tab_bottom+8，
                       传入数值则在指定Y位置绘制，用于弦线上方绘制)
        """
        if len(beats_info) < 1:
            return

        # 按X坐标排序
        beats_info.sort(key=lambda b: b[0])

        # 绘制Y坐标: 优先使用传入的base_y，否则默认在弦线下方
        line_y = base_y if base_y is not None else int(system.y_tab_bottom + 8)

        # 设置虚线样式
        pen = QPen(color, 1, Qt.DashLine)

        # 字体设置
        font = QFont(self.cfg.NOTE_FONT_FAMILY, 7)
        painter.setFont(font)

        # 计算连续判断阈值
        beat_spacing = self.cfg.NOTE_MIN_SPACING  # 标准拍间距基准值

        if strict:
            # 严格模式(Let Ring): 基于标准拍间距的绝对阈值
            # 间距 > 1.3倍NOTE_MIN_SPACING 就认为中间隔了非技巧拍，需要断开
            continuous_threshold = beat_spacing * 1.3  # 约34px
        else:
            # 宽松模式(P.M.): 基于实际数据相对阈值
            gaps = [beats_info[i + 1][0] - beats_info[i][2] for i in range(len(beats_info) - 1)]
            avg_gap = sum(gaps) / len(gaps) if gaps else beat_spacing
            continuous_threshold = avg_gap * 1.3

        i = 0
        while i < len(beats_info):
            seg_start = i

            # 找到当前连续段的结束位置
            seg_end = i
            while seg_end < len(beats_info) - 1:
                next_gap = beats_info[seg_end + 1][0] - beats_info[seg_end][2]
                if next_gap > continuous_threshold:
                    break
                seg_end += 1

            # === 绘制这一段连续的技巧拍 ===

            # 第一拍起始位置画标签文字
            sx = int(beats_info[seg_start][0])

            painter.setPen(color)
            painter.drawText(QPoint(sx + 2, line_y + 3), label)

            # 从标签后面画虚线到整段最后拍的结束位置
            # 根据标签文字长度计算虚线起点
            label_width = len(label) * 7 + 4  # 根据文字长度估算宽度(px)
            dash_start = sx + label_width
            total_end_x = int(beats_info[seg_end][1]) - 2

            if dash_start < total_end_x:
                painter.setPen(pen)
                painter.drawLine(dash_start, line_y, total_end_x, line_y)

            # 在整段末尾画竖线 | 表示停止
            painter.setPen(color)
            stop_x = int(beats_info[seg_end][1])
            painter.drawLine(stop_x, line_y - 4, stop_x, line_y + 5)

            i = seg_end + 1

    def _draw_stem(
        self, painter: QPainter, beat: GTPBeat, b_layout: BeatLayout, system: SystemLayout
    ) -> None:
        """
        绘制符干

        规则:
          - 全音符/二分音符：无符干（仅空心的全音符头在五线谱中适用，六线谱中省略）
          - 四分音符及更短：有符干
          - 符干方向：音符在第3弦及以上 → 向上；第4弦及以下 → 向下
          - 八分音符及更短：需要画符尾(beams)
        """
        if not beat.notes:
            return

        dur_val = beat.duration.value

        # 全音符和二分音符不画符干（六线谱简化处理）
        if dur_val <= NoteDuration.HALF.value:
            return

        # 确定符干方向和基准点
        beat.get_highest_string()
        beat.get_lowest_string()

        # 符干方向统一向下：所有符干从第6弦线下方延伸，符尾在下方
        # 这样视觉上更整洁，符尾不会与上方内容重叠
        stem_up = False

        if stem_up:
            # 符干向上：从第1弦线上方延伸
            stem_base_y = system.y_tab_top - 2
            stem_tip_y = stem_base_y - self.cfg.STEM_HEIGHT
        else:
            # 符干向下：从第6弦线下方延伸
            stem_base_y = system.y_tab_bottom + 2
            stem_tip_y = stem_base_y + self.cfg.STEM_HEIGHT

        # 绘制符干线
        pen = QPen(QColor(self.cfg.theme.COLOR_STEM), self.cfg.STEM_THICKNESS)
        painter.setPen(pen)
        painter.drawLine(b_layout.x_center, stem_base_y, b_layout.x_center, stem_tip_y)

        # 绘制符尾（八分音符及更短）
        if dur_val >= NoteDuration.EIGHTH.value:
            self._draw_beam_flags(painter, beat, b_layout, system, stem_up, stem_tip_y)

    def _draw_beam_flags(
        self,
        painter: QPainter,
        beat: GTPBeat,
        b_layout: BeatLayout,
        system: SystemLayout,
        stem_up: bool,
        stem_tip_y: int,
    ) -> None:
        """
        绘制符尾旗（单个拍独立时的斜杠标记）

        符尾规则:
          - 八分音符: 1条符尾旗
          - 十六分音符: 2条符尾旗（平行）
          - 三十二分音符: 3条符尾旗（平行）
          - 符尾方向与符干相反：向下符干 → 符尾向右上倾斜

        注意: MVP阶段暂不实现跨拍的连梁(beaming)，
              仅在每个拍上绘制独立的符尾旗。
        """
        dur_val = beat.duration.value
        cx = b_layout.x_center

        # 符尾数量：八分=1, 十六分=2, 三十二分=3
        flag_count = 0
        if dur_val >= NoteDuration.EIGHTH.value:
            flag_count += 1
        if dur_val >= NoteDuration.SIXTEENTH.value:
            flag_count += 1
        if dur_val >= NoteDuration.THIRTY_SECOND.value:
            flag_count += 1

        pen = QPen(QColor(self.cfg.theme.COLOR_BEAM), 1.5)  # 加粗使符尾更清晰
        painter.setPen(pen)

        beam_h = self.cfg.BEAM_HEIGHT
        flag_len = 7  # 符尾旗长度(px)，调整效果: 越长越明显

        for i in range(flag_count):
            offset = i * 4  # 多个符尾之间的垂直间距(px)
            if stem_up:
                # 向上的符尾旗：向右下方倾斜
                painter.drawLine(
                    cx, stem_tip_y + offset, cx + flag_len, stem_tip_y + offset + beam_h
                )
            else:
                # 向下的符尾旗：向右上方倾斜
                painter.drawLine(
                    cx, stem_tip_y - offset, cx + flag_len, stem_tip_y - offset - beam_h
                )

        # --- 附点标记：在符尾旁边画一个小圆点 ---
        if beat.is_dotted:
            dot_cx = cx + flag_len + 4  # 附点在符尾右侧
            dot_cy = stem_tip_y  # 与符干末端对齐
            painter.setPen(QPen(QColor(self.cfg.theme.COLOR_BEAM), 1.5))
            painter.setBrush(QColor(self.cfg.theme.COLOR_BEAM))
            painter.drawEllipse(dot_cx, dot_cy - 2, 4, 4)

    def _draw_rest_symbol(
        self, painter: QPainter, beat: GTPBeat, cx: int, system: SystemLayout
    ) -> None:
        """
        绘制休止符符号（根据时值绘制不同的休止符形状）

        休止符规则:
          - 全休止符: 悬挂在小节线上方的一条短粗线（类似"⊥"倒置）
          - 二分休止符: 坐落在小节线上的短粗线（类似"⊥"）
          - 四分休止符: 锯齿形/闪电形符号
          - 八分休止符: 类似数字"7"的圆形符号
          - 更短时值: 在四分休止符基础上加符尾

        参数:
            painter: QPainter绑制对象
            beat:    休止符拍的时值信息
            cx:      X坐标（拍中心）
            system:  系统布局（含Y坐标）
        """
        dur_val = beat.duration.value
        y_center = int(system.y_tab_top + 2.5 * self.cfg.TAB_LINE_SPACING)  # 六线谱中心Y(转int)
        y_top = system.y_tab_top
        y_bottom = system.y_tab_bottom

        painter.setPen(QPen(QColor(self.cfg.theme.COLOR_TEXT), 1.5))

        if dur_val == NoteDuration.WHOLE.value:
            # === 全休止符：悬挂式细线框 ===
            # 标准记谱法中全休止符悬挂在第四线下方
            # 六线谱简化为悬挂在最上方，用细线矩形而非填充块(避免看起来像□)
            rh = 6  # 矩形高度(px)，调整效果: 增大则符号更高
            rw = 8  # 矩形宽度(px)，调整效果: 增宽则符号更胖
            rx = cx - rw // 2
            ry = int(y_top) - rh - 2  # 悬挂在六线谱上方
            painter.setBrush(Qt.NoBrush)  # 不填充，只用细线边框
            painter.drawRect(rx, int(ry), rw, rh)

        elif dur_val == NoteDuration.HALF.value:
            # === 二分休止符：坐落式细线框 ===
            rh = 6  # 矩形高度(px)
            rw = 8  # 矩形宽度(px)
            rx = cx - rw // 2
            ry = int(y_top) + 2  # 坐落在六线谱顶部区域
            painter.setBrush(Qt.NoBrush)  # 不填充，只用细线边框
            painter.drawRect(rx, int(ry), rw, rh)

        elif dur_val == NoteDuration.QUARTER.value:
            # === 四分休止符：紧凑锯齿形 ===
            # 用折线模拟标准四分休止符，限制在六线谱中间区域（约20px高）
            r_top = y_center - 10  # 锯齿顶部Y
            r_bot = y_center + 10  # 锯齿底部Y
            points = [
                QPoint(cx + 3, r_top),  # 右上起点
                QPoint(cx - 3, r_top + 7),  # 左拐角
                QPoint(cx + 4, r_bot - 3),  # 右拐角
                QPoint(cx - 2, r_bot),  # 终点
            ]
            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i + 1])

        else:
            # === 八分及更短休止符：类似"7"的圆弧 + 符尾 ===
            # 画一个左开口的圆弧（类似"7"的上半部分）
            arc_r = 5  # 圆弧半径(px)
            arc_x = int(cx - arc_r)
            arc_y = int(y_center) - arc_r - 4  # 确保所有参数为int，避免PyQt5类型错误
            painter.drawArc(
                arc_x, arc_y, arc_r * 2, arc_r * 2, 180 * 16, 160 * 16
            )  # 从左边开始画约160度的弧

            # 画竖线向下（"7"的下半部分）
            painter.drawLine(cx + arc_r - 2, int(y_center) - 2, cx + arc_r - 2, int(y_center) + 10)

            # 附点标记
            if beat.is_dotted:
                dot_x = cx + arc_r + 3
                dot_y = y_center + 2
                painter.setBrush(QColor(self.cfg.theme.COLOR_TEXT))
                painter.drawEllipse(dot_x, dot_y, 3, 3)

        # 附点（全音符和二分音符也支持附点）
        if beat.is_dotted and dur_val <= NoteDuration.HALF.value:
            dot_x = cx + 6
            dot_y = (y_top + y_bottom) // 2
            painter.setBrush(QColor(self.cfg.theme.COLOR_TEXT))
            painter.drawEllipse(dot_x, dot_y, 3, 3)

    # ============================================================
    # 和弦名渲染 (v1.4.0 新增: 从主程序 overlay 迁移)
    # ============================================================

    @staticmethod
    def _first_chord_in_sequence(beats: list[BeatLayout]) -> list[BeatLayout]:
        """
        [v1.4.0] 在一段连续相同和弦中只保留第一个 BeatLayout

        用途: 避免一段 C C C 在每个 beat 上方都画 "C", 视觉冗余.
              与 GP 原版行为一致 (只在新一段首个 chord 上方显示).

        参数:
            beats: BeatLayout 列表 (来自 m_layout.beats)

        返回:
            过滤后的 BeatLayout 列表, 保留每段相同 chord.name 的第一个
        """
        result: list[BeatLayout] = []
        prev_name: str | None = None
        for b_layout in beats:
            beat = b_layout.beat
            chord = getattr(beat, 'chord', None)
            if chord is None:
                continue
            if chord.name != prev_name:
                result.append(b_layout)
                prev_name = chord.name
        return result

    def _draw_chord_names(
        self, painter: QPainter, system: SystemLayout, m_layout: MeasureLayout
    ) -> None:
        """
        [v1.4.0] 绘制当前小节的所有 chord 名称 (在谱表顶部上方)

        位置:
          - Y: system.y_top - 16 (info bar 外, system 顶部上方 16px)
          - X: 水平居中于 beat.x_center

        样式:
          - 文字色: theme.COLOR_TECHNIQUE (主色, dark=#F97316 / light=#D97706)
          - 背景色: theme.COLOR_HEADER_BG (浅色背景框, dark=#252538 / light=#F8F9FA)
          - 字号: 12pt Bold
          - 圆角矩形背景 4px radius

        触发条件: track 中存在 beat.chord (音轨中有就画, 没有不管)
        """
        filtered = self._first_chord_in_sequence(m_layout.beats)
        if not filtered:
            return

        # Y 位置: system 顶部上方 16px
        text_y = int(system.y_top) - 16
        # 字号
        font = QFont(self.cfg.NOTE_FONT_FAMILY, 12, QFont.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()

        # 颜色
        text_color = QColor(self.cfg.theme.COLOR_TECHNIQUE)
        bg_color = QColor(self.cfg.theme.COLOR_HEADER_BG)
        bg_color.setAlpha(220)

        for b_layout in filtered:
            chord = b_layout.beat.chord
            if chord is None:
                continue
            chord_name = chord.name
            text_width = fm.horizontalAdvance(chord_name)
            x_center = int(b_layout.x_center)
            # 背景框: 居中于 x_center, 上下留 4px padding
            bg_rect = QRect(
                x_center - text_width // 2 - 4,
                text_y - fm.height() + 4,
                text_width + 8,
                fm.height() + 2,
            )
            painter.fillRect(bg_rect, bg_color)
            # 圆角边框
            pen = QPen(text_color, 1)
            painter.setPen(pen)
            painter.drawRoundedRect(bg_rect, 4, 4)
            # 文字
            painter.setPen(text_color)
            painter.drawText(
                x_center - text_width // 2,
                text_y,
                chord_name,
            )

    # ============================================================
    # 页码绘制
    # ============================================================

    @staticmethod
    def _draw_page_number(painter: QPainter, text: str, width: int, height: int) -> None:
        """绘制页码"""
        painter.setPen(QColor("#666666"))
        painter.setFont(QFont("Arial", 9))
        painter.drawText(QRect(0, height - 25, width, 20), Qt.AlignCenter, text)


# ============================================================
# 便捷函数
# ============================================================


def render_gtp(file_path: str, track_index: int = 0) -> list[QPixmap]:
    """
    便捷函数：解析并渲染Guitar Pro文件

    参数:
        file_path:    .gp3/.gp4/.gp5/.gpx 文件路径
        track_index:  要渲染的音轨索引（默认0=第一条）

    返回:
        QPixmap列表，每页一张图片

    示例:
        >>> from gtp_engine.renderer import render_gtp
        >>> pages = render_gtp("song.gp5", track_index=2)
        >>> print(f"共 {len(pages)} 页")
    """
    from ..parser import parse_score
    song = parse_score(file_path)
    renderer = TabRenderer()
    return renderer.render(song, track_index=track_index)


def render_musicxml(file_path: str, track_index: int = 0) -> list[QPixmap]:
    """Parse and render a MusicXML (including compressed ``.mxl``) score."""
    return render_gtp(file_path, track_index)
