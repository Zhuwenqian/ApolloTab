"""
============================================================
文件名: constants.py
功能描述: GTP引擎全局常量定义 - 包含调弦、时值映射、渲染主题、渲染参数等
         所有可调整的渲染参数都在此集中管理，修改后全局生效
         v1.1.5 新增: ThemeConfig 支持运行时注册自定义主题颜色，
         使 TAB Score Viewer 的用户主题扩展功能能够同步应用到 ApolloTab 渲染引擎

创建日期: 2026-06-06
最后更新: 2026-06-30 (v1.3.0: ThemeConfig 新增 register_theme() 运行时主题注册接口;
                   v1.1.0: 新增 BendType/BendStyle/VibratoType 枚举)
依赖库: 无（纯常量定义模块）
============================================================
"""

from enum import Enum, IntEnum

# ============================================================
# 标准调弦定义（MIDI音高值）
# ============================================================


class StandardTunings:
    """
    常用吉他调弦方案（MIDI音高值）
    弦序：索引0 = 1弦(最细/高音E)，索引5 = 6弦(最粗/低音E)
    """

    STANDARD = (64, 59, 55, 50, 45, 40)  # 标准调弦 EADGBE
    DROP_D = (64, 59, 55, 50, 45, 38)  # Drop D
    OPEN_G = (62, 59, 55, 50, 43, 38)  # Open G (DGDGBD)
    OPEN_D = (62, 57, 50, 50, 45, 38)  # Open D (DADF#AD)
    DADGAD = (62, 57, 50, 45, 38, 45)  # DADGAD
    HALF_STEP_DOWN = (63, 58, 54, 49, 44, 39)  # 降半音调弦


# ============================================================
# 时值枚举（与 guitarpro.Duration.value 对应）
# ============================================================


class NoteDuration(IntEnum):
    """音符时值枚举 - 数值为 guitarpro Duration 的 value 字段"""

    WHOLE = 1  # 全音符
    HALF = 2  # 二分音符
    QUARTER = 4  # 四分音符
    EIGHTH = 8  # 八分音符
    SIXTEENTH = 16  # 十六分音符
    THIRTY_SECOND = 32  # 三十二分音符


# 时值 → 以四分音符为基准的时长比值
DURATION_RATIO = {
    1: 4.0,  # 全音符 = 4个四分音符
    2: 2.0,  # 二分音符 = 2个四分音符
    4: 1.0,  # 四分音符 = 1
    8: 0.5,  # 八分音符 = 0.5
    16: 0.25,  # 十六分音符 = 0.25
    32: 0.125,  # 三十二分音符 = 0.125
}

# 附点时值乘数（附点增加原时值的50%）
DOTTED_MULTIPLIER = 1.5

# ============================================================
# 技巧类型枚举（可扩展）
# ============================================================


class TechniqueType(Enum):
    """演奏技巧类型枚举 - 用于标注和渲染"""

    HAMMER_ON = "Hammer On"  # 击弦 (hammer)
    PULL_OFF = "Pull Off"  # 勾弦 (pull-off，通过slide反向推断)
    SLIDE_UP = "Slide Up"  # 上滑音 (slide into/from above)
    SLIDE_DOWN = "Slide Down"  # 下滑音 (slide into/from below)
    BEND = "Bend"  # 推弦 (bend)
    VIBRATO = "Vibrato"  # 颤音 (vibrato)
    PALM_MUTE = "P.M."  # 闷音 (palm mute)
    STACCATO = "Staccato"  # 断奏 (staccato)
    GHOST_NOTE = "Ghost"  # 幽灵音 (ghost note)
    LET_RING = "Let Ring"  # 延音 (let ring)
    NATURAL_HARMONIC = "N.H."  # 自然泛音
    ARTIFICIAL_HARMONIC = "A.H."  # 人工泛音
    TAPPED_HARMONIC = "T.H."  # 点弦泛音
    PINCH_HARMONIC = "P.H."  # 拨弦泛音
    TREMOLO_PICKING = "Trem.Pick."  # 震音拨弦
    TRILL = "Trill"  # 颤音(trill)
    GRACE_NOTE = "Grace"  # 装饰音(grace note)
    ACCENTUATED = ">"  # 重音
    SLAP = "Slap"  # 拍弦
    POP = "Pop"  # 勾弦(贝斯)


# 技巧 → 渲染时的缩写文本映射
TECHNIQUE_ABBREVIATION = {
    TechniqueType.HAMMER_ON: "H",
    TechniqueType.PULL_OFF: "P",
    TechniqueType.SLIDE_UP: "s",
    TechniqueType.SLIDE_DOWN: "S",
    TechniqueType.BEND: "B",
    TechniqueType.VIBRATO: "~",
    TechniqueType.PALM_MUTE: "P.M.",
    TechniqueType.STACCATO: ".",
    TechniqueType.GHOST_NOTE: "(",
    TechniqueType.LET_RING: "Let Ring",
    TechniqueType.NATURAL_HARMONIC: "N.H.",
    TechniqueType.ARTIFICIAL_HARMONIC: "A.H.",
    TechniqueType.TAPPED_HARMONIC: "T.H.",
    TechniqueType.PINCH_HARMONIC: "P.H.",
    TechniqueType.TREMOLO_PICKING: "Trem.P.",
    TechniqueType.TRILL: "tr",
    TechniqueType.ACCENTUATED: ">",
}

# ============================================================
# 渲染主题系统（v0.2.4）
# ============================================================


class ThemeConfig:
    """
    六线谱渲染主题配置类

    功能:
      预定义多套配色方案（黑白/深色等），
      支持运行时动态切换，统一管理所有颜色参数。

    设计模式:
      - 策略模式(Strategy): 每个主题是一个独立的颜色策略
      - 工厂方法(Factory): 通过预设名称快速获取主题实例

    使用示例:
        # 获取默认黑白主题
        theme = ThemeConfig.get_theme("light")

        # 获取深色主题
        dark_theme = ThemeConfig.get_theme("dark")

        # 应用到渲染器
        renderer.set_theme(dark_theme)

    可用主题名称:
      - "light":   黑白配色（谱子黑色，背景白色），适合打印/白天使用
      - "dark":    深色配色（暗背景亮文字），适合夜间/护眼使用

    扩展新主题:
      1. 在 PRESET_THEMES 字典中添加新的主题定义
      2. 或直接实例化 ThemeConfig 并传入自定义颜色字典
    """

    # ===== 内置预设主题定义 =====
    # 每个主题包含所有渲染所需的颜色参数
    # 格式: { 参数名: 颜色值(十六进制字符串) }
    PRESET_THEMES = {
        "light": {
            # --- 基础色彩 ---
            "COLOR_BG": "#FFFFFF",  # 背景色: 纯白
            "COLOR_TAB_LINE": "#000000",  # 弦线颜色: 黑色
            "COLOR_TEXT": "#000000",  # 文字颜色(品格数字/标题): 黑色
            "COLOR_BARLINE": "#333333",  # 小节线颜色: 深灰
            "COLOR_STEM": "#000000",  # 符干颜色: 黑色
            "COLOR_BEAM": "#000000",  # 符尾颜色: 黑色
            # --- 强调色彩 ---
            "COLOR_TECHNIQUE": "#D97706",  # 技巧标记颜色: 深橙色(打印友好)
            "COLOR_TRACK_NAME": "#1E40AF",  # 音轨名称颜色: 深蓝色
            "COLOR_REPEAT": "#047857",  # 重复记号颜色: 深绿色
            # --- 特殊元素 ---
            "COLOR_HEADER_BG": "#F8F9FA",  # 头部信息区背景: 浅灰白
            "COLOR_PAGE_NUMBER": "#666666",  # 页码颜色: 中灰
        },
        "dark": {
            # --- 基础色彩 ---
            "COLOR_BG": "#1E1E2E",  # 背景色: 深蓝灰
            "COLOR_TAB_LINE": "#888888",  # 弦线颜色: 浅灰
            "COLOR_TEXT": "#E2E8F0",  # 文字颜色: 亮白灰
            "COLOR_BARLINE": "#AAAAAA",  # 小节线颜色: 中浅灰
            "COLOR_STEM": "#CCCCCC",  # 符干颜色: 浅灰白
            "COLOR_BEAM": "#CCCCCC",  # 符尾颜色: 浅灰白
            # --- 强调色彩 ---
            "COLOR_TECHNIQUE": "#F97316",  # 技巧标记颜色: 亮橙色
            "COLOR_TRACK_NAME": "#60A5FA",  # 音轨名称颜色: 亮蓝色
            "COLOR_REPEAT": "#10B981",  # 重复记号颜色: 亮绿色
            # --- 特殊元素 ---
            "COLOR_HEADER_BG": "#252538",  # 头部信息区背景: 深蓝紫
            "COLOR_PAGE_NUMBER": "#888888",  # 页码颜色: 中灰
        },
    }

    # 默认主题名称
    DEFAULT_THEME_NAME = "dark"  # 保持向后兼容，默认使用深色主题

    def __init__(self, colors: dict = None, theme_name: str = "custom"):
        """
        初始化主题配置

        参数:
            colors:      颜色字典，包含所有 COLOR_* 参数
                         None 则使用深色主题作为默认值
            theme_name:  主题名称标识符，用于调试和日志输出

        注意:
          如果传入的 colors 缺少某些字段，
          会自动用深色主题的对应值填充（向后兼容保证）。
        """
        self.name = theme_name

        # 获取完整的颜色集合（确保所有必需字段都存在）
        default_colors = self.PRESET_THEMES.get("dark", {})

        if colors:
            # 合并用户提供的颜色和默认值（用户值优先）
            self._colors = {**default_colors, **colors}
        else:
            # 使用默认深色主题
            self._colors = dict(default_colors)

    @classmethod
    def get_theme(cls, name: str) -> 'ThemeConfig':
        """
        工厂方法：根据名称获取预定义主题实例

        参数:
            name: 主题名称 ("light" | "dark")

        返回:
            ThemeConfig 实例

        异常:
            ValueError: 当主题名称不存在时抛出

        示例:
            >>> light_theme = ThemeConfig.get_theme("light")
            >>> print(light_theme.COLOR_BG)  # 输出: "#FFFFFF"

            >>> dark_theme = ThemeConfig.get_theme("dark")
            >>> print(dark_theme.COLOR_BG)  # 输出: "#1E1E2E"
        """
        name_lower = name.lower().strip()

        if name_lower not in cls.PRESET_THEMES:
            available = ", ".join(cls.PRESET_THEMES.keys())
            raise ValueError(
                f"未知主题名称: '{name}'\n"
                f"可用主题: [{available}]\n"
                f"提示: 使用 ThemeConfig.list_themes() 查看所有可用主题"
            )

        return cls(colors=cls.PRESET_THEMES[name_lower], theme_name=name_lower)

    @classmethod
    def list_themes(cls) -> list[str]:
        """
        列出所有可用的预设主题名称

        返回:
            主题名称列表，如 ["light", "dark"]

        示例:
            >>> themes = ThemeConfig.list_themes()
            >>> for t in themes:
            ...     print(t)
            light
            dark
        """
        return list(cls.PRESET_THEMES.keys())

    @classmethod
    def register_theme(cls, name: str, colors: dict) -> 'ThemeConfig':
        """
        运行时注册自定义主题（v1.1.5 新增）

        功能:
          将用户定义的颜色字典注册到 PRESET_THEMES，
          使 TabRenderer.set_theme(name) / GTPPlayer.set_theme(name) 可以通过字符串名称使用自定义主题。
          注册时会自动用深色主题的默认值填充缺失的颜色键，保证渲染完整性。

        参数:
            name:   主题唯一标识符（会被 lower().strip() 规范化）
            colors: 颜色字典，键为 COLOR_* 格式，值为十六进制颜色字符串

        返回:
            注册后的 ThemeConfig 实例

        注意:
          - 为保护内置主题，name 为 "dark" 或 "light" 时会被忽略（返回内置主题实例）
          - 颜色字典缺失的键会自动使用深色主题默认值填充

        使用示例:
            >>> custom_colors = {"COLOR_BG": "#FFFDE7", "COLOR_TEXT": "#212121"}
            >>> ThemeConfig.register_theme("sepia", custom_colors)
            ThemeConfig(name='sepia', colors=10 params)
            >>> renderer.set_theme("sepia")  # 通过名称使用自定义主题
        """
        name_lower = name.lower().strip()

        # 保护内置主题，不允许覆盖 dark/light
        if name_lower in ("dark", "light"):
            print(f"[ThemeConfig] 忽略对内置主题 '{name_lower}' 的覆盖注册")
            return cls.get_theme(name_lower)

        # 用深色默认值填充缺失键，保证渲染完整性
        default_colors = cls.PRESET_THEMES.get("dark", {})
        merged_colors = {**default_colors, **colors}

        # 注册到预设表
        cls.PRESET_THEMES[name_lower] = merged_colors

        return cls(colors=merged_colors, theme_name=name_lower)

    @classmethod
    def unregister_theme(cls, name: str) -> bool:
        """
        运行时注销自定义主题（v1.1.5 新增）

        参数:
            name: 要注销的主题名称

        返回:
            True: 注销成功；False: 主题是内置主题或不存在，未执行注销
        """
        name_lower = name.lower().strip()
        if name_lower in ("dark", "light"):
            return False
        if name_lower not in cls.PRESET_THEMES:
            return False
        del cls.PRESET_THEMES[name_lower]
        return True

    @property
    def is_dark(self) -> bool:
        """判断当前是否为深色主题"""
        return self.name == "dark"

    @property
    def is_light(self) -> bool:
        """判断当前是否为浅色（黑白）主题"""
        return self.name == "light"

    def __getattr__(self, name: str):
        """
        动态属性访问 - 支持通过 instance.COLOR_BG 的方式获取颜色值

        原理:
          Python 的 __getattr__ 方法在实例属性不存在时被调用，
          这里用于将字典中的颜色键转换为类属性访问方式。

        参数:
            name: 属性名称 (如 "COLOR_BG", "COLOR_TEXT" 等)

        返回:
            对应的颜色值(十六进制字符串)，如 "#FFFFFF"

        异常:
            AttributeError: 当请求的颜色名称不存在时抛出
        """
        if name in self._colors:
            return self._colors[name]

        # 提供友好的错误提示
        available_colors = ", ".join(sorted(self._colors.keys()))
        raise AttributeError(
            f"未知的颜色属性: '{name}'\n"
            f"可用属性: [{available_colors}]\n"
            f"提示: 当前主题 '{self.name}' 包含 {len(self._colors)} 个颜色参数"
        )

    def __repr__(self) -> str:
        """返回主题的可读表示"""
        return f"ThemeConfig(name='{self.name}', colors={len(self._colors)} params)"

    def to_dict(self) -> dict:
        """
        导出为普通字典（用于序列化或自定义修改）

        返回:
          颜色字典的副本（修改不影响原对象）
        """
        return dict(self._colors)


# ============================================================
# 渲染参数常量（可调整）
# ============================================================


class RenderConfig:
    """
    六线谱渲染配置参数
    所有数值单位为像素(px)，可根据显示效果调整

    主题支持（v0.2.4新增）:
      - 通过 theme 属性可获取/设置 ThemeConfig 实例
      - 所有 COLOR_* 参数现在从 theme 对象读取
      - 支持运行时切换主题而不重新创建 RenderConfig

    使用示例:
        config = RenderConfig()  # 使用默认深色主题

        # 切换到黑白主题
        config.theme = ThemeConfig.get_theme("light")

        # 或者直接在初始化时指定
        config = RenderConfig(theme=ThemeConfig.get_theme("light"))
    """

    # --- 画布尺寸 ---
    # A4纸张标准比例: 210mm × 297mm, 宽高比 = √2 ≈ 1.41421356
    # 以下默认值保持精确的A4比例，确保打印输出不变形
    # 如需更高分辨率(如300DPI打印), 可设为 page_width=2480, page_height=3508
    PAGE_WIDTH_PX = 1000  # 每页渲染宽度(px) - 调整效果: 越宽每行容纳越多音符
    PAGE_HEIGHT_PX = 1414  # 每页渲染高度(px) - A4标准比例(=width×√2), 调整效果: 越高每页容纳更多行
    PAGE_MARGIN_TOP = 100  # 页面上边距(px) - 用于标题和调号信息区
    # v1.4.0: 60→100 给第一行六线谱上方留出 ~40px 空间
    #        容纳 chord 名 (12pt Bold + 圆角背景, 约 20px 高)
    PAGE_MARGIN_LEFT = 40  # 页面左边距(px)
    PAGE_MARGIN_RIGHT = 40  # 页面右边距(px)
    PAGE_MARGIN_BOTTOM = 40  # 页面下边距(px)

    # --- 六线谱线 ---
    TAB_LINE_SPACING = 14  # 弦线间距(px) - 调整效果: 越大六线谱越高，品格数字越清晰
    TAB_LINE_WIDTH_PER_STRING = 22  # 每根弦线分配的水平宽度(px) - 用于品格数字绘制区域
    TAB_LINE_THICKNESS = 1  # 弦线粗细(px)

    # --- 音符/品格数字 ---
    NOTE_FONT_SIZE = 10  # 品格数字字体大小(px) - 调整效果: 越大数字越清晰但占用空间多
    NOTE_FONT_FAMILY = "Arial"  # 品格数字字体族 - 推荐使用等宽字体保证对齐
    NOTE_MIN_SPACING = (
        26  # 相邻拍之间的最小水平间距(px) - 调整效果: 越小越紧凑，越大越宽松(推荐18-26)
    )
    NOTE_EXTRA_WIDTH_PER_CHAR = 7  # 多位数品格数字的额外宽度(px/字符) - 如品10比品0多占10px

    # --- 符干与符尾 ---
    STEM_HEIGHT = 18  # 符干高度(px) - 从六线谱向上/下延伸
    STEM_THICKNESS = 1  # 符干粗细(px)
    BEAM_HEIGHT = 6  # 符尾横杠高度(px)
    BEAM_SLOPE_MAX = 0.3  # 笔尾最大倾斜斜率

    # --- 小节线 ---
    BARLINE_THICKNESS = 1.5  # 小节线粗细(px)
    BARLINE_HEIGHT_EXTEND = 6  # 小节线超出六线谱上下延伸量(px)
    MEASURE_PADDING_LEFT = 10  # 小节左侧内边距(px)
    MEASURE_PADDING_RIGHT = 12  # 小节右侧内边距(px)

    # --- 调号/拍号/BPM 信息区 ---
    INFO_SECTION_HEIGHT = 50  # 顶部信息区高度(px)
    INFO_FONT_SIZE = 13  # 信息文字大小(px)
    TRACK_NAME_FONT_SIZE = 16  # 音轨名称字体大小(px)

    # --- 行间距 ---
    LINE_SPACING = 30  # 两行六线谱之间的垂直间距(px) - 含符干符尾空间
    SYSTEM_SPACING = 20  # 不同系统(组)之间的额外间距(px)

    def __init__(self, theme: ThemeConfig = None):
        """
        初始化渲染配置

        参数:
            theme: ThemeConfig 实例，None则使用默认深色主题

        注意:
          为了保持向后兼容，旧的 COLOR_* 类属性仍然保留为默认值（深色主题），
          但实际渲染时应优先使用 theme 对象的颜色值。
          新代码建议通过 self.theme.COLOR_* 访问颜色。
        """
        # 主题配置（核心改进点）
        self._theme = theme or ThemeConfig.get_theme(ThemeConfig.DEFAULT_THEME_NAME)

    @property
    def theme(self) -> ThemeConfig:
        """
        获取当前主题配置

        返回:
            ThemeConfig 实例，包含所有颜色参数
        """
        return self._theme

    @theme.setter
    def theme(self, value: ThemeConfig) -> None:
        """
        设置当前主题配置

        参数:
            value: 新的 ThemeConfig 实例

        效果:
          立即应用新主题到后续所有渲染操作。
          已渲染的图像不受影响（需要重新调用 render()）。

        示例:
            >>> config = RenderConfig()
            >>> config.theme = ThemeConfig.get_theme("light")  # 切换到黑白主题
        """
        if not isinstance(value, ThemeConfig):
            raise TypeError(
                f"theme 必须是 ThemeConfig 实例，收到: {type(value).__name__}\n"
                f"正确用法: config.theme = ThemeConfig.get_theme('light')"
            )
        self._theme = value

    # === 向后兼容的颜色属性（不推荐在新代码中使用）===
    # 这些属性会从当前 theme 对象中读取实际值，
    # 保证旧代码 self.cfg.COLOR_BG 仍然可以正常工作。

    @property
    def COLOR_BG(self) -> str:
        """背景色 - 从当前主题读取"""
        return self._theme.COLOR_BG

    @property
    def COLOR_TAB_LINE(self) -> str:
        """六线谱线颜色 - 从当前主题读取"""
        return self._theme.COLOR_TAB_LINE

    @property
    def COLOR_TEXT(self) -> str:
        """文字颜色 - 从当前主题读取"""
        return self._theme.COLOR_TEXT

    @property
    def COLOR_BARLINE(self) -> str:
        """小节线颜色 - 从当前主题读取"""
        return self._theme.COLOR_BARLINE

    @property
    def COLOR_STEM(self) -> str:
        """符干颜色 - 从当前主题读取"""
        return self._theme.COLOR_STEM

    @property
    def COLOR_BEAM(self) -> str:
        """符尾颜色 - 从当前主题读取"""
        return self._theme.COLOR_BEAM

    @property
    def COLOR_TECHNIQUE(self) -> str:
        """技巧标记颜色 - 从当前主题读取"""
        return self._theme.COLOR_TECHNIQUE

    @property
    def COLOR_TRACK_NAME(self) -> str:
        """音轨名称颜色 - 从当前主题读取"""
        return self._theme.COLOR_TRACK_NAME

    @property
    def COLOR_REPEAT(self) -> str:
        """重复记号颜色 - 从当前主题读取"""
        return self._theme.COLOR_REPEAT

    @property
    def COLOR_HEADER_BG(self) -> str:
        """头部信息区背景色 - 从当前主题读取"""
        return getattr(self._theme, 'COLOR_HEADER_BG', '#252538')

    @property
    def COLOR_PAGE_NUMBER(self) -> str:
        """页码颜色 - 从当前主题读取"""
        return getattr(self._theme, 'COLOR_PAGE_NUMBER', '#888888')


# ============================================================
# 渲染模式枚举（v0.4.0 新增 - GP7/GP8 多谱表支持预留）
# ============================================================


class RenderMode(Enum):
    """
    渲染模式枚举 - 用于 TabRenderer 控制渲染哪些谱表

    设计目的:
      GP7/GP8 文件可包含多种谱表(五线谱/TAB/斜线谱/简谱)，
      本程序当前仅渲染 TAB 谱表，但通过此枚举预留扩展接口，
      未来可在 TabRenderer 子类中实现其他谱表的渲染。

    使用示例:
        # 当前只支持 TAB 模式
        renderer = TabRenderer()
        renderer.render_mode = RenderMode.TAB_ONLY
        # 未来扩展(尚未实现):
        # renderer.render_mode = RenderMode.TAB_AND_STANDARD

    扩展指南:
      要新增渲染模式，需在 TabRenderer 子类中重写以下钩子方法:
        - _draw_standard_notation()  五线谱渲染(预留)
        - _draw_numbered_notation()  简谱渲染(预留)
        - _draw_slash_notation()     斜线谱渲染(预留)
    """

    TAB_ONLY = 1  # 仅渲染六线谱(当前唯一支持的默认模式)
    TAB_AND_STANDARD = 2  # 六线谱+五线谱(预留接口，未来扩展)
    TAB_AND_NUMBERED = 3  # 六线谱+简谱(预留接口，GP8 新功能)
    TAB_AND_SLASH = 4  # 六线谱+斜线谱(预留接口)
    ALL_STAVES = 5  # 所有谱表(预留接口，未来扩展)


# ============================================================
# 推弦/滑弦/揉弦相关枚举 (v1.1.0 - 对齐 alphaTab)
# ============================================================
#
# 推弦数值单位约定: 1/4 半音 (quarter semitone)
#   1 = 1/4 半音 = +0.25 半音
#   2 = 1/2 半音 = +0.5 半音
#   3 = 3/4 半音 = +0.75 半音
#   4 = Full bend = +1 半音 (在 12 半音 Pitch Bend Range 下,
#                              Full 实际对应 +2 半音 = 8192 + 8192/12*4 ≈ 9557)
#
# 调整效果: 修改这里的值会改变 midi_converter.get_pitch_wheel()
#          的输出范围，进而影响所有推弦音的实际音高。
#          调高 BendType.FULL 数值可加大最大推弦幅度。


class BendType(Enum):
    """推弦类型枚举(对齐 alphaTab BendType)"""

    CUSTOM = 0  # 自定义(摇把、组合推弦等无法归类的)
    BEND = 1  # 推弦后释放(单段)
    BEND_RELEASE = 2  # 推弦后释放(单段 + 释放)
    PREBEND = 3  # 预推弦(音符开始前已推到位)
    PREBEND_RELEASE = 4  # 预推弦 + 释放
    GRADUAL_RELEASE = 5  # 渐变释放(常见于长推弦尾音)
    IMMEDIATE_RELEASE = 6  # 立即释放(短促推弦)
    HELD = 7  # 保持(推弦后保持)
    REBEND = 8  # 二次推弦(从推弦状态再次推)


class BendStyle(Enum):
    """推弦风格枚举(影响渲染曲线弧度,不对应音频差异)"""

    DEFAULT = 0  # 默认平滑曲线
    GRADUAL = 1  # 渐变(长推弦)
    FAST = 2  # 快速(短促推弦)


class VibratoType(Enum):
    """揉弦类型枚举(对齐 alphaTab VibratoType)"""

    NONE = 0  # 无揉弦
    SLIGHT = 1  # 轻微揉弦(振幅小,周期快)
    WIDE = 2  # 大幅揉弦(振幅大,周期慢)


# ============================================================
# 弦序辅助函数
# ============================================================


def get_string_name(string_index: int) -> str:
    """
    根据弦索引获取弦名称
    参数: string_index - 弦索引(0-5, 0=1弦高音E)
    返回: 弦名称字符串，如 '1弦(E)', '6弦(E)'
    """
    string_names = ["1弦(E)", "2弦(B)", "3弦(G)", "4弦(D)", "5弦(A)", "6弦(E)"]
    if 0 <= string_index < len(string_names):
        return string_names[string_index]
    return f"{string_index + 1}弦"
