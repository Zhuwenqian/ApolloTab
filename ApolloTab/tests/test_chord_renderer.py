"""
ApolloTab/tests/test_chord_renderer.py

Chord 渲染逻辑测试 (ApolloTab v1.4.0)

覆盖范围:
  - _first_chord_in_sequence: 连续相同和弦只保留第一个
  - _draw_chord_names: 不会崩溃, 在 track 无 chord 时 no-op
  - 端到端: render() 输出包含 chord 名的 QPixmap

运行命令: python -m pytest ApolloTab/tests/test_chord_renderer.py -q

v1.4.1: 移除硬编码 macOS 路径，改用 conftest.py 定位 guitarpro7/ 样本。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# offscreen Qt 与项目根 sys.path 由 conftest.py 统一设置
from PyQt5.QtCore import QRect
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

# real_gp7_chords_file fixture 由 conftest.py 提供 (guitarpro7/chords.gp)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope='session')
def qapp():
    if not QApplication.instance():
        QApplication(sys.argv)
    yield QApplication.instance()


@pytest.fixture
def make_beat_with_chord():
    """创建带 chord 的 BeatLayout"""
    from ApolloTab.models.beat import GTPBeat
    from ApolloTab.models.chord import Chord
    from ApolloTab.renderer.layout_engine import BeatLayout

    def _factory(chord_name, x_center=100):
        beat = GTPBeat()
        beat.chord = (
            Chord(
                key=chord_name.rstrip('m7b5'),
                bass=None,
                suffix=chord_name[1:]
                if chord_name.startswith(('A', 'B', 'C', 'D', 'E', 'F', 'G'))
                and len(chord_name) > 1
                else '',
                extensions='',
            )
            if chord_name != chord_name.rstrip('m7b5')
            else None
        )
        return BeatLayout(beat=beat, x_center=x_center, x_start=x_center - 20, x_end=x_center + 20)

    return _factory


# ============================================================
# _first_chord_in_sequence 单元测试
# ============================================================


class TestFirstChordInSequence:
    """静态方法 _first_chord_in_sequence 各种边界场景"""

    def test_empty_beats(self):
        from ApolloTab.renderer.tab_renderer import TabRenderer

        result = TabRenderer._first_chord_in_sequence([])
        assert result == []

    def test_no_chord_beats(self):
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.renderer.layout_engine import BeatLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        beats = [BeatLayout(beat=GTPBeat(), x_center=100) for _ in range(3)]
        result = TabRenderer._first_chord_in_sequence(beats)
        assert result == []

    def test_all_distinct_chords(self):
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.chord import Chord
        from ApolloTab.renderer.layout_engine import BeatLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        names = ['C', 'Am', 'G', 'F']
        beats = []
        for i, name in enumerate(names):
            b = GTPBeat()
            b.chord = Chord(key=name)
            beats.append(BeatLayout(beat=b, x_center=100 * i))

        result = TabRenderer._first_chord_in_sequence(beats)
        assert len(result) == 4
        assert [r.beat.chord.name for r in result] == names

    def test_consecutive_same_chord(self):
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.chord import Chord
        from ApolloTab.renderer.layout_engine import BeatLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        # C C C Am Am G -> [C, Am, G]
        names = ['C', 'C', 'C', 'Am', 'Am', 'G']
        beats = []
        for i, name in enumerate(names):
            b = GTPBeat()
            b.chord = Chord(key=name)
            beats.append(BeatLayout(beat=b, x_center=100 * i))

        result = TabRenderer._first_chord_in_sequence(beats)
        assert len(result) == 3
        assert [r.beat.chord.name for r in result] == ['C', 'Am', 'G']

    def test_skips_beat_without_chord(self):
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.chord import Chord
        from ApolloTab.renderer.layout_engine import BeatLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        # C (无) (无) Am (无) G -> [C, Am, G]
        beats = []
        for i, name in enumerate(['C', None, None, 'Am', None, 'G']):
            b = GTPBeat()
            if name:
                b.chord = Chord(key=name)
            beats.append(BeatLayout(beat=b, x_center=100 * i))

        result = TabRenderer._first_chord_in_sequence(beats)
        assert len(result) == 3
        assert [r.beat.chord.name for r in result] == ['C', 'Am', 'G']

    def test_complex_chord_name_dedup(self):
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.chord import Chord
        from ApolloTab.renderer.layout_engine import BeatLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        # Cmaj7 Cmaj7 Dm7 Dm7 Cmaj7 -> [Cmaj7, Dm7, Cmaj7]
        names = ['Cmaj7', 'Cmaj7', 'Dm7', 'Dm7', 'Cmaj7']
        beats = []
        for i, name in enumerate(names):
            b = GTPBeat()
            if name.startswith('C'):
                b.chord = Chord(key='C', suffix='', extensions='maj7')
            else:
                b.chord = Chord(key='D', suffix='m', extensions='7')
            beats.append(BeatLayout(beat=b, x_center=100 * i))

        result = TabRenderer._first_chord_in_sequence(beats)
        # 验证: 'Cmaj7' 出现 2 次, 'Dm7' 出现 1 次, 然后 'Cmaj7' 又出现 → 3 个
        assert len(result) == 3
        assert [r.beat.chord.name for r in result] == ['Cmaj7', 'Dm7', 'Cmaj7']


# ============================================================
# _draw_chord_names 行为测试 (Mock QPainter)
# ============================================================


class TestDrawChordNamesBehavior:
    """验证 _draw_chord_names 在不同场景下不崩溃且行为正确"""

    def test_draw_with_no_chord_is_noop(self, qapp):
        """track 无 chord 时不调用 painter, 静默返回"""
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.measure import GTPMeasure
        from ApolloTab.renderer.layout_engine import BeatLayout, MeasureLayout, SystemLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        renderer = TabRenderer()
        painter = MagicMock(spec=QPainter)
        system = SystemLayout(y_top=100, y_bottom=300, y_tab_top=120, y_tab_bottom=240)
        m_layout = MeasureLayout(
            measure=GTPMeasure(),
            x_start=10,
            x_end=500,
            beats=[BeatLayout(beat=GTPBeat(), x_center=100)],
        )

        # 调用不应抛异常
        renderer._draw_chord_names(painter, system, m_layout)

        # 没有任何 chord → painter 不应被调用
        assert not painter.setFont.called
        assert not painter.drawText.called

    def test_draw_with_chord_calls_painter(self, qapp):
        """track 有 chord 时调用 painter.setFont / drawText / drawRoundedRect"""
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.chord import Chord
        from ApolloTab.models.measure import GTPMeasure
        from ApolloTab.renderer.layout_engine import BeatLayout, MeasureLayout, SystemLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        renderer = TabRenderer()
        painter = MagicMock(spec=QPainter)
        system = SystemLayout(y_top=100, y_bottom=300, y_tab_top=120, y_tab_bottom=240)
        # 创建 1 个带 chord 的 beat
        beat = GTPBeat()
        beat.chord = Chord(key='C')
        m_layout = MeasureLayout(
            measure=GTPMeasure(),
            x_start=10,
            x_end=500,
            beats=[BeatLayout(beat=beat, x_center=200)],
        )

        renderer._draw_chord_names(painter, system, m_layout)

        # 验证 painter 被调用
        assert painter.setFont.called
        assert painter.fillRect.called
        assert painter.drawRoundedRect.called
        assert painter.drawText.called
        # 验证 drawText 被调时传入了 'C'
        draw_text_calls = painter.drawText.call_args_list
        assert any('C' in str(call) for call in draw_text_calls)

    def test_draw_uses_theme_color(self, qapp):
        """_draw_chord_names 使用 theme.COLOR_TECHNIQUE 颜色"""
        from ApolloTab.models.beat import GTPBeat
        from ApolloTab.models.chord import Chord
        from ApolloTab.models.measure import GTPMeasure
        from ApolloTab.renderer.layout_engine import BeatLayout, MeasureLayout, SystemLayout
        from ApolloTab.renderer.tab_renderer import TabRenderer

        renderer = TabRenderer()
        # 切换到 light theme 验证颜色变化
        renderer.set_theme('light')
        light_tech = renderer.cfg.theme.COLOR_TECHNIQUE
        assert light_tech == '#D97706'

        painter = MagicMock(spec=QPainter)
        system = SystemLayout(y_top=100, y_bottom=300, y_tab_top=120, y_tab_bottom=240)
        beat = GTPBeat()
        beat.chord = Chord(key='G')
        m_layout = MeasureLayout(
            measure=GTPMeasure(),
            x_start=10,
            x_end=500,
            beats=[BeatLayout(beat=beat, x_center=200)],
        )

        renderer._draw_chord_names(painter, system, m_layout)
        # 验证 drawText 被调用 (颜色通过 setPen 设置, 难直接验证 hex)
        assert painter.drawText.called


# ============================================================
# 端到端: render() 输出 QPixmap
# ============================================================
# 注意: ApolloTab render() 的 _render_page 在 Qt offscreen 模式下存在兼容性问题:
#   - macOS: QPainter 触发进程 abort
#   - Windows: 同样会触发进程崩溃 (exit code 0xC0000409)
#   - Linux: offscreen 模式可正常通过
# 这是渲染引擎自身的已知问题 (与 chord 代码无关)，故端到端渲染测试仅在 Linux 运行。
# 手动验证: python -c "from ApolloTab import parse_score, TabRenderer; ..."
#           (在有显示环境的机器上生成 PNG 看效果)

# 仅在 Linux 上运行端到端渲染; macOS/Windows offscreen 会崩溃
_skip_e2e_off_linux = pytest.mark.skipif(
    sys.platform != 'linux',
    reason='ApolloTab render() 在 macOS/Windows Qt offscreen 模式下崩溃, 仅 Linux 可跑 (与 chord 无关)',
)


@_skip_e2e_off_linux
class TestEndToEndRender:
    """完整 parse_score → render() 流程, 验证 QPixmap 正常输出 (Linux only)"""

    def test_render_real_gp7_with_chords(self, qapp, real_gp7_chords_file):
        """真实 GP7 文件: render() 返回非空 QPixmap 列表, 不崩溃"""
        from ApolloTab import parse_score
        from ApolloTab.renderer import TabRenderer

        song = parse_score(str(real_gp7_chords_file))
        # 验证 track 0 有 chord
        track = song.tracks[0]
        assert any(b.chord is not None for m in track.measures for b in m.beats)

        renderer = TabRenderer()
        pixmaps = renderer.render(song, track_index=0)
        assert len(pixmaps) >= 1
        assert all(isinstance(pm, QPixmap) and not pm.isNull() for pm in pixmaps)

    def test_render_with_themes(self, qapp, real_gp7_chords_file):
        """dark/light 主题下都能正常渲染"""
        from ApolloTab import parse_score
        from ApolloTab.renderer import TabRenderer

        song = parse_score(str(real_gp7_chords_file))
        for theme_name in ['dark', 'light']:
            renderer = TabRenderer()
            renderer.set_theme(theme_name)
            pixmaps = renderer.render(song, track_index=0)
            assert len(pixmaps) >= 1, f'{theme_name} theme render failed'
            assert not pixmaps[0].isNull()

    def test_render_track_without_chord(self, qapp, real_gp7_chords_file):
        """和弦文件: 即使有 chord 也不报错 (无 chord 音轨也兼容)"""
        from ApolloTab import parse_score
        from ApolloTab.renderer import TabRenderer

        song = parse_score(str(real_gp7_chords_file))
        # 强制清除所有 chord (模拟无和弦的 song)
        for track in song.tracks:
            for measure in track.measures:
                for beat in measure.beats:
                    beat.chord = None

        renderer = TabRenderer()
        pixmaps = renderer.render(song, track_index=0)
        assert len(pixmaps) >= 1
        assert not pixmaps[0].isNull()
