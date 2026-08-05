"""
ApolloTab/tests/test_binary_stylesheet.py

GP7/GP8 二进制样式表解析测试 (ApolloTab v1.4.1)

覆盖范围:
  - 7 种数据类型解析 (Boolean/Integer/Float/String/Point/Size/Rectangle/Color)
  - get(key, default) / values 字典访问
  - apply 写入 GTPSong.stylesheet
  - 边界: 空数据 / 数据损坏 / 未知类型 / 多条目混合

运行命令: python -m pytest ApolloTab/tests/test_binary_stylesheet.py -q
"""

from __future__ import annotations

import struct

import pytest

from ApolloTab.models.song import GTPSong
from ApolloTab.parser.binary_stylesheet import (
    BinaryStylesheet,
    BinaryStylesheetDataType,
)

# ============================================================
# 二进制构造辅助
# ============================================================


def build_entry(key: str, type_code: int, value) -> bytes:
    """构造单条 BinaryStylesheet 键值对的二进制 (key 长度 + key + type + value)"""
    key_bytes = key.encode("utf-8")
    data = struct.pack("B", len(key_bytes)) + key_bytes
    data += struct.pack("B", type_code)
    if type_code == BinaryStylesheetDataType.BOOLEAN:
        data += struct.pack("B", 1 if value else 0)
    elif type_code == BinaryStylesheetDataType.INTEGER:
        data += struct.pack(">i", value)
    elif type_code == BinaryStylesheetDataType.FLOAT:
        data += struct.pack(">f", value)
    elif type_code == BinaryStylesheetDataType.STRING:
        vb = value.encode("utf-8")
        data += struct.pack(">h", len(vb)) + vb
    elif type_code == BinaryStylesheetDataType.POINT or type_code == BinaryStylesheetDataType.SIZE:
        data += struct.pack(">ii", value[0], value[1])
    elif type_code == BinaryStylesheetDataType.RECTANGLE:
        data += struct.pack(">iiii", *value)
    elif type_code == BinaryStylesheetDataType.COLOR:
        data += struct.pack("BBBB", *value)
    else:
        raise ValueError(f"测试不支持的类型码: {type_code}")
    return data


def build_stylesheet(entries) -> bytes:
    """构造完整 BinaryStylesheet 二进制: int32 数量 + 各 entry"""
    data = struct.pack(">i", len(entries))
    for key, type_code, value in entries:
        data += build_entry(key, type_code, value)
    return data


# ============================================================
# 数据类型常量
# ============================================================

BOOL = BinaryStylesheetDataType.BOOLEAN
INT = BinaryStylesheetDataType.INTEGER
FLOAT = BinaryStylesheetDataType.FLOAT
STR = BinaryStylesheetDataType.STRING
POINT = BinaryStylesheetDataType.POINT
SIZE = BinaryStylesheetDataType.SIZE
RECT = BinaryStylesheetDataType.RECTANGLE
COLOR = BinaryStylesheetDataType.COLOR


# ============================================================
# 单类型解析
# ============================================================


class TestBinaryStylesheetTypes:
    """各数据类型二进制解析"""

    def test_boolean_true(self):
        bs = BinaryStylesheet(build_stylesheet([("flag", BOOL, True)]))
        assert bs.get("flag") is True

    def test_boolean_false(self):
        bs = BinaryStylesheet(build_stylesheet([("flag", BOOL, False)]))
        assert bs.get("flag") is False

    def test_integer_positive(self):
        bs = BinaryStylesheet(build_stylesheet([("count", INT, 42)]))
        assert bs.get("count") == 42

    def test_integer_negative(self):
        """大端 int32 支持负值"""
        bs = BinaryStylesheet(build_stylesheet([("offset", INT, -7)]))
        assert bs.get("offset") == -7

    def test_float_value(self):
        bs = BinaryStylesheet(build_stylesheet([("ratio", FLOAT, 1.5)]))
        assert abs(bs.get("ratio") - 1.5) < 1e-6

    def test_string_utf8(self):
        bs = BinaryStylesheet(build_stylesheet([("name", STR, "Guitar")]))
        assert bs.get("name") == "Guitar"

    def test_string_chinese_utf8(self):
        """UTF-8 中文内容"""
        bs = BinaryStylesheet(build_stylesheet([("label", STR, "吉他")]))
        assert bs.get("label") == "吉他"

    def test_point(self):
        bs = BinaryStylesheet(build_stylesheet([("pos", POINT, (10, 20))]))
        assert bs.get("pos") == (10, 20)

    def test_size(self):
        bs = BinaryStylesheet(build_stylesheet([("dim", SIZE, (800, 600))]))
        assert bs.get("dim") == (800, 600)

    def test_rectangle(self):
        bs = BinaryStylesheet(build_stylesheet([("rect", RECT, (1, 2, 3, 4))]))
        assert bs.get("rect") == (1, 2, 3, 4)

    def test_color(self):
        bs = BinaryStylesheet(build_stylesheet([("clr", COLOR, (255, 128, 0, 64))]))
        assert bs.get("clr") == (255, 128, 0, 64)


# ============================================================
# 多条目与字典访问
# ============================================================


class TestBinaryStylesheetMultiple:
    """多条目混合解析与字典访问"""

    def test_multiple_entries_mixed_types(self):
        bs = BinaryStylesheet(
            build_stylesheet(
                [
                    ("showName", BOOL, True),
                    ("zoom", INT, 100),
                    ("title", STR, "My Song"),
                    ("bg", COLOR, (10, 20, 30, 255)),
                ]
            )
        )
        assert bs.get("showName") is True
        assert bs.get("zoom") == 100
        assert bs.get("title") == "My Song"
        assert bs.get("bg") == (10, 20, 30, 255)

    def test_values_returns_full_dict(self):
        bs = BinaryStylesheet(
            build_stylesheet(
                [
                    ("a", INT, 1),
                    ("b", INT, 2),
                ]
            )
        )
        vals = bs.values
        assert vals == {"a": 1, "b": 2}

    def test_values_returns_copy(self):
        """values 返回副本，修改不影响内部"""
        bs = BinaryStylesheet(build_stylesheet([("a", INT, 1)]))
        vals = bs.values
        vals["a"] = 999
        assert bs.get("a") == 1

    def test_get_missing_key_returns_default(self):
        bs = BinaryStylesheet(build_stylesheet([("a", INT, 1)]))
        assert bs.get("missing") is None
        assert bs.get("missing", "fallback") == "fallback"

    def test_empty_stylesheet_dict(self):
        """0 条目 → 空字典"""
        bs = BinaryStylesheet(build_stylesheet([]))
        assert bs.values == {}


# ============================================================
# apply 到 GTPSong
# ============================================================


class TestBinaryStylesheetApply:
    """apply 写入 GTPSong.stylesheet"""

    def test_apply_writes_stylesheet_dict(self):
        bs = BinaryStylesheet(
            build_stylesheet(
                [
                    ("System/showTrackNameSingle", STR, "AllSystems"),
                    ("zoom", INT, 150),
                ]
            )
        )
        song = GTPSong()
        assert song.stylesheet is None
        bs.apply(song)
        assert song.stylesheet is not None
        assert song.stylesheet["System/showTrackNameSingle"] == "AllSystems"
        assert song.stylesheet["zoom"] == 150


# ============================================================
# 边界与容错
# ============================================================


class TestBinaryStylesheetEdgeCases:
    """数据损坏与边界场景"""

    def test_empty_bytes(self):
        """空字节数据 → 空字典，不抛异常"""
        bs = BinaryStylesheet(b"")
        assert bs.values == {}

    def test_none_data(self):
        """None → 空字典"""
        bs = BinaryStylesheet(None)
        assert bs.values == {}

    def test_data_shorter_than_count_header(self):
        """不足 4 字节 → 静默返回空"""
        bs = BinaryStylesheet(b"\x01\x02")
        assert bs.values == {}

    def test_truncated_entry_stops_gracefully(self):
        """声明 2 条但只提供 1 条数据 → 解析到断点停止，不抛异常"""
        data = struct.pack(">i", 2)  # 声明 2 条
        data += build_entry("first", INT, 1)  # 只给 1 条
        bs = BinaryStylesheet(data)
        assert bs.get("first") == 1

    def test_unknown_type_code_stops_parsing(self):
        """未知类型码 → 返回 None 并停止"""
        key_bytes = b"unknown"
        data = struct.pack(">i", 1)
        data += struct.pack("B", len(key_bytes)) + key_bytes
        data += struct.pack("B", 99)  # 未知类型
        data += b"\x00\x00\x00\x00"  # 占位
        bs = BinaryStylesheet(data)
        # 未知类型返回 None，后续停止
        assert bs.get("unknown") is None

    def test_data_type_constants(self):
        """类型常量值与 alphaTab 一致"""
        assert BinaryStylesheetDataType.BOOLEAN == 0
        assert BinaryStylesheetDataType.INTEGER == 1
        assert BinaryStylesheetDataType.FLOAT == 2
        assert BinaryStylesheetDataType.STRING == 3
        assert BinaryStylesheetDataType.POINT == 4
        assert BinaryStylesheetDataType.SIZE == 5
        assert BinaryStylesheetDataType.RECTANGLE == 6
        assert BinaryStylesheetDataType.COLOR == 7
