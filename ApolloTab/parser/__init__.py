"""
============================================================
文件名: parser/__init__.py
功能描述: ApolloTab 解析器模块入口
         导出: GTPParser(GP3-5)、GP7Parser(GP7/GP8)、parse_gtp、parse_gp7、parse_score

创建日期: 2026-06-06
最后更新: 2026-06-28 (v0.4.0: 新增 GP7/GP8 支持和智能调度)
依赖: gtp_parser.py, gp7_parser.py, gpif_parser.py
============================================================
"""

import os

from ..models.song import GTPSong
from .binary_stylesheet import BinaryStylesheet
from .gp7_parser import GP7Parser
from .gpif_parser import GpifParser
from .gtp_parser import GTPParser, parse_gtp
from .part_configuration import PartConfiguration

# 支持的文件扩展名(统一管理)
GP3_5_EXTENSIONS = ('.gp3', '.gp4', '.gp5', '.gpx', '.gtp')  # GP3-5(PyGuitarPro 解析)
GP7_8_EXTENSIONS = ('.gp',)  # GP7/GP8(原生解析)
ALL_SUPPORTED_EXTENSIONS = GP3_5_EXTENSIONS + GP7_8_EXTENSIONS


def parse_score(file_path: str) -> GTPSong:
    """
    智能调度解析器 - 根据文件扩展名自动选择解析器

    原理:
      - .gp3/.gp4/.gp5/.gpx/.gtp → 使用 GTPParser(基于 PyGuitarPro)
      - .gp                       → 使用 GP7Parser(原生 ZIP+GPIF 解析)
      - 其他扩展名                → 抛出 ValueError

    参数:
        file_path: GTP 文件路径(相对路径或绝对路径)

    返回:
        GTPSong 对象

    异常:
        ValueError: 不支持的文件扩展名
        FileNotFoundError: 文件不存在

    使用示例:
        from ApolloTab import parse_score
        song = parse_score("song.gp5")   # 自动用 GTPParser
        song = parse_score("song.gp")    # 自动用 GP7Parser
    """
    if not file_path:
        raise ValueError("文件路径为空")

    # 获取小写扩展名
    _, ext = os.path.splitext(file_path)
    ext_lower = ext.lower()

    if ext_lower in GP7_8_EXTENSIONS:
        # GP7/GP8 文件 → 使用原生解析器
        parser = GP7Parser()
        return parser.parse_file(file_path)
    elif ext_lower in GP3_5_EXTENSIONS:
        # GP3-5 文件 → 使用 PyGuitarPro 解析器
        return parse_gtp(file_path)
    else:
        raise ValueError(
            f"不支持的文件扩展名: {ext} (支持的扩展名: {', '.join(ALL_SUPPORTED_EXTENSIONS)})"
        )


def parse_gp7(file_path: str) -> GTPSong:
    """
    解析 GP7/GP8 (.gp) 文件 - 便捷函数

    参数:
        file_path: .gp 文件路径

    返回:
        GTPSong 对象

    使用示例:
        from ApolloTab.parser import parse_gp7
        song = parse_gp7("song.gp")
    """
    parser = GP7Parser()
    return parser.parse_file(file_path)


__all__ = [
    # GP3-5 解析器
    'GTPParser',
    'parse_gtp',
    # GP7/GP8 解析器
    'GP7Parser',
    'GpifParser',
    'parse_gp7',
    # 二进制配置解析器
    'BinaryStylesheet',
    'PartConfiguration',
    # 智能调度函数(推荐入口)
    'parse_score',
    # 扩展名常量
    'GP3_5_EXTENSIONS',
    'GP7_8_EXTENSIONS',
    'ALL_SUPPORTED_EXTENSIONS',
]
