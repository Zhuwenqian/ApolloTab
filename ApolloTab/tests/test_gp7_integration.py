"""
ApolloTab/tests/test_gp7_integration.py

GP7/GP8 真实文件端到端解析测试 (ApolloTab v1.4.1)

覆盖范围:
  - parse_score 对 guitarpro7/ 下多个 .gp 样本都能解析为 GTPSong
  - chords.gp: track/measure/beat 结构完整 + 至少 1 个 chord
  - GP7Parser.parse_bytes: 字节流直接解析
  - 版本号读取 (gp_version)
  - 错误处理: 坏 ZIP / 缺 score.gpif

样本目录: <项目根>/guitarpro7/ (真实 Guitar Pro 7 测试文件集)
运行命令: python -m pytest ApolloTab/tests/test_gp7_integration.py -q
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from ApolloTab.models.song import GTPSong
from ApolloTab.parser import GP7Parser, parse_score

# ============================================================
# 辅助: 收集 guitarpro7 下所有 .gp 样本
# ============================================================


def _all_gp_samples(guitarpro7_dir: Path) -> list[Path]:
    """列出 guitarpro7/ 下所有 .gp 文件 (排除子目录)"""
    if not guitarpro7_dir.exists():
        return []
    return sorted(p for p in guitarpro7_dir.glob("*.gp") if p.is_file())


# ============================================================
# 批量解析: 所有 .gp 样本不崩溃
# ============================================================


class TestAllGpSamplesParse:
    """guitarpro7/ 下每个 .gp 样本都能成功解析为 GTPSong"""

    def test_all_samples_parse_without_error(self, guitarpro7_dir: Path):
        """遍历所有 .gp 文件，逐一解析，断言返回 GTPSong"""
        samples = _all_gp_samples(guitarpro7_dir)
        if not samples:
            pytest.skip("guitarpro7 目录下无 .gp 样本")
        assert len(samples) >= 10, f"样本过少: {len(samples)}"

        for sample in samples:
            song = parse_score(str(sample))
            assert isinstance(song, GTPSong), f"{sample.name} 未返回 GTPSong"
            assert len(song.tracks) >= 1, f"{sample.name} 无音轨"

    def test_all_samples_have_gp_version(self, guitarpro7_dir: Path):
        """所有 GP7 样本应带版本号 (7.0/8.0)"""
        samples = _all_gp_samples(guitarpro7_dir)
        if not samples:
            pytest.skip("无 .gp 样本")
        for sample in samples:
            song = parse_score(str(sample))
            assert song.gp_version, f"{sample.name} 缺少 gp_version"


# ============================================================
# chords.gp 结构验证
# ============================================================


class TestChordsGpStructure:
    """chords.gp 端到端结构验证"""

    def test_chords_file_has_tracks_and_measures(self, real_gp7_chords_file: Path):
        song = parse_score(str(real_gp7_chords_file))
        assert song.track_count >= 1
        assert song.total_measures >= 1

    def test_chords_file_has_at_least_one_chord(self, real_gp7_chords_file: Path):
        """chords.gp 至少有一个 beat 带 chord"""
        song = parse_score(str(real_gp7_chords_file))
        chord_count = 0
        for track in song.tracks:
            for measure in track.measures:
                for beat in measure.beats:
                    if beat.chord is not None:
                        chord_count += 1
        assert chord_count >= 1, "chords.gp 应至少包含 1 个和弦"

    def test_chords_have_nonempty_names(self, real_gp7_chords_file: Path):
        """所有 chord.name 非空"""
        song = parse_score(str(real_gp7_chords_file))
        names = [
            beat.chord.name
            for track in song.tracks
            for measure in track.measures
            for beat in measure.beats
            if beat.chord is not None
        ]
        assert names, "未找到任何 chord"
        for name in names:
            assert name != "", "存在空 chord 名"


# ============================================================
# GP7Parser.parse_bytes
# ============================================================


class TestGp7ParseBytes:
    """字节流直接解析"""

    def test_parse_bytes_same_as_parse_file(self, real_gp7_chords_file: Path):
        """parse_bytes(文件字节) 与 parse_file 结果一致 (track_count)"""
        data = real_gp7_chords_file.read_bytes()
        song = GP7Parser().parse_bytes(data)
        assert isinstance(song, GTPSong)
        assert len(song.tracks) >= 1

    def test_parse_bytes_sets_gp_version(self, real_gp7_chords_file: Path):
        data = real_gp7_chords_file.read_bytes()
        parser = GP7Parser()
        song = parser.parse_bytes(data)
        assert parser.gp_version
        assert song.gp_version == parser.gp_version


# ============================================================
# 错误处理
# ============================================================


class TestGp7ErrorHandling:
    """坏数据与缺失条目"""

    def test_invalid_zip_raises_bad_zipfile(self):
        """非 ZIP 数据 → zipfile.BadZipFile"""
        with pytest.raises(zipfile.BadZipFile):
            GP7Parser().parse_bytes(b"not a zip file at all")

    def test_zip_without_gpif_raises_key_error(self, tmp_path: Path):
        """ZIP 包缺 Content/score.gpif → KeyError"""
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("VERSION", "7.0")
            zf.writestr("Other/file.txt", "no score.gpif here")
        with pytest.raises(KeyError, match="score.gpif"):
            GP7Parser().parse_bytes(buf.getvalue())

    def test_zip_missing_version_defaults_to_7(self, tmp_path: Path):
        """缺 VERSION 条目 → 默认版本 7.0，不报错"""
        import io

        # 构造一个最小合法 GPIF (让 GpifParser 不崩)
        minimal_gpif = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<GPIF><MasterTrack><Tracks>0</Tracks></MasterTrack>'
            '<Tracks><Track id="0" /></Tracks>'
            '<MasterBars></MasterBars>'
            '<Bars></Bars><Voices></Voices><Beats></Beats>'
            '</GPIF>'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Content/score.gpif", minimal_gpif)
            # 故意不写 VERSION
        parser = GP7Parser()
        song = parser.parse_bytes(buf.getvalue())
        assert parser.gp_version == "7.0"  # 默认值
        assert isinstance(song, GTPSong)
