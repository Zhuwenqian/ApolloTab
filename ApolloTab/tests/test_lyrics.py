"""
ApolloTab/tests/test_lyrics.py

歌词(Lyrics)功能测试 (ApolloTab v1.5.x) - 对应 "一feature一测试"

覆盖范围:
  1. Lyrics.finish() chunk 解析状态机（移植自 alphaTab，多组边界用例）
  2. GTPTrack.apply_lyrics() 按拍分配（跳休止符/多行/start_bar/跨小节）
  3. GP3-5 歌词映射 (mock pyguitarpro song.lyrics + trackChoice)
  4. 布局引擎为歌词带预留垂直空间（纯 Python，无 Qt）
  5. 渲染器 _draw_lyrics 单元测试（offscreen Qt，仅画小 pixmap 不崩）
  6. 真实 GP7 样本端到端 (beat-lyrics.gp / lyrics-template.gp / lyrics-null.gp)

运行: python -m pytest ApolloTab/tests/test_lyrics.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# offscreen Qt 由 conftest.py 统一设置
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from ApolloTab.models.beat import GTPBeat
from ApolloTab.models.lyrics import Lyrics
from ApolloTab.models.measure import GTPMeasure
from ApolloTab.models.note import GTPNote
from ApolloTab.models.track import GTPTrack
from ApolloTab.parser.gpif_parser import GpifParser
from ApolloTab.parser.gtp_parser import GTPParser
from ApolloTab.renderer.layout_engine import (
    BeatLayout,
    MeasureLayout,
    SystemLayout,
    TabLayoutEngine,
)
from ApolloTab.utils.constants import NoteDuration, RenderConfig

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="session")
def qapp():
    if not QApplication.instance():
        QApplication(sys.argv)
    yield QApplication.instance()


def _find_guitarpro7_dir() -> Path | None:
    """定位 guitarpro7/ 样本目录: 从测试文件位置向上查找，再回退 CWD。

    conftest.py 的 GUITARPRO7_DIR 在已安装包场景下可能指向 site-packages，
    因此这里额外向上搜索项目根的 guitarpro7/。
    """
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidates.append(parent / "guitarpro7")
    candidates.append(Path.cwd() / "guitarpro7")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _note(string: int = 0, fret: int = 0) -> GTPNote:
    """构造一个有音符的 GTPNote（供 apply_lyrics 测试用）"""
    return GTPNote(string=string, fret=fret)


def _beat_with_note() -> GTPBeat:
    b = GTPBeat(duration=NoteDuration.QUARTER)
    b.notes.append(_note())
    return b


def _rest_beat() -> GTPBeat:
    b = GTPBeat(duration=NoteDuration.QUARTER)
    b.is_rest = True
    return b


# ============================================================
# 1. Lyrics.finish() chunk 解析
# ============================================================


class TestLyricsChunkParsing:
    """移植自 alphaTab Lyrics.ts 的解析语义，用例对齐 alphaTab Lyrics.test.ts"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # spaces (对齐 alphaTab: 多空格产生空 chunk)
            ("AAA BBB CCC DDD EEE", ["AAA", "BBB", "CCC", "DDD", "EEE"]),
            ("AAA  BBB   CCC", ["AAA", "", "BBB", "", "", "CCC"]),
            # new-lines
            ("AAA\r\nBBB\rCCC\nDDD\r\nEEE", ["AAA", "BBB", "CCC", "DDD", "EEE"]),
            # dash (对齐 alphaTab: 连字符保留在音节末尾)
            ("AAA-BBB CCC- DDD EEE--FFF", ["AAA-", "BBB", "CCC-", "DDD", "EEE--", "FFF"]),
            # plus (+ 合并音节 → 空格，不裁剪)
            ("AAA+BBB CCC++DDD EEE+ FFF", ["AAA BBB", "CCC  DDD", "EEE ", "FFF"]),
            # comments ([..] 仅在 chunk 起始处作注释)
            ("[ABCD]AAA BBB", ["AAA", "BBB"]),
            ("[ABCD] AAA BBB", ["", "AAA", "BBB"]),
            # 末尾 '_' 裁剪
            ("You____", ["You"]),
            ("You____ world", ["You", "world"]),
            # 文中 '[' 为字面量（非 chunk 起始）
            ("a[cm]b", ["a[cm]b"]),
            # 边界
            ("", []),
            ("word", ["word"]),
        ],
    )
    def test_chunk_parsing(self, text, expected):
        ly = Lyrics(text=text)
        ly.finish()
        assert ly.chunks == expected

    def test_skip_empty_filters_dash_and_empty(self):
        """skip_empty=True 时跳过空片段和单独的 '-'（用于拍自由文本当歌词）"""
        ly = Lyrics(text="a - b")
        ly.finish(skip_empty=True)
        # 'a' 和 'b' 之间是 ' - '：解析为 ['a', '-', 'b']，skip_empty 跳过 '-'
        assert ly.chunks == ["a", "b"]

    def test_finish_is_idempotent(self):
        ly = Lyrics(text="Hello world")
        ly.finish()
        first = list(ly.chunks)
        ly.finish()
        assert ly.chunks == first


# ============================================================
# 2. GTPTrack.apply_lyrics()
# ============================================================


class TestApplyLyrics:
    """验证歌词 chunk 按拍分配（跳休止符/多行/start_bar/跨小节）"""

    def test_single_line_assigns_chunks_to_beats_in_order(self):
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_beat_with_note(), _beat_with_note(), _beat_with_note()]
        track.measures = [m]

        ly = Lyrics(start_bar=0, text="Hel-lo world")
        track.apply_lyrics([ly])

        chunks = [b.lyrics for b in m.beats]
        # dash 保留在音节末尾（与 alphaTab 一致）
        assert chunks[0] == ["Hel-"]
        assert chunks[1] == ["lo"]
        assert chunks[2] == ["world"]

    def test_skips_rest_beats(self):
        """休止符拍不承载歌词，chunk 顺延到下一个有音符的拍"""
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_rest_beat(), _beat_with_note(), _beat_with_note()]
        track.measures = [m]

        ly = Lyrics(start_bar=0, text="a b")
        track.apply_lyrics([ly])

        # 休止符拍无歌词
        assert m.beats[0].lyrics is None
        assert m.beats[1].lyrics == ["a"]
        assert m.beats[2].lyrics == ["b"]

    def test_multi_line_each_line_independent(self):
        """多行歌词：每行独立分配，beat.lyrics 长度 = 行数"""
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_beat_with_note(), _beat_with_note()]
        track.measures = [m]

        line1 = Lyrics(start_bar=0, text="foo bar")
        line2 = Lyrics(start_bar=0, text="x y")
        track.apply_lyrics([line1, line2])

        assert m.beats[0].lyrics == ["foo", "x"]
        assert m.beats[1].lyrics == ["bar", "y"]
        # 每拍 lyrics 长度 = 行数
        assert len(m.beats[0].lyrics) == 2

    def test_start_bar_offset(self):
        """start_bar 指定从第几个小节开始分配"""
        track = GTPTrack()
        m0 = GTPMeasure(number=1)
        m0.beats = [_beat_with_note()]
        m1 = GTPMeasure(number=2)
        m1.beats = [_beat_with_note()]
        track.measures = [m0, m1]

        ly = Lyrics(start_bar=1, text="hi")  # 从第 2 小节(start_bar=1)开始
        track.apply_lyrics([ly])

        assert m0.beats[0].lyrics is None  # 第 1 小节不受影响
        assert m1.beats[0].lyrics == ["hi"]

    def test_more_chunks_than_beats_truncates_safely(self):
        """chunk 数多于有音符的拍时安全停止，不抛异常"""
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_beat_with_note()]
        track.measures = [m]

        ly = Lyrics(start_bar=0, text="a b c d e")
        track.apply_lyrics([ly])  # 不应抛异常
        assert m.beats[0].lyrics == ["a"]

    def test_empty_lyrics_list_is_noop(self):
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_beat_with_note()]
        track.measures = [m]
        track.apply_lyrics([])
        assert m.beats[0].lyrics is None


# ============================================================
# 3. GP3-5 歌词映射 (mock pyguitarpro)
# ============================================================


class TestGtpLyricsMapping:
    """验证 GTPParser._apply_song_lyrics 把 song 级歌词映射到 trackChoice 轨道"""

    def test_lyrics_assigned_to_trackchoice_track(self):
        parser = GTPParser()

        # 构造两轨道 GTPSong
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_beat_with_note(), _beat_with_note()]
        track.measures = [m]

        track2 = GTPTrack()
        m2 = GTPMeasure(number=1)
        m2.beats = [_beat_with_note(), _beat_with_note()]
        track2.measures = [m2]

        # mock pyguitarpro Song.lyrics: trackChoice=2 → 第 2 轨道(0-based idx 1)
        raw_song = SimpleNamespace(
            lyrics=SimpleNamespace(
                trackChoice=2,
                lines=[
                    SimpleNamespace(startingMeasure=1, lyrics="Hel-lo"),
                    SimpleNamespace(startingMeasure=1, lyrics=""),  # 空行跳过
                ],
            )
        )

        # 直接调用映射方法（避免依赖真实文件解析）
        from ApolloTab.models.song import GTPSong

        song = GTPSong()
        song.tracks = [track, track2]
        parser._apply_song_lyrics(song, raw_song)

        # 第 1 轨道无歌词
        assert track.measures[0].beats[0].lyrics is None
        # 第 2 轨道分到歌词（1 行，因第 2 行空被过滤）；dash 保留在音节末尾
        assert track2.measures[0].beats[0].lyrics == ["Hel-"]
        assert track2.measures[0].beats[1].lyrics == ["lo"]

    def test_invalid_trackchoice_does_not_crash(self):
        """trackChoice 超出范围时安全跳过，不抛异常"""
        parser = GTPParser()
        track = GTPTrack()
        m = GTPMeasure(number=1)
        m.beats = [_beat_with_note()]
        track.measures = [m]

        raw_song = SimpleNamespace(
            lyrics=SimpleNamespace(
                trackChoice=99,  # 越界
                lines=[SimpleNamespace(startingMeasure=1, lyrics="hi")],
            )
        )
        from ApolloTab.models.song import GTPSong

        song = GTPSong()
        song.tracks = [track]
        parser._apply_song_lyrics(song, raw_song)  # 不抛异常
        assert track.measures[0].beats[0].lyrics is None


# ============================================================
# 4. 布局引擎预留歌词带空间
# ============================================================


class TestLayoutReservesLyricsSpace:
    """验证 TabLayoutEngine 为含歌词的系统预留垂直空间"""

    def _system_with_lyrics(self, line_count: int) -> SystemLayout:
        sys = SystemLayout()
        sys.y_tab_top = 100
        sys.y_tab_bottom = 170
        sys.y_lyrics_top = 200
        sys.lyrics_line_count = line_count
        m = GTPMeasure(number=1)
        beat = _beat_with_note()
        beat.lyrics = ["x"] * line_count
        bl = BeatLayout(beat=beat, x_center=50)
        ml = MeasureLayout(measure=m)
        ml.beats = [bl]
        sys.measures = [ml]
        return sys

    def test_system_with_lyrics_has_expanded_bottom(self):
        cfg = RenderConfig()
        engine = TabLayoutEngine(cfg)
        # 直接验证 _assign_system_coordinates 对含歌词行的处理
        beat = _beat_with_note()
        beat.lyrics = ["foo", "bar"]  # 2 行歌词
        measure = GTPMeasure(number=1)
        measure.beats = [beat]

        systems = engine._assign_system_coordinates(
            rows=[[measure]], start_x=40, start_y=100, usable_width=900, string_count=6
        )
        assert len(systems) == 1
        sys = systems[0]
        assert sys.lyrics_line_count == 2
        assert sys.y_lyrics_top > sys.y_tab_bottom
        # y_bottom 应包含 2 行歌词带高度
        expected_bottom = sys.y_lyrics_top + 2 * cfg.LYRICS_LINE_PITCH
        assert sys.y_bottom == expected_bottom

    def test_system_without_lyrics_has_no_lyrics_band(self):
        cfg = RenderConfig()
        engine = TabLayoutEngine(cfg)
        beat = _beat_with_note()  # 无 lyrics
        measure = GTPMeasure(number=1)
        measure.beats = [beat]

        systems = engine._assign_system_coordinates(
            rows=[[measure]], start_x=40, start_y=100, usable_width=900, string_count=6
        )
        sys = systems[0]
        assert sys.lyrics_line_count == 0
        assert sys.y_lyrics_top == 0
        # y_bottom 仅含符干区，不含歌词带
        assert sys.y_bottom == sys.y_tab_bottom + cfg.STEM_HEIGHT + 8


# ============================================================
# 5. 渲染器 _draw_lyrics 单元测试 (offscreen Qt)
# ============================================================


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ApolloTab QPainter/QPixmap 在 Windows/macOS Qt offscreen 模式下崩溃, 仅 Linux 可跑 (与歌词逻辑无关)",
)
class TestRendererDrawLyrics:
    """验证 TabRenderer._draw_lyrics 不崩溃且无歌词时为 no-op (Linux only)"""

    def test_draw_lyrics_noop_without_lyrics(self, qapp):
        from ApolloTab.renderer.tab_renderer import TabRenderer

        renderer = TabRenderer()
        pixmap = QPixmap(200, 200)
        pixmap.fill()  # 白底
        painter = QPainter(pixmap)
        try:
            sys_layout = SystemLayout()
            sys_layout.lyrics_line_count = 0
            sys_layout.y_lyrics_top = 0
            # 无歌词 → no-op，不抛异常
            renderer._draw_lyrics(painter, sys_layout)
        finally:
            painter.end()

    def test_draw_lyrics_renders_without_crash(self, qapp):
        """有歌词时 _draw_lyrics 在小 pixmap 上正常绘制"""
        from ApolloTab.renderer.tab_renderer import TabRenderer

        renderer = TabRenderer()
        beat = _beat_with_note()
        beat.lyrics = ["Hel", "lo"]
        bl = BeatLayout(beat=beat, x_center=100)
        ml = MeasureLayout(measure=GTPMeasure(number=1))
        ml.beats = [bl]

        sys_layout = SystemLayout()
        sys_layout.y_tab_top = 20
        sys_layout.y_tab_bottom = 90
        sys_layout.y_lyrics_top = 120
        sys_layout.lyrics_line_count = 2
        sys_layout.measures = [ml]

        pixmap = QPixmap(200, 200)
        pixmap.fill()
        painter = QPainter(pixmap)
        try:
            renderer._draw_lyrics(painter, sys_layout)  # 不抛异常即通过
        finally:
            painter.end()
        assert not pixmap.isNull()


# ============================================================
# 6. 真实 GP7 样本端到端
# ============================================================


class TestRealGp7LyricsFiles:
    """真实 GP7 歌词样本端到端解析（样本缺失则 skip）"""

    @pytest.fixture
    def lyrics_sample(self):
        gp7_dir = _find_guitarpro7_dir()
        if gp7_dir is None:
            pytest.skip("guitarpro7/ 样本目录未找到")
        # 优先 beat-lyrics.gp（beat 级歌词），其次 lyrics-template.gp
        for name in ("beat-lyrics.gp", "lyrics-template.gp"):
            p = gp7_dir / name
            if p.exists():
                return p
        pytest.skip("未找到 beat-lyrics.gp / lyrics-template.gp 样本")

    def test_lyrics_sample_has_lyrics_on_some_beat(self, lyrics_sample):
        """解析后至少有一个非空 beat.lyrics（beat 级或 track 级）"""
        from ApolloTab.parser import parse_score

        song = parse_score(str(lyrics_sample))
        found = False
        for track in song.tracks:
            for measure in track.measures:
                for beat in measure.beats:
                    if beat.lyrics and any(s for s in beat.lyrics):
                        found = True
                        break
        assert found, f"{lyrics_sample.name} 解析后未找到任何歌词"

    def test_lyrics_null_sample_parses_without_lyrics(self):
        """lyrics-null.gp 应能正常解析（无歌词或空歌词不崩溃）"""
        gp7_dir = _find_guitarpro7_dir()
        if gp7_dir is None:
            pytest.skip("guitarpro7/ 样本目录未找到")
        p = gp7_dir / "lyrics-null.gp"
        if not p.exists():
            pytest.skip("lyrics-null.gp 样本未找到")
        from ApolloTab.parser import parse_score

        song = parse_score(str(p))  # 不抛异常即通过
        assert len(song.tracks) >= 1

    def test_render_lyrics_sample_does_not_crash(self, qapp, lyrics_sample):
        """渲染含歌词的样本不崩溃（Linux 端到端；其他平台仅解析层验证已覆盖）"""
        if sys.platform.startswith("win") or sys.platform == "darwin":
            pytest.skip("ApolloTab render() 在 Windows/macOS offscreen 模式下崩溃, 仅 Linux 跑")
        from ApolloTab.parser import parse_score
        from ApolloTab.renderer.tab_renderer import TabRenderer

        song = parse_score(str(lyrics_sample))
        renderer = TabRenderer()
        pixmaps = renderer.render(song, track_index=0)
        assert len(pixmaps) >= 1
