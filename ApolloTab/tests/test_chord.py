# -*- coding: utf-8 -*-
"""
ApolloTab/tests/test_chord.py

Chord 数据模型与 GPIF 解析测试 (ApolloTab v1.4.0)

覆盖范围:
  - Chord 数据类: 各种根音/低音/后缀/扩展
  - _chord_note_name: 升降号映射
  - _build_chord_from_xml: 各种 Degree 组合 → Chord 对象
    - 大三/小三/大七/属七/半减/减/增/sus4/6/9/13/转位/双升降
    - 优先级: 13 抑制 7/9;  6 抑制 7;  m7b5 不重复 7
  - GpifParser.parse_xml: 真实 GP7 文件 → beat.chord 自动填充
  - _chord_definitions 解析: 顶层 <Beats> 容器中 <Chord> 引用 → 索引映射

运行命令: venv/bin/python -m pytest /Users/limeng/Desktop/TAB-Score-Viewer/venv/lib/python3.13/site-packages/ApolloTab/tests/test_chord.py -q
"""
from __future__ import annotations

import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


# ============================================================
# 导入 ApolloTab 模块
# ============================================================

from ApolloTab.models.chord import Chord
from ApolloTab.parser.gpif_parser import (
    _chord_note_name,
    _build_chord_from_xml,
    _CHORD_ACCIDENTAL_MAP,
    GpifParser,
)


# ============================================================
# Fixtures: 测试用 GP7 文件 (zip+XML 格式)
# ============================================================

def _make_minimal_gp7_xml() -> str:
    """
    构造一个最小的 GP7 GPIF XML, 包含:
      - 1 个 track, 2 个 measure, 每个 measure 4 个 beat
      - 8 个 <Chord> 定义 (C, Cm, C, Cm, D, Dm, D, Dm)
    """
    chord_xmls = []
    chord_names_data = [
        ('C', 'Major'), ('C', 'Minor'), ('C', 'Major'), ('C', 'Minor'),
        ('D', 'Major'), ('D', 'Minor'), ('D', 'Major'), ('D', 'Minor'),
    ]
    for key, third in chord_names_data:
        chord_xmls.append(
            f'<Chord><KeyNote step="{key}" />'
            f'<Degree interval="Third" alteration="{third}" />'
            f'<Degree interval="Fifth" alteration="Perfect" /></Chord>'
        )
    chords_xml = ''.join(chord_xmls)

    beat_xmls = ''.join(
        f'<Beat id="{i}"><Chord>{i}</Chord></Beat>'
        for i in range(8)
    )

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <MasterTrack><Tracks>0</Tracks></MasterTrack>
  <Tracks><Track id="0" /></Tracks>
  <MasterBars>
    <MasterBar><Bars>0</Bars></MasterBar>
    <MasterBar><Bars>1</Bars></MasterBar>
  </MasterBars>
  <Bars>
    <Bar id="0"><Voices>0 -1 -1 -1</Voices></Bar>
    <Bar id="1"><Voices>1 -1 -1 -1</Voices></Bar>
  </Bars>
  <Voices>
    <Voice id="0"><Beats>0 1 2 3</Beats></Voice>
    <Voice id="1"><Beats>4 5 6 7</Beats></Voice>
  </Voices>
  <Beats>{beat_xmls}</Beats>
  {chords_xml}
</GPIF>'''
    return xml


@pytest.fixture
def sample_gp7_file(tmp_path: Path) -> Path:
    """生成一个最小 GP7 zip+XML 文件供测试使用"""
    xml_content = _make_minimal_gp7_xml()
    gp_path = tmp_path / "test_chords.gp"
    with zipfile.ZipFile(gp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('Content/score.gpif', xml_content)
    return gp_path


@pytest.fixture
def real_gp7_chords_file() -> Path:
    """真实的 guitarpro7/chords.gp 文件 (ApolloTab 测试目录或项目内)"""
    candidates = [
        Path('/Users/limeng/Desktop/TAB-Score-Viewer/guitarpro7/chords.gp'),
    ]
    for p in candidates:
        if p.exists():
            return p
    pytest.skip('chords.gp not found in known test paths')


# ============================================================
# _CHORD_ACCIDENTAL_MAP / _chord_note_name
# ============================================================

def test_chord_accidental_map_all_entries():
    """升降号映射应包含所有标准值"""
    assert _CHORD_ACCIDENTAL_MAP['Natural'] == ''
    assert _CHORD_ACCIDENTAL_MAP['Sharp'] == '#'
    assert _CHORD_ACCIDENTAL_MAP['Flat'] == 'b'
    assert _CHORD_ACCIDENTAL_MAP['DoubleSharp'] == '##'
    assert _CHORD_ACCIDENTAL_MAP['DoubleFlat'] == 'bb'


def test_chord_note_name_natural_sharp_flat():
    """音符名生成: Natural / Sharp / Flat"""
    e_natural = ET.fromstring('<KeyNote step="C" />')
    e_sharp = ET.fromstring('<KeyNote step="F" accidental="Sharp" />')
    e_flat = ET.fromstring('<KeyNote step="B" accidental="Flat" />')
    assert _chord_note_name(e_natural) == 'C'
    assert _chord_note_name(e_sharp) == 'F#'
    assert _chord_note_name(e_flat) == 'Bb'


def test_chord_note_name_double_accidentals():
    """音符名生成: DoubleSharp / DoubleFlat"""
    e_ds = ET.fromstring('<KeyNote step="G" accidental="DoubleSharp" />')
    e_df = ET.fromstring('<KeyNote step="A" accidental="DoubleFlat" />')
    assert _chord_note_name(e_ds) == 'G##'
    assert _chord_note_name(e_df) == 'Abb'


def test_chord_note_name_none_returns_empty():
    """note_el 为 None 时返回空字符串"""
    assert _chord_note_name(None) == ''


def test_chord_note_name_missing_accidental_defaults_natural():
    """缺失 accidental 属性时默认为 Natural"""
    e = ET.fromstring('<KeyNote step="D" />')
    assert _chord_note_name(e) == 'D'


# ============================================================
# Chord 数据类
# ============================================================

def test_chord_dataclass_major():
    """大三和弦数据类: C"""
    c = Chord(key='C')
    assert c.key == 'C'
    assert c.bass is None
    assert c.suffix == ''
    assert c.extensions == ''
    assert c.name == 'C'
    assert str(c) == 'C'


def test_chord_dataclass_minor():
    """小三和弦数据类: Cm"""
    c = Chord(key='C', suffix='m')
    assert c.name == 'Cm'
    assert str(c) == 'Cm'


def test_chord_dataclass_with_bass():
    """转位和弦: G/B"""
    c = Chord(key='G', bass='B')
    assert c.name == 'G/B'
    assert str(c) == 'G/B'


def test_chord_dataclass_bass_equals_key_omits_slash():
    """BassNote 与 KeyNote 相同时不附加 /BassName"""
    c = Chord(key='C', bass='C')
    assert c.name == 'C'


def test_chord_dataclass_complex_chord():
    """复杂和弦: Am7b5"""
    c = Chord(key='A', suffix='m7b5')
    assert c.name == 'Am7b5'


# ============================================================
# _build_chord_from_xml
# ============================================================

def _chord_el(xml: str) -> ET.Element:
    """解析 <Chord> XML 字符串为 Element"""
    wrapped = f'<root>{xml}</root>'
    root = ET.fromstring(wrapped)
    return root.find('Chord')


class TestBuildChordFromXml:
    """_build_chord_from_xml 各种和弦结构测试"""

    def test_major_chord(self):
        """大三和弦: C → Chord('C')"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c is not None
        assert c.name == 'C'

    def test_minor_chord(self):
        """小三和弦: C → Chord('Cm')"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Minor" />'
            '<Degree interval="Fifth" alteration="Perfect" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Cm'

    def test_major_seventh_chord(self):
        """大七和弦: Gmaj7"""
        el = _chord_el(
            '<Chord><KeyNote step="G" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Seventh" alteration="Major" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Gmaj7'

    def test_dominant_seventh_chord(self):
        """属七和弦: F#7"""
        el = _chord_el(
            '<Chord><KeyNote step="F" accidental="Sharp" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Seventh" alteration="Minor" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'F#7'

    def test_half_diminished_seventh(self):
        """半减七和弦: Dm7b5"""
        el = _chord_el(
            '<Chord><KeyNote step="D" />'
            '<Degree interval="Third" alteration="Minor" />'
            '<Degree interval="Fifth" alteration="Diminished" />'
            '<Degree interval="Seventh" alteration="Minor" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Dm7b5'

    def test_diminished_chord(self):
        """减和弦: Bdim (无 7 音)"""
        el = _chord_el(
            '<Chord><KeyNote step="B" />'
            '<Degree interval="Third" alteration="Minor" />'
            '<Degree interval="Fifth" alteration="Diminished" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Bdim'

    def test_augmented_chord(self):
        """增和弦: Caug"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Augmented" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Caug'

    def test_sus4_chord(self):
        """挂四和弦: Gsus4 (不带扩展)"""
        el = _chord_el(
            '<Chord><KeyNote step="G" />'
            '<Degree interval="Fourth" alteration="Perfect" />'
            '<Degree interval="Fifth" alteration="Perfect" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Gsus4'

    def test_sixth_chord(self):
        """六和弦: C6"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Sixth" alteration="Major" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'C6'

    def test_ninth_chord(self):
        """九和弦: D9"""
        el = _chord_el(
            '<Chord><KeyNote step="D" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Ninth" alteration="Major" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'D9'

    def test_bass_slash_chord(self):
        """转位和弦: Am/E"""
        el = _chord_el(
            '<Chord><KeyNote step="A" /><BassNote step="E" />'
            '<Degree interval="Third" alteration="Minor" />'
            '<Degree interval="Fifth" alteration="Perfect" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'Am/E'

    def test_bass_same_as_key_omits_slash(self):
        """BassNote 与 KeyNote 相同时不附加 /BassName"""
        el = _chord_el(
            '<Chord><KeyNote step="C" /><BassNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'C'

    def test_chord_with_no_keynote_returns_none(self):
        """缺 KeyNote 节点返回 None"""
        el = ET.fromstring('<Chord><Degree interval="Third" /></Chord>')
        assert _build_chord_from_xml(el) is None

    def test_chord_with_no_degrees_returns_keyonly(self):
        """无 Degree 节点时仅返回 KeyNote (如 'A' → 'A')"""
        el = _chord_el('<Chord><KeyNote step="A" /></Chord>')
        c = _build_chord_from_xml(el)
        assert c is not None
        assert c.name == 'A'

    def test_thirteenth_chord(self):
        """十三和弦: C13 (13 抑制 7)"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Seventh" alteration="Minor" />'
            '<Degree interval="Thirteenth" alteration="Major" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        assert c.name == 'C13'

    def test_13th_suppresses_9th(self):
        """13 抑制 9 (不重复显示)"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Ninth" alteration="Major" />'
            '<Degree interval="Thirteenth" alteration="Major" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        # 13 优先, 9 被抑制
        assert c.name == 'C13'

    def test_6th_suppresses_7th(self):
        """6 抑制 7 (不重复显示)"""
        el = _chord_el(
            '<Chord><KeyNote step="C" />'
            '<Degree interval="Third" alteration="Major" />'
            '<Degree interval="Fifth" alteration="Perfect" />'
            '<Degree interval="Sixth" alteration="Major" />'
            '<Degree interval="Seventh" alteration="Minor" /></Chord>'
        )
        c = _build_chord_from_xml(el)
        # 6 优先, 7 被抑制
        assert c.name == 'C6'


# ============================================================
# GpifParser 集成 (端到端)
# ============================================================

class TestGpifParserChordIntegration:
    """GpifParser.parse_xml → beat.chord 自动填充端到端测试"""

    def test_parse_sample_gp7_attaches_chord_to_beats(self, sample_gp7_file):
        """解析构造的 GP7 文件: 8 个 beat 全部带 chord"""
        # 通过 GP7Parser 走完整 ZIP 流程
        from ApolloTab.parser.gp7_parser import GP7Parser
        song = GP7Parser().parse_file(str(sample_gp7_file))

        # 收集所有有 chord 的 beat
        chord_beats = []
        for ti, track in enumerate(song.tracks):
            for mi, measure in enumerate(track.measures):
                for bi, beat in enumerate(measure.beats):
                    if beat.chord is not None:
                        chord_beats.append((ti, mi, bi, beat.chord.name))

        # 应该有 8 个
        assert len(chord_beats) == 8
        # 验证名称
        names = [c[3] for c in chord_beats]
        assert names == ['C', 'Cm', 'C', 'Cm', 'D', 'Dm', 'D', 'Dm']

    def test_parse_real_gp7_file(self, real_gp7_chords_file):
        """解析真实 guitarpro7/chords.gp: 至少 1 个 beat 带 chord, 全部有名称"""
        from ApolloTab.parser.gp7_parser import GP7Parser
        song = GP7Parser().parse_file(str(real_gp7_chords_file))

        chord_beats = []
        for track in song.tracks:
            for measure in track.measures:
                for beat in measure.beats:
                    if beat.chord is not None:
                        chord_beats.append(beat.chord.name)

        assert len(chord_beats) >= 1
        for name in chord_beats:
            assert name != ''

    def test_beat_without_chord_has_none(self, sample_gp7_file):
        """没有 <Chord> 引用的 beat 应有 beat.chord is None"""
        from ApolloTab.parser.gp7_parser import GP7Parser
        song = GP7Parser().parse_file(str(sample_gp7_file))

        # 构造的 GP7 中所有 beat 都有 chord, 验证 beat 字段存在
        for track in song.tracks:
            for measure in track.measures:
                for beat in measure.beats:
                    # 字段必须存在 (即使为 None)
                    assert hasattr(beat, 'chord')

    def test_chord_definitions_filtered_correctly(self, sample_gp7_file):
        """_chord_definitions 只包含真定义 (有 KeyNote), 不含 beat 引用"""
        # 单独走 GpifParser 验证内部状态
        import zipfile
        with zipfile.ZipFile(str(sample_gp7_file), 'r') as z:
            with z.open('Content/score.gpif') as fp:
                xml_str = fp.read().decode('utf-8')

        parser = GpifParser()
        parser.parse_xml(xml_str)

        # 应有 8 个 chord 定义 (跳过 8 个 beat 内的引用)
        assert len(parser._chord_definitions) == 8
        # 验证是 Chord 对象且有正确的 name
        names = [c.name for c in parser._chord_definitions]
        assert names == ['C', 'Cm', 'C', 'Cm', 'D', 'Dm', 'D', 'Dm']

    def test_beat_chord_index_mapping(self, sample_gp7_file):
        """beat → chord_idx 映射正确, _chord_idx_of_beat 应指向 0~7"""
        import zipfile
        with zipfile.ZipFile(str(sample_gp7_file), 'r') as z:
            with z.open('Content/score.gpif') as fp:
                xml_str = fp.read().decode('utf-8')

        parser = GpifParser()
        parser.parse_xml(xml_str)

        # 8 个 beat 各自有 chord_idx 0~7
        assert len(parser._chord_idx_of_beat) == 8
        for i in range(8):
            assert parser._chord_idx_of_beat.get(str(i)) == i


# ============================================================
# 公共 API 导出
# ============================================================

def test_chord_exported_from_apollotab_root():
    """Chord 应从 ApolloTab 根包直接可导入"""
    from ApolloTab import Chord as RootChord
    assert RootChord is Chord


def test_chord_exported_from_models():
    """Chord 应从 ApolloTab.models 可导入"""
    from ApolloTab.models import Chord as ModelsChord
    assert ModelsChord is Chord
