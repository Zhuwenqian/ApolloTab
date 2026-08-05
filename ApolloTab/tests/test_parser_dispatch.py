"""
ApolloTab/tests/test_parser_dispatch.py

parse_score 智能调度与 GTPParser 纯函数测试 (ApolloTab v1.4.1)

覆盖范围:
  - parse_score: 按扩展名调度 (.gp→GP7Parser, .gp3-5→parse_gtp)
                 空路径/未知扩展名 → ValueError; 大小写不敏感
  - parse_gp7 便捷函数
  - 扩展名常量: GP3_5_EXTENSIONS / GP7_8_EXTENSIONS / ALL_SUPPORTED_EXTENSIONS
  - GTPParser._infer_instrument_from_name: 名称→MIDI乐器映射 + 打击乐跳过 + 顺序优先
  - GTPParser._duration_from_value: 整数→NoteDuration 枚举 + 未知回退
  - GTPParser._convert_key: 调号枚举→整数 (value/name/异常分支)

运行命令: python -m pytest ApolloTab/tests/test_parser_dispatch.py -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ApolloTab.models.song import GTPSong
from ApolloTab.parser import (
    ALL_SUPPORTED_EXTENSIONS,
    GP3_5_EXTENSIONS,
    GP7_8_EXTENSIONS,
    GTPParser,
    parse_gp7,
    parse_score,
)
from ApolloTab.utils.constants import NoteDuration

# ============================================================
# 扩展名常量
# ============================================================


class TestExtensionConstants:
    """扩展名常量完整性"""

    def test_gp7_8_contains_only_gp(self):
        assert GP7_8_EXTENSIONS == (".gp",)

    def test_gp3_5_contains_classic_extensions(self):
        for ext in (".gp3", ".gp4", ".gp5", ".gpx", ".gtp"):
            assert ext in GP3_5_EXTENSIONS

    def test_all_supported_is_union(self):
        assert set(ALL_SUPPORTED_EXTENSIONS) == set(GP3_5_EXTENSIONS) | set(GP7_8_EXTENSIONS)

    def test_no_overlap_between_groups(self):
        assert set(GP3_5_EXTENSIONS) & set(GP7_8_EXTENSIONS) == set()


# ============================================================
# parse_score 调度
# ============================================================


class TestParseScoreDispatch:
    """parse_score 根据扩展名选择解析器"""

    def test_dispatch_gp_uses_gp7parser(self, real_gp7_chords_file: Path):
        """.gp 文件 → GP7Parser → 返回 GTPSong (真实 chords.gp)"""
        song = parse_score(str(real_gp7_chords_file))
        assert isinstance(song, GTPSong)
        assert song.gp_version  # GP7 文件带版本号

    def test_dispatch_gp5_uses_gtp_parser(self, tmp_path: Path):
        """.gp5 → parse_gtp (mock 验证调度，不真正解析)"""
        fake = tmp_path / "fake.gp5"
        fake.write_bytes(b"fake")
        with patch("ApolloTab.parser.parse_gtp") as mock_parse:
            mock_parse.return_value = GTPSong(title="mocked")
            song = parse_score(str(fake))
            mock_parse.assert_called_once_with(str(fake))
            assert song.title == "mocked"

    @pytest.mark.parametrize("ext", [".gp3", ".gp4", ".gpx", ".gtp"])
    def test_dispatch_gp3_5_extensions_use_gtp_parser(self, tmp_path: Path, ext):
        """所有 GP3-5 扩展名都路由到 parse_gtp"""
        fake = tmp_path / f"fake{ext}"
        fake.write_bytes(b"fake")
        with patch("ApolloTab.parser.parse_gtp") as mock_parse:
            mock_parse.return_value = GTPSong()
            parse_score(str(fake))
            mock_parse.assert_called_once()

    def test_dispatch_case_insensitive_extension(self, tmp_path: Path):
        """大写扩展名同样识别"""
        fake = tmp_path / "song.GP"
        fake.write_bytes(b"fake")
        # .GP → GP7Parser; 用 mock 避免真正解析假数据
        with patch("ApolloTab.parser.GP7Parser") as mock_cls:
            mock_cls.return_value.parse_file.return_value = GTPSong()
            parse_score(str(fake))
            mock_cls.return_value.parse_file.assert_called_once_with(str(fake))

    def test_empty_path_raises_value_error(self):
        with pytest.raises(ValueError, match="文件路径为空"):
            parse_score("")

    def test_unknown_extension_raises_value_error(self, tmp_path: Path):
        fake = tmp_path / "song.mid"
        fake.write_bytes(b"x")
        with pytest.raises(ValueError, match="不支持的文件扩展名"):
            parse_score(str(fake))

    def test_unknown_extension_message_lists_supported(self, tmp_path: Path):
        """错误信息应列出所有支持的扩展名"""
        fake = tmp_path / "song.xyz"
        fake.write_bytes(b"x")
        with pytest.raises(ValueError, match="\\.gp3|\\.gp|\\.gp5"):
            parse_score(str(fake))


# ============================================================
# parse_gp7 便捷函数
# ============================================================


class TestParseGp7Helper:
    """parse_gp7 便捷函数"""

    def test_parse_gp7_returns_song(self, real_gp7_chords_file: Path):
        song = parse_gp7(str(real_gp7_chords_file))
        assert isinstance(song, GTPSong)


# ============================================================
# GTPParser._infer_instrument_from_name
# ============================================================


class TestInferInstrumentFromName:
    """音轨名 → MIDI 乐器编号推断"""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Nylon Guitar", 24),
            ("Acoustic Guitar", 25),
            ("Steel Guitar", 25),
            ("Overdriven Guitar", 29),
            ("Piano", 0),
            ("Keyboard", 0),
        ],
    )
    def test_known_instruments(self, name, expected):
        assert GTPParser._infer_instrument_from_name(name) == expected

    def test_substring_match_not_word_match(self):
        """子串匹配: 'Nylon Acoustic Guitar' 含 'Acoustic Guitar' → 25

        注: 源码注释声称 'Nylon Guitar' 应优先于 'Acoustic Guitar'，但实际为
        子串匹配，'Nylon Guitar' 非 'Nylon Acoustic Guitar' 的子串，故落到 25。
        此为预存行为差异 (非本轮引入)，测试反映实际行为。
        """
        assert GTPParser._infer_instrument_from_name("Nylon Acoustic Guitar") == 25

    @pytest.mark.parametrize("name", ["Drums", "Drum Kit", "percussion", "perc", "鼓轨"])
    def test_drum_keywords_return_none(self, name):
        """打击乐关键词 → None (跳过推断)"""
        assert GTPParser._infer_instrument_from_name(name) is None

    def test_empty_string_returns_none(self):
        assert GTPParser._infer_instrument_from_name("") is None

    def test_none_returns_none(self):
        assert GTPParser._infer_instrument_from_name(None) is None

    def test_unknown_name_returns_none(self):
        assert GTPParser._infer_instrument_from_name("Mystery Instrument") is None

    def test_case_insensitive(self):
        """匹配不区分大小写"""
        assert GTPParser._infer_instrument_from_name("acoustic guitar") == 25
        assert GTPParser._infer_instrument_from_name("PIANO") == 0

    def test_substring_match(self):
        """子串匹配: 'Lead Acoustic Guitar Track' 含 'Acoustic Guitar'"""
        assert GTPParser._infer_instrument_from_name("Lead Acoustic Guitar Track") == 25


# ============================================================
# GTPParser._duration_from_value
# ============================================================


class TestDurationFromValue:
    """guitarpro Duration 整数 → NoteDuration 枚举"""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (1, NoteDuration.WHOLE),
            (2, NoteDuration.HALF),
            (4, NoteDuration.QUARTER),
            (8, NoteDuration.EIGHTH),
            (16, NoteDuration.SIXTEENTH),
            (32, NoteDuration.THIRTY_SECOND),
        ],
    )
    def test_known_values(self, value, expected):
        assert GTPParser._duration_from_value(value) == expected

    def test_unknown_value_defaults_quarter(self):
        """未知值 → 默认四分音符"""
        assert GTPParser._duration_from_value(64) == NoteDuration.QUARTER

    def test_zero_defaults_quarter(self):
        assert GTPParser._duration_from_value(0) == NoteDuration.QUARTER


# ============================================================
# GTPParser._convert_key
# ============================================================


class TestConvertKey:
    """调号枚举 → 整数 (0=C大调, 正=升号数, 负=降号数)"""

    def test_with_value_attribute(self):
        """key_sig 有 value 属性 → 直接返回 value"""

        class FakeKey:
            value = 3

        assert GTPParser()._convert_key(FakeKey()) == 3

    def test_with_name_cmajor(self):
        """无 value, name='CMajor' → 0"""

        class FakeKey:
            name = "CMajor"

        assert GTPParser()._convert_key(FakeKey()) == 0

    def test_with_name_gmajor(self):
        """GMajor = 1 升号"""

        class FakeKey:
            name = "GMajor"

        assert GTPParser()._convert_key(FakeKey()) == 1

    def test_with_name_fmajor(self):
        """FMajor = 1 降号 → -1"""

        class FakeKey:
            name = "FMajor"

        assert GTPParser()._convert_key(FakeKey()) == -1

    def test_with_name_bflatmajor(self):
        """BFlatMajor = 2 降号 → -2"""

        class FakeKey:
            name = "BFlatMajor"

        assert GTPParser()._convert_key(FakeKey()) == -2

    def test_with_name_aminor(self):
        """AMinor = 0 (关系小调)"""

        class FakeKey:
            name = "AMinor"

        assert GTPParser()._convert_key(FakeKey()) == 0

    def test_unknown_name_defaults_zero(self):
        class FakeKey:
            name = "UnknownKey"

        assert GTPParser()._convert_key(FakeKey()) == 0

    def test_no_value_no_name_defaults_zero(self):
        """既无 value 也无 name → 0 (异常分支)"""

        class FakeKey:
            pass

        assert GTPParser()._convert_key(FakeKey()) == 0
