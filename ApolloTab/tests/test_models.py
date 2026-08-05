"""
ApolloTab/tests/test_models.py

数据模型纯逻辑测试 (ApolloTab v1.4.1)

覆盖范围:
  - GTPNote: get_display_fret / has_technique / add_technique 去重
  - BendData: get_display_text (度数映射) / has_release (释放段判定)
  - GTPBeat: is_empty / duration_value (附点+连音) / get_highest/lowest_string
  - GTPMeasure: total_duration (拍号) / actual_duration / is_full
  - GTPTrack: string_count / total_measures / get_tuning_name / get_total_beats
  - GTPSong: track_count / visible_tracks / total_measures / get_track_by_name
            / get_primary_guitar_track

运行命令: python -m pytest ApolloTab/tests/test_models.py -q
"""

from __future__ import annotations

import pytest

from ApolloTab.models.beat import GTPBeat
from ApolloTab.models.chord import Chord
from ApolloTab.models.measure import GTPMeasure
from ApolloTab.models.note import BendData, GTPNote
from ApolloTab.models.song import GTPSong
from ApolloTab.models.track import GTPTrack, PercussionArticulation
from ApolloTab.utils.constants import (
    NoteDuration,
    StandardTunings,
    TechniqueType,
)

# ============================================================
# GTPNote
# ============================================================


class TestGTPNote:
    """GTPNote 显示与技巧管理"""

    def test_display_fret_normal(self):
        n = GTPNote(fret=7)
        assert n.get_display_fret() == "7"

    def test_display_fret_zero_open_string(self):
        n = GTPNote(fret=0)
        assert n.get_display_fret() == "0"

    def test_display_fret_ghost_wraps_parentheses(self):
        """幽灵音用括号包裹品格"""
        n = GTPNote(fret=5, is_ghost=True)
        assert n.get_display_fret() == "(5)"

    def test_has_technique_false_by_default(self):
        n = GTPNote()
        assert n.has_technique(TechniqueType.HAMMER_ON) is False

    def test_add_technique_then_has(self):
        n = GTPNote()
        n.add_technique(TechniqueType.HAMMER_ON)
        assert n.has_technique(TechniqueType.HAMMER_ON) is True

    def test_add_technique_dedup(self):
        """重复添加同一技巧不重复入队"""
        n = GTPNote()
        n.add_technique(TechniqueType.VIBRATO)
        n.add_technique(TechniqueType.VIBRATO)
        assert len(n.techniques) == 1

    def test_add_multiple_distinct_techniques(self):
        n = GTPNote()
        n.add_technique(TechniqueType.HAMMER_ON)
        n.add_technique(TechniqueType.PULL_OFF)
        assert len(n.techniques) == 2

    def test_default_values(self):
        """默认值: velocity=95, 非休止, 非幽灵"""
        n = GTPNote()
        assert n.velocity == 95
        assert n.is_rest is False
        assert n.is_ghost is False
        assert n.duration == NoteDuration.QUARTER
        assert n.techniques == []


# ============================================================
# BendData
# ============================================================


class TestBendData:
    """推弦数据显示文本与释放段判定"""

    @pytest.mark.parametrize(
        "max_val,expected",
        [
            (25, "1/4"),
            (50, "1/2"),
            (75, "3/4"),
            (100, "Full"),
        ],
    )
    def test_display_text_standard_values(self, max_val, expected):
        b = BendData(max_value=max_val)
        assert b.get_display_text() == expected

    def test_display_text_unknown_defaults_full(self):
        """未知 max_value 默认返回 Full"""
        b = BendData(max_value=33)
        assert b.get_display_text() == "Full"

    def test_has_release_false_when_no_points(self):
        b = BendData(max_value=100)
        assert b.has_release is False

    def test_has_release_true_when_endpoint_below_peak(self):
        """终点 value 低于峰值 → 有释放段 (points 为 (pos, val) 元组)"""
        b = BendData(max_value=100, points=[(0, 0), (60, 100), (120, 40)])
        assert b.has_release is True

    def test_has_release_false_when_endpoint_at_peak(self):
        """终点 value 等于峰值 → 无释放段"""
        b = BendData(max_value=100, points=[(0, 0), (60, 100)])
        assert b.has_release is False

    def test_has_release_false_when_peak_zero(self):
        """峰值=0 时即使终点低也不算释放"""
        b = BendData(max_value=0, points=[(0, 0), (60, 50)])
        assert b.has_release is False


# ============================================================
# GTPBeat
# ============================================================


class TestGTPBeat:
    """拍时值计算与弦序查询"""

    def test_is_empty_default_beat(self):
        """无音符且非休止 → 空"""
        b = GTPBeat()
        assert b.is_empty is True

    def test_is_empty_with_notes_false(self):
        b = GTPBeat(notes=[GTPNote()])
        assert b.is_empty is False

    def test_is_empty_with_rest_false(self):
        b = GTPBeat(is_rest=True)
        assert b.is_empty is False

    @pytest.mark.parametrize(
        "duration,expected",
        [
            (NoteDuration.WHOLE, 4.0),
            (NoteDuration.HALF, 2.0),
            (NoteDuration.QUARTER, 1.0),
            (NoteDuration.EIGHTH, 0.5),
            (NoteDuration.SIXTEENTH, 0.25),
            (NoteDuration.THIRTY_SECOND, 0.125),
        ],
    )
    def test_duration_value_basic(self, duration, expected):
        b = GTPBeat(duration=duration)
        assert b.duration_value == expected

    def test_duration_value_dotted_quarter(self):
        """附点四分音符 = 1.0 × 1.5 = 1.5"""
        b = GTPBeat(duration=NoteDuration.QUARTER, is_dotted=True)
        assert b.duration_value == 1.5

    def test_duration_value_dotted_eighth(self):
        """附点八分音符 = 0.5 × 1.5 = 0.75"""
        b = GTPBeat(duration=NoteDuration.EIGHTH, is_dotted=True)
        assert b.duration_value == 0.75

    def test_duration_value_triplet(self):
        """三连音: base × (2/3); 四分三连音 = 1.0 × 2/3"""
        b = GTPBeat(duration=NoteDuration.QUARTER, tuplet_numerator=3, tuplet_denominator=2)
        assert abs(b.duration_value - (1.0 * 2 / 3)) < 1e-9

    def test_duration_value_quintuplet(self):
        """五连音: base × (4/5); 八分五连音 = 0.5 × 4/5"""
        b = GTPBeat(duration=NoteDuration.EIGHTH, tuplet_numerator=5, tuplet_denominator=4)
        assert abs(b.duration_value - (0.5 * 4 / 5)) < 1e-9

    def test_duration_value_no_tuplet_when_negative(self):
        """tuplet 为 -1 时不应用连音修正"""
        b = GTPBeat(duration=NoteDuration.QUARTER, tuplet_numerator=-1, tuplet_denominator=-1)
        assert b.duration_value == 1.0

    def test_get_highest_string_empty_returns_minus_one(self):
        assert GTPBeat().get_highest_string() == -1

    def test_get_highest_string_min_index(self):
        """最高(最细)弦 = 最小 string 索引 (0=1弦)"""
        b = GTPBeat(notes=[GTPNote(string=3), GTPNote(string=0), GTPNote(string=2)])
        assert b.get_highest_string() == 0

    def test_get_lowest_string_empty_returns_minus_one(self):
        assert GTPBeat().get_lowest_string() == -1

    def test_get_lowest_string_max_index(self):
        """最低(最粗)弦 = 最大 string 索引 (5=6弦)"""
        b = GTPBeat(notes=[GTPNote(string=1), GTPNote(string=5), GTPNote(string=2)])
        assert b.get_lowest_string() == 5


# ============================================================
# GTPMeasure
# ============================================================


class TestGTPMeasure:
    """小节时长与填满判定"""

    @pytest.mark.parametrize(
        "sig,expected",
        [
            ((4, 4), 4.0),
            ((3, 4), 3.0),
            ((6, 8), 3.0),
            ((2, 2), 4.0),
            ((7, 8), 3.5),
        ],
    )
    def test_total_duration_by_time_signature(self, sig, expected):
        m = GTPMeasure(time_signature=sig)
        assert m.total_duration == expected

    def test_actual_duration_sum_of_beats(self):
        m = GTPMeasure(
            beats=[
                GTPBeat(duration=NoteDuration.QUARTER),
                GTPBeat(duration=NoteDuration.QUARTER),
                GTPBeat(duration=NoteDuration.HALF),
            ]
        )
        assert m.actual_duration == 4.0

    def test_is_full_when_matches(self):
        m = GTPMeasure(
            time_signature=(4, 4),
            beats=[
                GTPBeat(duration=NoteDuration.QUARTER),
                GTPBeat(duration=NoteDuration.QUARTER),
                GTPBeat(duration=NoteDuration.HALF),
            ],
        )
        assert m.is_full() is True

    def test_is_full_false_when_underfilled(self):
        m = GTPMeasure(
            time_signature=(4, 4),
            beats=[
                GTPBeat(duration=NoteDuration.QUARTER),
            ],
        )
        assert m.is_full() is False

    def test_is_full_with_dotted_complements(self):
        """附点四分(1.5) + 八分(0.5) + 四分(1) + 四分(1) = 4.0 → 填满"""
        m = GTPMeasure(
            time_signature=(4, 4),
            beats=[
                GTPBeat(duration=NoteDuration.QUARTER, is_dotted=True),
                GTPBeat(duration=NoteDuration.EIGHTH),
                GTPBeat(duration=NoteDuration.QUARTER),
                GTPBeat(duration=NoteDuration.QUARTER),
            ],
        )
        assert m.is_full() is True

    def test_default_values(self):
        m = GTPMeasure()
        assert m.time_signature == (4, 4)
        assert m.beats == []
        assert m.is_repeat_open is False
        assert m.repeat_close == -1
        assert m.key_signature == 0


# ============================================================
# GTPTrack
# ============================================================


class TestGTPTrack:
    """音轨调弦识别与统计"""

    def test_string_count_default_six(self):
        assert GTPTrack().string_count == 6

    def test_string_count_custom(self):
        t = GTPTrack(strings=(64, 59, 55, 50, 45, 40, 35))
        assert t.string_count == 7

    def test_total_measures_empty(self):
        assert GTPTrack().total_measures == 0

    def test_total_measures_with_measures(self):
        t = GTPTrack(measures=[GTPMeasure(), GTPMeasure(), GTPMeasure()])
        assert t.total_measures == 3

    def test_get_total_beats(self):
        m1 = GTPMeasure(beats=[GTPBeat(), GTPBeat()])
        m2 = GTPMeasure(beats=[GTPBeat()])
        t = GTPTrack(measures=[m1, m2])
        assert t.get_total_beats() == 3

    @pytest.mark.parametrize(
        "tuning,name",
        [
            (StandardTunings.STANDARD, "Standard"),
            (StandardTunings.DROP_D, "Drop D"),
            (StandardTunings.OPEN_G, "Open G"),
            (StandardTunings.OPEN_D, "Open D"),
            (StandardTunings.DADGAD, "DADGAD"),
            (StandardTunings.HALF_STEP_DOWN, "Half Step Down"),
        ],
    )
    def test_get_tuning_name_known(self, tuning, name):
        t = GTPTrack(strings=tuning)
        assert t.get_tuning_name() == name

    def test_get_tuning_name_custom(self):
        """未知调弦返回 Custom 描述"""
        t = GTPTrack(strings=(60, 55, 50, 45, 40, 35))
        assert t.get_tuning_name() == "Custom (6 strings)"

    def test_get_tuning_name_custom_seven_strings(self):
        t = GTPTrack(strings=(64, 59, 55, 50, 45, 40, 35))
        assert t.get_tuning_name() == "Custom (7 strings)"

    def test_percussion_articulation_default(self):
        pa = PercussionArticulation()
        assert pa.output_midi_number == -1
        assert pa.staff_line == 0

    def test_percussion_articulation_fields(self):
        pa = PercussionArticulation(
            name="Snare", element_type="Snare", output_midi_number=38, staff_line=2
        )
        assert pa.name == "Snare"
        assert pa.output_midi_number == 38


# ============================================================
# GTPSong
# ============================================================


class TestGTPSong:
    """歌曲顶层容器查询"""

    def test_track_count_empty(self):
        assert GTPSong().track_count == 0

    def test_track_count(self):
        s = GTPSong(tracks=[GTPTrack(), GTPTrack()])
        assert s.track_count == 2

    def test_visible_tracks_filters_hidden(self):
        t1 = GTPTrack(name="visible", is_visible=True)
        t2 = GTPTrack(name="hidden", is_visible=False)
        s = GTPSong(tracks=[t1, t2])
        assert s.visible_tracks == [t1]

    def test_total_measures_empty_returns_zero(self):
        assert GTPSong().total_measures == 0

    def test_total_measures_takes_first_track(self):
        t1 = GTPTrack(measures=[GTPMeasure(), GTPMeasure()])
        t2 = GTPTrack(measures=[GTPMeasure()])
        s = GTPSong(tracks=[t1, t2])
        assert s.total_measures == 2  # 取第一条轨道

    def test_get_track_by_name_case_insensitive(self):
        t = GTPTrack(name="Lead Guitar")
        s = GTPSong(tracks=[t])
        assert s.get_track_by_name("lead guitar") is t
        assert s.get_track_by_name("LEAD GUITAR") is t

    def test_get_track_by_name_not_found(self):
        s = GTPSong(tracks=[GTPTrack(name="Bass")])
        assert s.get_track_by_name("Piano") is None

    def test_get_primary_guitar_track_prefers_guitar(self):
        """优先选择可见的吉他乐器 (24-30) 轨道"""
        piano = GTPTrack(name="Piano", instrument=0)
        guitar = GTPTrack(name="Lead", instrument=29)
        s = GTPSong(tracks=[piano, guitar])
        assert s.get_primary_guitar_track() is guitar

    def test_get_primary_guitar_track_falls_back_to_visible(self):
        """无吉他时回退到第一个可见轨道"""
        piano = GTPTrack(name="Piano", instrument=0)
        s = GTPSong(tracks=[piano])
        assert s.get_primary_guitar_track() is piano

    def test_get_primary_guitar_track_skips_mute(self):
        """静音轨道被跳过"""
        mute_guitar = GTPTrack(name="Muted", instrument=29, is_mute=True)
        clean_guitar = GTPTrack(name="Clean", instrument=27)
        s = GTPSong(tracks=[mute_guitar, clean_guitar])
        assert s.get_primary_guitar_track() is clean_guitar

    def test_get_primary_guitar_track_none_when_no_visible(self):
        s = GTPSong(tracks=[GTPTrack(is_visible=False)])
        assert s.get_primary_guitar_track() is None

    def test_default_tempo(self):
        assert GTPSong().tempo == 120
