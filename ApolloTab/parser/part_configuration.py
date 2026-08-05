"""
============================================================
文件名: part_configuration.py
功能描述: GP7/GP8 音轨视图配置(PartConfiguration)解析器
         解析 .gp 文件 ZIP 包中的 Content/PartConfiguration 二进制文件，
         提取每个音轨的谱表显示开关（五线谱/TAB/斜线谱/简谱）

原理:
  PartConfiguration 文件结构（参照 alphaTab PartConfiguration.ts）:
    int32 (大端)    | ScoreView 数量
    ScoreView[]     | 各 ScoreView 包含 isMultiRest + TrackViewGroup[]
    int32 (大端)    | 当前活跃视图索引

  ScoreView 结构:
    1 字节 (bool)      | isMultiRest
    int32 (大端)       | TrackViewGroup 数量
    TrackViewGroup[]   | 每个组 1 字节位标志

  TrackViewGroup 位标志:
    bit0 (0x01) | showStandardNotation (五线谱)
    bit1 (0x02) | showTablature        (六线谱 TAB)
    bit2 (0x04) | showSlash            (斜线谱)
    bit3 (0x08) | showNumbered         (简谱, GP8 新功能)
    全 0        | 默认启用五线谱（向后兼容）

  文件始终包含: 1 个多轨 ScoreView (索引0) + 每轨 1 个单轨 ScoreView

调用来源: alphaTab-develop/packages/alphatab/src/importer/PartConfiguration.ts
解析后: apply() 方法将位标志应用到 GTPTrack.show_standard_notation 等字段

创建日期: 2026-06-28 (v0.4.0: GP7/GP8 支持)
依赖: Python 3.11+ 标准库 struct
============================================================
"""

import struct
from dataclasses import dataclass, field
from typing import List


@dataclass
class PartConfigurationTrackViewGroup:
    """
    单个音轨的谱表显示配置（对应 alphaTab PartConfigurationTrackViewGroup）

    字段说明:
      show_numbered:         是否显示简谱（GP8 新功能，预留渲染接口）
      show_slash:            是否显示斜线谱（预留渲染接口）
      show_standard_notation: 是否显示五线谱（预留渲染接口）
      show_tablature:        是否显示六线谱 TAB（默认 True）
    """

    show_numbered: bool = False
    show_slash: bool = False
    show_standard_notation: bool = False
    show_tablature: bool = False


@dataclass
class PartConfigurationScoreView:
    """
    单个 ScoreView（对应 alphaTab PartConfigurationScoreView）

    字段说明:
      is_multi_rest:     是否多小节休止
      track_view_groups: 每个音轨的谱表显示配置列表
    """

    is_multi_rest: bool = False
    track_view_groups: list[PartConfigurationTrackViewGroup] = field(default_factory=list)


class PartConfiguration:
    """
    GP7/GP8 音轨视图配置解析器

    用法:
        # 解析配置数据
        part_config = PartConfiguration(data_bytes)
        # 应用到 GTPSong
        part_config.apply(song)

    解析后:
      - 第 0 个 ScoreView 是多轨布局，其位标志应用到对应 GTPTrack
      - 第 1+ 个 ScoreView 是单轨视图，记录每轨的 multi_rest 状态
    """

    def __init__(self, data: bytes):
        """
        初始化并解析 PartConfiguration 二进制数据

        参数:
            data: PartConfiguration 文件的原始字节数据
                  （从 .gp ZIP 包的 Content/PartConfiguration 条目读取）
        """
        self.score_views: list[PartConfigurationScoreView] = []
        self.active_view_index: int = 0
        if data:
            self._read(data)

    def _read(self, data: bytes) -> None:
        """
        解析二进制数据，填充 score_views 列表和 active_view_index

        原理:
          1. 读取 4 字节大端 int32 获取 ScoreView 数量
          2. 循环读取每个 ScoreView:
             - 1 字节 isMultiRest 布尔
             - 4 字节 TrackViewGroup 数量
             - 循环读取 TrackViewGroup (每个 1 字节位标志)
          3. 读取 4 字节大端 int32 获取活跃视图索引
        """
        offset = 0
        if len(data) < 4:
            return

        # 读取 ScoreView 数量
        score_view_count = struct.unpack_from('>i', data, offset)[0]
        offset += 4

        for _ in range(score_view_count):
            if offset >= len(data):
                break

            # 读取 1 字节 isMultiRest
            is_multi_rest = data[offset] != 0
            offset += 1

            # 读取 4 字节 TrackViewGroup 数量
            if offset + 4 > len(data):
                break
            group_count = struct.unpack_from('>i', data, offset)[0]
            offset += 4

            score_view = PartConfigurationScoreView(is_multi_rest=is_multi_rest)

            # 读取每个 TrackViewGroup
            for _ in range(group_count):
                if offset >= len(data):
                    break
                flags = data[offset]
                offset += 1

                # 全 0 时默认启用五线谱（alphaTab 兼容性处理）
                if flags == 0:
                    flags = 1

                group = PartConfigurationTrackViewGroup(
                    show_standard_notation=(flags & 0x01) != 0,
                    show_tablature=(flags & 0x02) != 0,
                    show_slash=(flags & 0x04) != 0,
                    show_numbered=(flags & 0x08) != 0,
                )
                score_view.track_view_groups.append(group)

            self.score_views.append(score_view)

        # 读取活跃视图索引
        if offset + 4 <= len(data):
            self.active_view_index = struct.unpack_from('>i', data, offset)[0]

    def apply(self, song) -> None:
        """
        将配置应用到 GTPSong 对象的音轨上

        原理:
          - 第 0 个 ScoreView 是多轨布局，其 trackViewGroups 按 trackIndex 对应到 song.tracks
          - 对每个 track 设置 show_standard_notation / show_tablature / show_slash / show_numbered
          - 打击乐轨道强制保留 show_tablature（鼓谱也是 TAB 形式）

        参数:
            song: GTPSong 对象（将被原地修改）
        """
        if not self.score_views or not song.tracks:
            return

        # 取第 0 个 ScoreView (多轨布局)
        multi_view = self.score_views[0]

        # 遍历每个 TrackViewGroup，对应到 song.tracks[i]
        for track_idx, group in enumerate(multi_view.track_view_groups):
            if track_idx >= len(song.tracks):
                break
            track = song.tracks[track_idx]

            # 应用谱表显示配置
            track.show_standard_notation = group.show_standard_notation
            track.show_tablature = group.show_tablature
            track.show_slash = group.show_slash
            track.show_numbered = group.show_numbered

            # 打击乐轨道：强制启用 TAB 显示（鼓谱通常用 TAB 表示）
            if track.is_percussion:
                track.show_tablature = True
                # 打击乐不显示五线谱/简谱/斜线谱
                # （本程序不渲染五线谱和简谱，但保留字段以备未来扩展）
