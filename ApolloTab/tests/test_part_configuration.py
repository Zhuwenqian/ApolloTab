"""
ApolloTab/tests/test_part_configuration.py

GP7/GP8 音轨视图配置解析测试 (ApolloTab v1.4.1)

覆盖范围:
  - PartConfiguration 二进制解析: ScoreView / TrackViewGroup 位标志
  - flags==0 默认启用五线谱 (alphaTab 兼容)
  - apply 写入 GTPTrack.show_standard_notation / show_tablature 等
  - 打击乐轨道强制 show_tablature=True
  - 边界: 空数据 / 数据截断 / track 多于 group

运行命令: python -m pytest ApolloTab/tests/test_part_configuration.py -q
"""

from __future__ import annotations

import struct

import pytest

from ApolloTab.models.song import GTPSong
from ApolloTab.models.track import GTPTrack
from ApolloTab.parser.part_configuration import (
    PartConfiguration,
    PartConfigurationScoreView,
    PartConfigurationTrackViewGroup,
)

# ============================================================
# 二进制构造辅助
# ============================================================


def build_part_config(score_views, active_view_index: int = 0) -> bytes:
    """构造 PartConfiguration 二进制

    score_views: list of (is_multi_rest: bool, groups: list[int flags])
    """
    data = struct.pack(">i", len(score_views))
    for is_multi_rest, groups in score_views:
        data += struct.pack("B", 1 if is_multi_rest else 0)
        data += struct.pack(">i", len(groups))
        for flags in groups:
            data += struct.pack("B", flags)
    data += struct.pack(">i", active_view_index)
    return data


# 位标志常量
FLAG_STD = 0x01  # 五线谱
FLAG_TAB = 0x02  # 六线谱
FLAG_SLASH = 0x04  # 斜线谱
FLAG_NUM = 0x08  # 简谱


# ============================================================
# 解析
# ============================================================


class TestPartConfigurationParse:
    """二进制 → ScoreView/TrackViewGroup 解析"""

    def test_single_view_single_group_tab_only(self):
        """单视图, 单轨道: 仅 TAB"""
        data = build_part_config([(False, [FLAG_TAB])])
        pc = PartConfiguration(data)
        assert len(pc.score_views) == 1
        sv = pc.score_views[0]
        assert sv.is_multi_rest is False
        assert len(sv.track_view_groups) == 1
        assert sv.track_view_groups[0].show_tablature is True
        assert sv.track_view_groups[0].show_standard_notation is False

    def test_flags_standard_and_tab(self):
        """位标志组合: 五线谱 + TAB"""
        data = build_part_config([(False, [FLAG_STD | FLAG_TAB])])
        g = PartConfiguration(data).score_views[0].track_view_groups[0]
        assert g.show_standard_notation is True
        assert g.show_tablature is True
        assert g.show_slash is False
        assert g.show_numbered is False

    def test_flags_all_four(self):
        """四位全开"""
        data = build_part_config([(False, [FLAG_STD | FLAG_TAB | FLAG_SLASH | FLAG_NUM])])
        g = PartConfiguration(data).score_views[0].track_view_groups[0]
        assert g.show_standard_notation is True
        assert g.show_tablature is True
        assert g.show_slash is True
        assert g.show_numbered is True

    def test_flags_zero_defaults_to_standard(self):
        """flags==0 → alphaTab 兼容: 默认启用五线谱"""
        data = build_part_config([(False, [0])])
        g = PartConfiguration(data).score_views[0].track_view_groups[0]
        assert g.show_standard_notation is True

    def test_multi_rest_flag(self):
        data = build_part_config([(True, [FLAG_TAB])])
        sv = PartConfiguration(data).score_views[0]
        assert sv.is_multi_rest is True

    def test_multiple_tracks_in_view(self):
        """单视图多轨道"""
        data = build_part_config([(False, [FLAG_TAB, FLAG_STD, FLAG_TAB | FLAG_STD])])
        sv = PartConfiguration(data).score_views[0]
        assert len(sv.track_view_groups) == 3
        assert sv.track_view_groups[0].show_tablature is True
        assert sv.track_view_groups[1].show_standard_notation is True
        assert sv.track_view_groups[2].show_tablature is True

    def test_multiple_score_views(self):
        """多 ScoreView (多轨 + 单轨)"""
        data = build_part_config(
            [
                (False, [FLAG_TAB, FLAG_STD]),  # 多轨视图
                (True, [FLAG_TAB]),  # 单轨视图 1
            ]
        )
        pc = PartConfiguration(data)
        assert len(pc.score_views) == 2
        assert pc.score_views[1].is_multi_rest is True

    def test_active_view_index(self):
        data = build_part_config([(False, [FLAG_TAB])], active_view_index=3)
        assert PartConfiguration(data).active_view_index == 3


# ============================================================
# apply 到 GTPSong
# ============================================================


class TestPartConfigurationApply:
    """apply 写入 GTPTrack 显示开关"""

    def test_apply_sets_track_flags(self):
        data = build_part_config([(False, [FLAG_TAB, FLAG_STD])])
        song = GTPSong(tracks=[GTPTrack(), GTPTrack()])
        PartConfiguration(data).apply(song)
        assert song.tracks[0].show_tablature is True
        assert song.tracks[0].show_standard_notation is False
        assert song.tracks[1].show_standard_notation is True
        assert song.tracks[1].show_tablature is False

    def test_apply_percussion_forces_tab(self):
        """打击乐轨道强制 show_tablature=True，即使 flags 未含 TAB"""
        data = build_part_config([(False, [FLAG_STD])])  # 仅五线谱
        drum_track = GTPTrack(name="Drums", is_percussion=True)
        song = GTPSong(tracks=[drum_track])
        PartConfiguration(data).apply(song)
        assert song.tracks[0].show_tablature is True

    def test_apply_more_tracks_than_groups_ok(self):
        """track 数量多于 group → 多余 track 不受影响"""
        data = build_part_config([(False, [FLAG_TAB])])  # 只 1 个 group
        song = GTPSong(tracks=[GTPTrack(), GTPTrack(), GTPTrack()])
        # 不应抛异常
        PartConfiguration(data).apply(song)
        assert song.tracks[0].show_tablature is True
        # tracks[1], [2] 保持默认 (show_tablature=True 默认)
        assert song.tracks[1].show_tablature is True

    def test_apply_empty_song_no_op(self):
        """空 song → 不操作"""
        data = build_part_config([(False, [FLAG_TAB])])
        PartConfiguration(data).apply(GTPSong())  # 不抛异常

    def test_apply_empty_score_views_no_op(self):
        """无 ScoreView → 不操作"""
        song = GTPSong(tracks=[GTPTrack()])
        PartConfiguration(b"").apply(song)
        # track 保持默认
        assert song.tracks[0].show_tablature is True


# ============================================================
# 边界
# ============================================================


class TestPartConfigurationEdgeCases:
    """数据损坏与边界"""

    def test_none_data(self):
        pc = PartConfiguration(None)
        assert pc.score_views == []
        assert pc.active_view_index == 0

    def test_empty_bytes(self):
        pc = PartConfiguration(b"")
        assert pc.score_views == []

    def test_data_shorter_than_header(self):
        """不足 4 字节 → 静默空"""
        pc = PartConfiguration(b"\x01\x02")
        assert pc.score_views == []

    def test_dataclass_defaults(self):
        """PartConfigurationTrackViewGroup 默认全 False"""
        g = PartConfigurationTrackViewGroup()
        assert g.show_standard_notation is False
        assert g.show_tablature is False
        assert g.show_slash is False
        assert g.show_numbered is False

    def test_score_view_dataclass_defaults(self):
        sv = PartConfigurationScoreView()
        assert sv.is_multi_rest is False
        assert sv.track_view_groups == []
