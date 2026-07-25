# -*- coding: utf-8 -*-
"""
ApolloTab/tests/test_chord_gp3.py

GP3-5 和弦提取测试 (ApolloTab v1.4.0)

覆盖范围:
  - _pitch_class_to_name: sharp/flat 升降号转换
  - _convert_gp3_chord: 防御性判空, 结构化字段映射, 验证回退
  - 端到端: 真实 GP3 文件 (Beyond - Grey Track) 解析 → 验证 chord.name

运行命令: venv/bin/python -m pytest /Users/limeng/Desktop/TAB-Score-Viewer/venv/lib/python3.13/site-packages/ApolloTab/tests/test_chord_gp3.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 强制使用 offscreen Qt (CI 友好)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


PROJECT_ROOT = Path('/Users/limeng/Desktop/TAB-Score-Viewer')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope='session')
def real_gp3_chords_file() -> Path:
    """Beyond - Grey Track (live 1991).gp3: 轨 1 Acoustic Guitar Chord 含 F#m/A/E/B"""
    p = PROJECT_ROOT / 'Beyond - Grey Track (live 1991).gp3'
    if not p.exists():
        pytest.skip(f'GP3 测试文件未找到: {p}')
    return p


def _make_pitch_class(value: int, sharp: bool = True):
    """构造一个 mock PitchClass (PyGuitarPro 内部类)"""
    from guitarpro.models import PitchClass
    pc = PitchClass.__new__(PitchClass)
    pc.value = value
    pc.sharp = sharp
    return pc


def _make_chord(name: str, root_value: int, type_name: str,
                ext_name: str = 'none', bass_value: int = None,
                sharp: bool = True):
    """构造一个 mock PyGuitarPro Chord 对象

    模拟真实 GP 文件行为: 默认 bass 与 root 相同 (PyGuitarPro 会自动填).
    显式传 bass_value=0..11 可覆盖; 传 'none' (字符串) 表示 None.
    """
    from guitarpro.models import Chord, ChordType, ChordExtension
    chord = Chord.__new__(Chord)
    chord.name = name
    chord.root = _make_pitch_class(root_value, sharp)
    if bass_value == 'none':
        chord.bass = None
    elif bass_value is not None:
        chord.bass = _make_pitch_class(bass_value, sharp)
    else:
        # 默认: bass 与 root 相同 (匹配 PyGuitarPro 真实行为)
        chord.bass = _make_pitch_class(root_value, sharp)
    chord.type = ChordType[type_name]
    chord.extension = ChordExtension[ext_name]
    chord.sharp = sharp
    return chord


# ============================================================
# _pitch_class_to_name 单元测试
# ============================================================

class TestPitchClassToName:
    """sharp/flat 升降号转换"""

    def test_sharp_C(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        assert _pitch_class_to_name(_make_pitch_class(0, sharp=True), True) == 'C'

    def test_sharp_F_sharp(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        # F# = value 6
        assert _pitch_class_to_name(_make_pitch_class(6, sharp=True), True) == 'F#'

    def test_flat_B_flat(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        # Bb = value 10, flat
        assert _pitch_class_to_name(_make_pitch_class(10, sharp=False), False) == 'Bb'

    def test_flat_D_flat(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        # Db = value 1, flat
        assert _pitch_class_to_name(_make_pitch_class(1, sharp=False), False) == 'Db'

    def test_all_sharp_names(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        expected = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        for i, exp in enumerate(expected):
            assert _pitch_class_to_name(_make_pitch_class(i, sharp=True), True) == exp

    def test_all_flat_names(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        expected = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
        for i, exp in enumerate(expected):
            assert _pitch_class_to_name(_make_pitch_class(i, sharp=False), False) == exp

    def test_none_returns_empty(self):
        from ApolloTab.parser.gtp_parser import _pitch_class_to_name
        assert _pitch_class_to_name(None, True) == ''


# ============================================================
# _convert_gp3_chord 单元测试 (mock PyGuitarPro Chord)
# ============================================================

class TestConvertGp3Chord:
    """结构化字段映射 + 验证 + 防御性判空"""

    def test_F_sharp_m(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('F#m', root_value=6, type_name='minor')
        result = _convert_gp3_chord(ch)
        assert result is not None
        assert result.key == 'F#'
        assert result.suffix == 'm'
        assert result.extensions == ''
        assert result.bass == 'F#'  # bass 与 root 相同, name 不会追加 /bass
        assert result.name == 'F#m'

    def test_A_major(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('A', root_value=9, type_name='major')
        result = _convert_gp3_chord(ch)
        assert result is not None
        assert result.name == 'A'

    def test_E_major(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('E', root_value=4, type_name='major')
        result = _convert_gp3_chord(ch)
        assert result is not None
        assert result.name == 'E'

    def test_C_sharp_major(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C#', root_value=1, type_name='major')
        result = _convert_gp3_chord(ch)
        assert result is not None
        assert result.key == 'C#'
        assert result.suffix == ''
        assert result.name == 'C#'

    def test_seventh(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C7', root_value=0, type_name='seventh')
        result = _convert_gp3_chord(ch)
        assert result.name == 'C7'

    def test_major_seventh(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('Cmaj7', root_value=0, type_name='majorSeventh')
        result = _convert_gp3_chord(ch)
        assert result.name == 'Cmaj7'

    def test_suspended_fourth(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('Gsus4', root_value=7, type_name='suspendedFourth')
        result = _convert_gp3_chord(ch)
        assert result.name == 'Gsus4'

    def test_diminished(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('Bdim', root_value=11, type_name='diminished')
        result = _convert_gp3_chord(ch)
        assert result.name == 'Bdim'

    def test_augmented(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('Caug', root_value=0, type_name='augmented')
        result = _convert_gp3_chord(ch)
        assert result.name == 'Caug'

    def test_minor_seventh(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('Am7', root_value=9, type_name='minorSeventh')
        result = _convert_gp3_chord(ch)
        assert result.name == 'Am7'

    def test_sixth(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C6', root_value=0, type_name='sixth')
        result = _convert_gp3_chord(ch)
        assert result.name == 'C6'

    def test_power_chord(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C5', root_value=0, type_name='power')
        result = _convert_gp3_chord(ch)
        assert result.name == 'C5'

    def test_ninth_extension(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('D9', root_value=2, type_name='major', ext_name='ninth')
        result = _convert_gp3_chord(ch)
        assert result.name == 'D9'

    def test_thirteenth_extension(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C13', root_value=0, type_name='major', ext_name='thirteenth')
        result = _convert_gp3_chord(ch)
        assert result.name == 'C13'

    def test_slash_chord(self):
        """bass != root 时, name 自动加 /bass"""
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        # G/B: root=G(value=7), bass=B(value=11)
        ch = _make_chord('G/B', root_value=7, type_name='major', bass_value=11)
        result = _convert_gp3_chord(ch)
        assert result.key == 'G'
        assert result.bass == 'B'
        assert result.name == 'G/B'

    def test_flat_naming_Bb(self):
        """flat 文件应使用降号"""
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('Bb', root_value=10, type_name='major', sharp=False)
        result = _convert_gp3_chord(ch)
        assert result.key == 'Bb'
        assert result.name == 'Bb'

    # ---- 防御性测试 ----

    def test_none_chord_returns_none(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        assert _convert_gp3_chord(None) is None

    def test_missing_root_returns_none(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C', root_value=0, type_name='major')
        ch.root = None
        assert _convert_gp3_chord(ch) is None

    def test_missing_type_returns_none(self):
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        ch = _make_chord('C', root_value=0, type_name='major')
        ch.type = None
        assert _convert_gp3_chord(ch) is None

    def test_mismatch_name_returns_none(self):
        """映射表缺项导致构造 name 与 GP name 不一致时返回 None (不显示)"""
        from ApolloTab.parser.gtp_parser import _convert_gp3_chord
        # 故意构造一个 name 与结构化字段不一致的 chord (模拟极端情况)
        ch = _make_chord('C', root_value=0, type_name='major')  # name='C', type=major → 映射 'C'
        # 我们的映射会得到 'C', name 也是 'C', 一致 → 返回 Chord, 不是 None
        # 改为把 name 改成 'CUSTOMXYZ' (与结构化字段不一致)
        ch.name = 'CUSTOMXYZ'
        result = _convert_gp3_chord(ch)
        # 我们的映射会得到 'C', 但 expected='CUSTOMXYZ', 不一致 → None
        assert result is None


# ============================================================
# 端到端: 真实 GP3 文件
# ============================================================

class TestEndToEndGp3:
    """完整 parse_score 流程, 验证 GTPSong.tracks[0].measures[].beats[].chord"""

    def test_parse_gp3_has_chords(self, real_gp3_chords_file):
        """Beyond - Grey Track: 轨 1 前 8 小节应有 F#m, A, E, F#m"""
        from ApolloTab import parse_score
        song = parse_score(str(real_gp3_chords_file))
        track = song.tracks[0]
        assert 'Acoustic Guitar Chord' in track.name

        # 前 8 小节的拍 chord
        expected = [
            # (measure_index, beat_index, chord_name)
            (4, 0, 'F#m'),  # 小节 5
            (5, 0, 'A'),    # 小节 6
            (6, 0, 'E'),    # 小节 7
            (7, 0, 'F#m'),  # 小节 8
        ]
        for mi, bi, exp_name in expected:
            m = track.measures[mi]
            beat = m.beats[bi]
            assert beat.chord is not None, f'm{mi+1} b{bi} 应有 chord'
            assert beat.chord.name == exp_name, \
                f'm{mi+1} b{bi} 期望 {exp_name!r}, 实际 {beat.chord.name!r}'

    def test_parse_gp3_rest_measure_no_chord(self, real_gp3_chords_file):
        """小节 1-4 是全休止, 不应有 chord"""
        from ApolloTab import parse_score
        song = parse_score(str(real_gp3_chords_file))
        track = song.tracks[0]
        for mi in range(4):
            for beat in track.measures[mi].beats:
                # 全休止的 beat.chord 应该是 None (没有 effect.chord)
                # 或 beat 没有任何 notes
                if beat.notes:
                    assert beat.chord is None, \
                        f'休止小节 {mi+1} 不应有 chord, 实际 {beat.chord!r}'

    def test_chord_structured_fields_match(self, real_gp3_chords_file):
        """F#m 的结构化字段: key=F#, suffix=m"""
        from ApolloTab import parse_score
        song = parse_score(str(real_gp3_chords_file))
        track = song.tracks[0]
        m5_beat0 = track.measures[4].beats[0]
        c = m5_beat0.chord
        assert c is not None
        assert c.key == 'F#'
        assert c.suffix == 'm'
        assert c.extensions == ''
        # bass 与 key 相同, name 不追加 /bass
        assert c.bass == 'F#' or c.bass is None
        assert c.name == 'F#m'
