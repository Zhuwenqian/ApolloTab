"""
============================================================
文件名: gp7_parser.py
功能描述: GP7/GP8 (.gp) 文件解析器 - ZIP 解包+调度核心
         解析 .gp 文件(本质是 ZIP 包)，调度 GPIF/BinaryStylesheet/PartConfiguration 解析器
         生成最终的 GTPSong 数据模型

原理:
  GP7/GP8 的 .gp 文件本质是 ZIP 压缩包，包含以下条目:
    VERSION                - 版本信息文本(如 "7.0" 或 "8.0")
    Content/score.gpif     - GPIF XML 谱面数据(核心)
    Content/BinaryStylesheet - 二进制样式表(页眉页脚/音轨名显示策略等)
    Content/PartConfiguration - 二进制音轨视图配置(五线谱/TAB/简谱显示开关)
    Content/LayoutConfiguration - 布局配置(暂不解析)
    Content/Assets         - 资源文件(音频/图像等，暂不解析)

  解析流程:
    1. 用 zipfile 读取 .gp 文件
    2. 读取 VERSION 确认版本(GP7=7.x, GP8=8.x)
    3. 读取 Content/score.gpif → 调用 GpifParser 解析为 GTPSong
    4. 读取 Content/PartConfiguration → 调用 PartConfiguration 应用谱表显示配置
    5. 读取 Content/BinaryStylesheet → 调用 BinaryStylesheet 应用样式表

调用来源: alphaTab-develop/packages/alphatab/src/importer/Gp7Parser.ts
调用入口: parser/__init__.py 中的 parse_score() 根据扩展名调度

创建日期: 2026-06-28 (v0.4.0: GP7/GP8 支持)
最后更新: 2026-06-28 (v0.4.1: 改用 io.BytesIO 兼容 Python 3.13+ zipfile)
依赖: Python 3.11+ 标准库 zipfile, io
============================================================
"""

import io
import zipfile
from typing import Optional

# 导入数据模型
from ..models.song import GTPSong
from .binary_stylesheet import BinaryStylesheet

# 导入子解析器
from .gpif_parser import GpifParser
from .part_configuration import PartConfiguration

# ZIP 包内文件路径常量(使用正斜杠，跨平台兼容)
_PATH_VERSION = 'VERSION'
_PATH_GPIF = 'Content/score.gpif'
_PATH_BINARY_STYLESHEET = 'Content/BinaryStylesheet'
_PATH_PART_CONFIGURATION = 'Content/PartConfiguration'
_PATH_LAYOUT_CONFIGURATION = 'Content/LayoutConfiguration'


class GP7Parser:
    """
    GP7/GP8 (.gp) 文件解析器

    用法:
        parser = GP7Parser()
        song = parser.parse_file("song.gp")
        # 或解析字节数据
        song = parser.parse_bytes(zip_bytes)

    解析流程:
      1. 解包 ZIP 文件
      2. 读取 VERSION 确认版本
      3. 调用 GpifParser 解析 score.gpif → GTPSong
      4. 应用 PartConfiguration(谱表显示配置)
      5. 应用 BinaryStylesheet(样式表)
    """

    def __init__(self) -> None:
        """初始化解析器"""
        self._gp_version: str = ""  # 文件版本("7.0"/"8.0")

    def parse_file(self, file_path: str) -> GTPSong:
        """
        解析 .gp 文件

        参数:
            file_path: .gp 文件路径(相对路径或绝对路径)

        返回:
            GTPSong 对象

        异常:
            zipfile.BadZipFile: 文件不是有效的 ZIP/GP 格式
            KeyError: ZIP 包中缺少必要的 score.gpif 条目
            ValueError: GPIF XML 解析失败
        """
        with open(file_path, 'rb') as f:
            data = f.read()
        return self.parse_bytes(data)

    def parse_bytes(self, data: bytes) -> GTPSong:
        """
        解析 .gp 文件的字节数据

        参数:
            data: .gp 文件的完整字节数据

        返回:
            GTPSong 对象

        执行步骤:
          1. 用 zipfile.ZipFile 读取 ZIP 数据
          2. 读取 VERSION 条目获取版本号
          3. 读取 Content/score.gpif 调用 GpifParser 解析
          4. 读取 Content/PartConfiguration 应用谱表显示配置
          5. 读取 Content/BinaryStylesheet 应用样式表
        """
        # === Step 1: 解包 ZIP ===
        # 使用标准库 io.BytesIO 包装字节数据，兼容 zipfile.ZipFile 的文件对象接口
        # (Python 3.13+ 的 zipfile 要求文件对象提供 seekable() 方法，io.BytesIO 完整支持)
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise zipfile.BadZipFile(f"文件不是有效的 GP7/GP8 格式(非ZIP): {e}") from e

        # === Step 2: 读取版本号 ===
        try:
            version_bytes = zf.read(_PATH_VERSION)
            self._gp_version = version_bytes.decode('utf-8', errors='ignore').strip()
        except KeyError:
            # 缺少 VERSION 文件，使用默认值
            self._gp_version = "7.0"

        # === Step 3: 解析 GPIF XML (核心) ===
        try:
            gpif_bytes = zf.read(_PATH_GPIF)
        except KeyError as e:
            raise KeyError(f"GP7/GP8 文件缺少必需的条目: {_PATH_GPIF}") from e

        gpif_xml = gpif_bytes.decode('utf-8', errors='ignore')
        gpif_parser = GpifParser()
        song = gpif_parser.parse_xml(gpif_xml)

        # 覆盖版本号(优先使用 VERSION 文件的值)
        if self._gp_version:
            song.gp_version = self._gp_version

        # === Step 4: 应用 PartConfiguration (谱表显示配置) ===
        try:
            part_config_bytes = zf.read(_PATH_PART_CONFIGURATION)
            if part_config_bytes:
                part_config = PartConfiguration(part_config_bytes)
                part_config.apply(song)
        except KeyError:
            # 缺少 PartConfiguration 时使用默认值(全部显示)
            pass

        # === Step 5: 应用 BinaryStylesheet (样式表) ===
        try:
            stylesheet_bytes = zf.read(_PATH_BINARY_STYLESHEET)
            if stylesheet_bytes:
                stylesheet = BinaryStylesheet(stylesheet_bytes)
                stylesheet.apply(song)
        except KeyError:
            # 缺少 BinaryStylesheet 时不影响核心解析
            pass

        zf.close()
        return song

    @property
    def gp_version(self) -> str:
        """获取最近解析的文件版本号"""
        return self._gp_version
