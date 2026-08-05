"""
============================================================
文件名: binary_stylesheet.py
功能描述: GP7/GP8 二进制样式表(BinaryStylesheet)解析器
         解析 .gp 文件 ZIP 包中的 Content/BinaryStylesheet 二进制文件，
         提取显示样式键值对（音轨名显示策略、页眉页脚模板、对齐方式等）

原理:
  BinaryStylesheet 文件结构（参照 alphaTab BinaryStylesheet.ts）:
    int32 (大端)    | 键值对数量
    KeyValuePair[]  | 每条记录: 1字节key长度 + UTF8 key + 1字节类型 + 值

  7 种数据类型:
    0=Boolean  (1字节, 0=false)
    1=Integer  (4字节大端 int32)
    2=Float    (4字节大端 IEEE float32)
    3=String   (int16长度 + UTF8内容)
    4=Point    (int32 x + int32 y)
    5=Size     (int32 width + int32 height)
    6=Rectangle(4×int32: x/y/w/h)
    7=Color    (4字节 RGBA)

调用来源: alphaTab-develop/packages/alphatab/src/importer/BinaryStylesheet.ts
解析后: apply() 方法将键值对应用到 GTPSong.stylesheet 字典字段

创建日期: 2026-06-28 (v0.4.0: GP7/GP8 支持)
依赖: Python 3.11+ 标准库 struct
============================================================
"""

import struct
from typing import Any, Dict, Optional


# ============================================================
# 数据类型枚举（与 alphaTab DataType 一致）
# ============================================================
class BinaryStylesheetDataType:
    """BinaryStylesheet 值的数据类型常量"""

    BOOLEAN = 0  # 1字节布尔
    INTEGER = 1  # 4字节大端int32
    FLOAT = 2  # 4字节大端IEEE float32
    STRING = 3  # int16长度 + UTF8
    POINT = 4  # int32 x + int32 y
    SIZE = 5  # int32 width + int32 height
    RECTANGLE = 6  # 4×int32 (x/y/w/h)
    COLOR = 7  # 4字节 RGBA


class BinaryStylesheet:
    """
    GP7/GP8 二进制样式表解析器

    用法:
        # 解析样式表数据
        stylesheet = BinaryStylesheet(data_bytes)
        # 应用到 GTPSong
        stylesheet.apply(song)

    解析后所有键值对存储在 self._values 字典中，
    通过 self.get(key, default) 可获取单个值，
    通过 self.values 可获取完整字典（用于 GTPSong.stylesheet 字段）。
    """

    def __init__(self, data: bytes):
        """
        初始化并解析 BinaryStylesheet 二进制数据

        参数:
            data: BinaryStylesheet 文件的原始字节数据
                  （从 .gp ZIP 包的 Content/BinaryStylesheet 条目读取）
        """
        self._values: dict[str, Any] = {}
        self._types: dict[str, int] = {}
        if data:
            self._read(data)

    def _read(self, data: bytes) -> None:
        """
        解析二进制数据，填充 _values 和 _types 字典

        原理:
          1. 读取前 4 字节大端 int32 获取键值对数量
          2. 循环读取每条 KeyValuePair:
             - 1 字节 key 长度
             - n 字节 UTF-8 key
             - 1 字节类型枚举
             - 按类型读取值
        """
        offset = 0

        # 读取键值对数量（4字节大端int32）
        if len(data) < 4:
            return
        entry_count = struct.unpack_from('>i', data, offset)[0]
        offset += 4

        for _ in range(entry_count):
            if offset >= len(data):
                break

            # 读取 1 字节 key 长度
            key_len = data[offset]
            offset += 1

            # 读取 UTF-8 key
            key = data[offset : offset + key_len].decode('utf-8', errors='ignore')
            offset += key_len

            # 读取 1 字节类型
            if offset >= len(data):
                break
            type_code = data[offset]
            offset += 1
            self._types[key] = type_code

            # 按类型读取值
            value, offset = self._read_value(data, offset, type_code)
            self._values[key] = value

    def _read_value(self, data: bytes, offset: int, type_code: int):
        """
        按数据类型读取值，返回 (value, new_offset)

        参数:
            data:      完整二进制数据
            offset:    当前读取位置
            type_code: 数据类型枚举值

        返回:
            Tuple[值, 新偏移]
        """
        try:
            if type_code == BinaryStylesheetDataType.BOOLEAN:
                # 1 字节布尔
                val = data[offset] == 1
                return val, offset + 1

            elif type_code == BinaryStylesheetDataType.INTEGER:
                # 4 字节大端 int32
                val = struct.unpack_from('>i', data, offset)[0]
                return val, offset + 4

            elif type_code == BinaryStylesheetDataType.FLOAT:
                # 4 字节大端 IEEE float32
                val = struct.unpack_from('>f', data, offset)[0]
                return val, offset + 4

            elif type_code == BinaryStylesheetDataType.STRING:
                # int16 长度 + UTF8 内容
                str_len = struct.unpack_from('>h', data, offset)[0]
                offset += 2
                val = data[offset : offset + str_len].decode('utf-8', errors='ignore')
                return val, offset + str_len

            elif type_code == BinaryStylesheetDataType.POINT:
                # int32 x + int32 y
                x, y = struct.unpack_from('>ii', data, offset)
                return (x, y), offset + 8

            elif type_code == BinaryStylesheetDataType.SIZE:
                # int32 width + int32 height
                w, h = struct.unpack_from('>ii', data, offset)
                return (w, h), offset + 8

            elif type_code == BinaryStylesheetDataType.RECTANGLE:
                # 4×int32: x/y/w/h
                x, y, w, h = struct.unpack_from('>iiii', data, offset)
                return (x, y, w, h), offset + 16

            elif type_code == BinaryStylesheetDataType.COLOR:
                # 4 字节 RGBA
                r, g, b, a = data[offset], data[offset + 1], data[offset + 2], data[offset + 3]
                return (r, g, b, a), offset + 4

            else:
                # 未知类型，跳过（无法确定大小，只能停止解析）
                return None, len(data)
        except (struct.error, IndexError):
            # 数据损坏，停止解析
            return None, len(data)

    @property
    def values(self) -> dict[str, Any]:
        """获取完整的键值对字典"""
        return dict(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        """获取单个键值"""
        return self._values.get(key, default)

    def apply(self, song) -> None:
        """
        将样式表应用到 GTPSong 对象

        原理:
          将所有键值对写入 song.stylesheet 字典字段，
          同时根据已知键更新 song 的特定字段（标题/艺术家等已由 GPIF XML 设置，
          这里只补充 stylesheet 信息以备渲染器未来扩展使用）。

        参数:
            song: GTPSong 对象（将被原地修改）
        """
        # 将完整样式表存入 song.stylesheet
        song.stylesheet = dict(self._values)

        # 解析音轨名显示策略（用于渲染器未来扩展）
        # System/showTrackNameSingle: Hidden/FirstSystem/AllSystems
        # System/showTrackNameMulti:  同上
        # System/trackNameModeSingle: 0=FirstSystem, 1=FirstSystem(每页), 2=AllSystems
        # 这些值已存入 song.stylesheet，渲染器可按需读取
