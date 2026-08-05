"""
ApolloTab/tests/test_constants.py

utils/constants 纯逻辑测试 (ApolloTab v1.4.1)

覆盖范围:
  - StandardTunings: 调弦 MIDI 值
  - NoteDuration / DURATION_RATIO / DOTTED_MULTIPLIER
  - TechniqueType / TECHNIQUE_ABBREVIATION
  - ThemeConfig: get_theme / list_themes / register_theme / unregister_theme
                / is_dark / is_light / __getattr__ / to_dict / __repr__
  - RenderConfig: 默认主题 / theme setter 类型校验 / COLOR_* 透传
  - BendType / BendStyle / VibratoType / RenderMode 枚举
  - get_string_name: 弦序名

运行命令: python -m pytest ApolloTab/tests/test_constants.py -q
"""

from __future__ import annotations

import pytest

from ApolloTab.utils.constants import (
    DOTTED_MULTIPLIER,
    DURATION_RATIO,
    TECHNIQUE_ABBREVIATION,
    BendStyle,
    BendType,
    NoteDuration,
    RenderConfig,
    RenderMode,
    StandardTunings,
    TechniqueType,
    ThemeConfig,
    VibratoType,
    get_string_name,
)

# ============================================================
# StandardTunings
# ============================================================


class TestStandardTunings:
    """标准调弦 MIDI 音高"""

    def test_standard_tuning_six_strings(self):
        assert len(StandardTunings.STANDARD) == 6

    def test_standard_tuning_values(self):
        """标准 EADGBE: 1弦E4=64 ... 6弦E2=40"""
        assert StandardTunings.STANDARD == (64, 59, 55, 50, 45, 40)

    def test_drop_d_changes_only_sixth(self):
        """Drop D 仅 6 弦降为 D2=38, 其余同标准"""
        assert StandardTunings.DROP_D[5] == 38
        assert StandardTunings.DROP_D[:5] == StandardTunings.STANDARD[:5]

    def test_half_step_down_one_below_standard(self):
        """降半音: 每根弦比标准低 1"""
        for std, hsd in zip(StandardTunings.STANDARD, StandardTunings.HALF_STEP_DOWN, strict=True):
            assert hsd == std - 1

    def test_all_tunings_are_tuples(self):
        """调弦定义应为不可变元组"""
        for name in ("STANDARD", "DROP_D", "OPEN_G", "OPEN_D", "DADGAD", "HALF_STEP_DOWN"):
            tuning = getattr(StandardTunings, name)
            assert isinstance(tuning, tuple)


# ============================================================
# NoteDuration / DURATION_RATIO
# ============================================================


class TestNoteDuration:
    """时值枚举与时长比值"""

    @pytest.mark.parametrize(
        "duration,value",
        [
            (NoteDuration.WHOLE, 1),
            (NoteDuration.HALF, 2),
            (NoteDuration.QUARTER, 4),
            (NoteDuration.EIGHTH, 8),
            (NoteDuration.SIXTEENTH, 16),
            (NoteDuration.THIRTY_SECOND, 32),
        ],
    )
    def test_enum_values(self, duration, value):
        assert duration.value == value

    @pytest.mark.parametrize(
        "key,expected",
        [
            (1, 4.0),
            (2, 2.0),
            (4, 1.0),
            (8, 0.5),
            (16, 0.25),
            (32, 0.125),
        ],
    )
    def test_duration_ratio(self, key, expected):
        assert DURATION_RATIO[key] == expected

    def test_dotted_multiplier(self):
        assert DOTTED_MULTIPLIER == 1.5

    def test_note_duration_is_int_enum(self):
        """IntEnum 保证与 int 比较兼容"""
        assert NoteDuration.QUARTER == 4
        assert isinstance(NoteDuration.QUARTER, int)


# ============================================================
# TechniqueType / TECHNIQUE_ABBREVIATION
# ============================================================


class TestTechniqueType:
    """技巧枚举与缩写映射"""

    def test_hammer_on_value(self):
        assert TechniqueType.HAMMER_ON.value == "Hammer On"

    def test_palm_mute_value(self):
        assert TechniqueType.PALM_MUTE.value == "P.M."

    def test_abbreviation_hammer_on(self):
        assert TECHNIQUE_ABBREVIATION[TechniqueType.HAMMER_ON] == "H"

    def test_abbreviation_pull_off(self):
        assert TECHNIQUE_ABBREVIATION[TechniqueType.PULL_OFF] == "P"

    def test_abbreviation_vibrato(self):
        assert TECHNIQUE_ABBREVIATION[TechniqueType.VIBRATO] == "~"

    def test_abbreviation_keys_are_techniques(self):
        """所有缩写键都是 TechniqueType 成员"""
        for key in TECHNIQUE_ABBREVIATION:
            assert isinstance(key, TechniqueType)

    def test_abbreviation_values_are_nonempty_strings(self):
        for val in TECHNIQUE_ABBREVIATION.values():
            assert isinstance(val, str) and len(val) > 0


# ============================================================
# ThemeConfig
# ============================================================


@pytest.fixture()
def cleanup_custom_themes():
    """注册自定义主题的测试用例执行后清理，避免污染全局 PRESET_THEMES"""
    yield
    for name in ("sepia", "highcontrast", "testtheme"):
        ThemeConfig.unregister_theme(name)


class TestThemeConfig:
    """主题配置工厂、注册与访问"""

    def test_get_theme_light(self, cleanup_custom_themes):
        t = ThemeConfig.get_theme("light")
        assert t.name == "light"
        assert t.COLOR_BG == "#FFFFFF"
        assert t.COLOR_TEXT == "#000000"

    def test_get_theme_dark(self, cleanup_custom_themes):
        t = ThemeConfig.get_theme("dark")
        assert t.name == "dark"
        assert t.COLOR_BG == "#1E1E2E"

    def test_get_theme_case_insensitive(self, cleanup_custom_themes):
        """名称大小写不敏感"""
        assert ThemeConfig.get_theme("LIGHT").name == "light"
        assert ThemeConfig.get_theme("Dark").name == "dark"

    def test_get_theme_unknown_raises_value_error(self, cleanup_custom_themes):
        with pytest.raises(ValueError, match="未知主题名称"):
            ThemeConfig.get_theme("nonexistent")

    def test_list_themes_contains_defaults(self, cleanup_custom_themes):
        names = ThemeConfig.list_themes()
        assert "light" in names
        assert "dark" in names

    def test_is_dark_is_light(self, cleanup_custom_themes):
        assert ThemeConfig.get_theme("dark").is_dark is True
        assert ThemeConfig.get_theme("dark").is_light is False
        assert ThemeConfig.get_theme("light").is_light is True
        assert ThemeConfig.get_theme("light").is_dark is False

    def test_unknown_color_attribute_raises(self, cleanup_custom_themes):
        t = ThemeConfig.get_theme("dark")
        with pytest.raises(AttributeError, match="未知的颜色属性"):
            _ = t.COLOR_NOT_EXIST

    def test_to_dict_returns_copy(self, cleanup_custom_themes):
        t = ThemeConfig.get_theme("light")
        d = t.to_dict()
        d["COLOR_BG"] = "#000000"
        assert t.COLOR_BG == "#FFFFFF"  # 原对象不受影响

    def test_repr_contains_name(self, cleanup_custom_themes):
        r = repr(ThemeConfig.get_theme("dark"))
        assert "dark" in r and "ThemeConfig" in r

    def test_init_with_partial_colors_fills_defaults(self, cleanup_custom_themes):
        """传入部分颜色，缺失键用深色主题默认值填充"""
        t = ThemeConfig(colors={"COLOR_BG": "#FFFDE7"})
        assert t.COLOR_BG == "#FFFDE7"
        # COLOR_TEXT 未提供 → 用 dark 默认值
        assert t.COLOR_TEXT == "#E2E8F0"

    def test_init_with_none_uses_dark(self, cleanup_custom_themes):
        t = ThemeConfig(colors=None)
        assert t.COLOR_BG == "#1E1E2E"


class TestThemeConfigRegister:
    """运行时主题注册/注销"""

    def test_register_custom_theme(self, cleanup_custom_themes):
        t = ThemeConfig.register_theme("sepia", {"COLOR_BG": "#FFFDE7", "COLOR_TEXT": "#212121"})
        assert t.name == "sepia"
        assert t.COLOR_BG == "#FFFDE7"
        # 注册后可通过 get_theme 获取
        assert ThemeConfig.get_theme("sepia").COLOR_BG == "#FFFDE7"

    def test_register_fills_missing_keys(self, cleanup_custom_themes):
        """注册时缺失键用深色主题默认值填充"""
        ThemeConfig.register_theme("highcontrast", {"COLOR_BG": "#000000"})
        t = ThemeConfig.get_theme("highcontrast")
        # COLOR_TEXT 未提供 → dark 默认值
        assert t.COLOR_TEXT == "#E2E8F0"

    def test_register_protects_builtin_light(self, cleanup_custom_themes):
        """覆盖 light 内置主题被忽略，返回内置 light"""
        t = ThemeConfig.register_theme("light", {"COLOR_BG": "#000000"})
        assert t.COLOR_BG == "#FFFFFF"  # 仍是内置 light

    def test_register_protects_builtin_dark(self, cleanup_custom_themes):
        t = ThemeConfig.register_theme("dark", {"COLOR_BG": "#FFFFFF"})
        assert t.COLOR_BG == "#1E1E2E"  # 仍是内置 dark

    def test_unregister_custom_theme_success(self, cleanup_custom_themes):
        ThemeConfig.register_theme("testtheme", {"COLOR_BG": "#123456"})
        assert ThemeConfig.unregister_theme("testtheme") is True
        # 注销后 get_theme 应抛错
        with pytest.raises(ValueError):
            ThemeConfig.get_theme("testtheme")

    def test_unregister_builtin_returns_false(self, cleanup_custom_themes):
        assert ThemeConfig.unregister_theme("dark") is False
        assert ThemeConfig.unregister_theme("light") is False

    def test_unregister_nonexistent_returns_false(self, cleanup_custom_themes):
        assert ThemeConfig.unregister_theme("never_registered") is False

    def test_register_case_insensitive(self, cleanup_custom_themes):
        ThemeConfig.register_theme("Sepia", {"COLOR_BG": "#AAAAAA"})
        assert ThemeConfig.get_theme("sepia").COLOR_BG == "#AAAAAA"


# ============================================================
# RenderConfig
# ============================================================


class TestRenderConfig:
    """渲染配置主题透传与类型校验"""

    def test_default_theme_is_dark(self, cleanup_custom_themes):
        cfg = RenderConfig()
        assert cfg.theme.name == "dark"

    def test_init_with_light_theme(self, cleanup_custom_themes):
        cfg = RenderConfig(theme=ThemeConfig.get_theme("light"))
        assert cfg.theme.name == "light"
        assert cfg.COLOR_BG == "#FFFFFF"

    def test_theme_setter_switches_colors(self, cleanup_custom_themes):
        cfg = RenderConfig()
        assert cfg.COLOR_BG == "#1E1E2E"
        cfg.theme = ThemeConfig.get_theme("light")
        assert cfg.COLOR_BG == "#FFFFFF"

    def test_theme_setter_rejects_non_theme(self, cleanup_custom_themes):
        cfg = RenderConfig()
        with pytest.raises(TypeError, match="必须是 ThemeConfig"):
            cfg.theme = "not a theme"

    def test_color_properties_read_from_theme(self, cleanup_custom_themes):
        cfg = RenderConfig(theme=ThemeConfig.get_theme("dark"))
        assert cfg.COLOR_TAB_LINE == "#888888"
        assert cfg.COLOR_TECHNIQUE == "#F97316"
        assert cfg.COLOR_TRACK_NAME == "#60A5FA"

    def test_page_dimensions_a4_ratio(self, cleanup_custom_themes):
        """页面宽高比应接近 A4 的 √2 ≈ 1.414"""
        ratio = RenderConfig.PAGE_HEIGHT_PX / RenderConfig.PAGE_WIDTH_PX
        assert abs(ratio - 1.41421356) < 0.01


# ============================================================
# 其他枚举
# ============================================================


class TestEnums:
    """推弦/揉弦/渲染模式枚举完整性"""

    def test_bend_type_has_custom_and_bend(self):
        assert BendType.CUSTOM.value == 0
        assert BendType.BEND.value == 1

    def test_bend_style_members(self):
        assert BendStyle.DEFAULT.value == 0
        assert BendStyle.GRADUAL.value == 1
        assert BendStyle.FAST.value == 2

    def test_vibrato_type_members(self):
        assert VibratoType.NONE.value == 0
        assert VibratoType.SLIGHT.value == 1
        assert VibratoType.WIDE.value == 2

    def test_render_mode_tab_only_default(self):
        assert RenderMode.TAB_ONLY.value == 1

    def test_render_mode_has_five_modes(self):
        assert len(list(RenderMode)) == 5


# ============================================================
# get_string_name
# ============================================================


class TestGetStringName:
    """弦序名辅助函数"""

    @pytest.mark.parametrize(
        "idx,expected",
        [
            (0, "1弦(E)"),
            (1, "2弦(B)"),
            (2, "3弦(G)"),
            (3, "4弦(D)"),
            (4, "5弦(A)"),
            (5, "6弦(E)"),
        ],
    )
    def test_valid_indices(self, idx, expected):
        assert get_string_name(idx) == expected

    def test_negative_index_returns_generic(self):
        assert get_string_name(-1) == "0弦"

    def test_out_of_range_returns_generic(self):
        """越界返回 {idx+1}弦"""
        assert get_string_name(6) == "7弦"
        assert get_string_name(10) == "11弦"
