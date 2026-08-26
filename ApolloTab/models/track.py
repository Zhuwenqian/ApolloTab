"""
============================================================
文件名: track.py
功能描述: 音轨(Track)数据模型 - 存储一条吉他/贝斯轨道的完整信息
         包含调弦、乐器设置、所有小节等内容

创建日期: 2026-06-06
最后更新: 2026-06-30 (v1.3.0: 新增 percussion_articulations 字段,
                   用于 GP7/GP8 鼓轨 InstrumentArticulation → MIDI note 映射)
依赖: Python 3.11+ dataclasses
============================================================
"""

from dataclasses import dataclass, field
from typing import Optional

from .measure import GTPMeasure


@dataclass
class PercussionArticulation:
    """
    打击乐 articulation 定义 (GP7/GP8)

    在 GPIF XML 中，每个 <Articulation> 节点定义了一种打击乐演奏法，
    其中 <OutputMidiNumber> 是该演奏法对应的 GM 标准 MIDI 鼓音编号。

    属性:
      name:              显示名称(如 "Snare (hit)")
      element_type:      元素类型(如 "Snare")
      output_midi_number: GM 标准 MIDI 鼓音(如 38=军鼓)
      staff_line:        在五线谱/TAB 谱上的行号(仅用于渲染)
      unique_id:         内部唯一标识(可选)
    """

    name: str = ""
    element_type: str = ""
    output_midi_number: int = -1
    staff_line: int = 0
    unique_id: str = ""


@dataclass
class GTPTrack:
    """
    一条音轨(Track)的数据模型 - 对应 Guitar Pro 中的一条吉他/贝斯轨道

    属性说明:
      name:          轨道名称 (如 "Lead Guitar", "Bass")
      number:        轨道编号 (从1开始)
      strings:       调弦信息 (MIDI音高元组, 从1弦到6弦)
                    例: (64,59,55,50,45,40) = 标准调弦 EADGBE
      fret_count:    品格数量 (通常21-24)
      instrument:    MIDI乐器编号 (24=尼龙弦吉他, 25=钢弦吉他,
                    26=爵士吉他, 27=清洁吉他, 28=失真吉他,
                    29=过载吉他, 30=电吉他)
      measures:      该轨道的所有小节列表
      is_visible:    是否在乐谱中可见
      is_solo:       是否独奏
      is_mute:       是否静音
      capo:          变调夹位置 (0=无变调夹)

    GP7/GP8 扩展字段 (v0.4.0 新增; v1.1.2 新增 percussion_articulations):
      is_percussion:        是否打击乐轨道（GP7 drumKit）
      show_standard_notation: 是否显示五线谱（来自 PartConfiguration，预留渲染接口）
      show_tablature:        是否显示六线谱（默认 True）
      show_slash:            是否显示斜线谱（预留渲染接口）
      show_numbered:         是否显示简谱（GP8 新功能，预留渲染接口）
      midi_bank_msb:         MIDI Bank MSB（音色库选择高7位）
      midi_bank_lsb:         MIDI Bank LSB（音色库选择低7位）
      color:                 音轨颜色（RGBA 元组，GP7 音轨颜色标识）
      short_name:            短名称（用于多轨混排时的简短显示）
      percussion_articulations: GP7/GP8 鼓轨 articulation 列表，
                                每个条目含 output_midi_number，
                                用于把 note.percussion_articulation 索引映射到真实 GM 鼓音
    """

    name: str = ""  # 轨道名称
    number: int = 1  # 轨道编号
    strings: tuple[int, ...] = (64, 59, 55, 50, 45, 40)  # 调弦(MIDI音高)
    fret_count: int = 24  # 品格数
    instrument: int = 30  # MIDI乐器编号
    measures: list[GTPMeasure] = field(default_factory=list)  # 小节列表
    is_visible: bool = True  # 可见性
    is_solo: bool = False  # 独奏
    is_mute: bool = False  # 静音
    capo: int = 0  # 变调夹位置

    # === GP7/GP8 扩展字段 (v0.4.0) ===
    is_percussion: bool = False  # 打击乐轨道
    show_standard_notation: bool = False  # 是否显示五线谱（预留）
    show_tablature: bool = True  # 是否显示六线谱（默认）
    show_slash: bool = False  # 是否显示斜线谱（预留）
    show_numbered: bool = False  # 是否显示简谱（GP8预留）
    midi_bank_msb: int = 0  # MIDI Bank MSB
    midi_bank_lsb: int = 0  # MIDI Bank LSB
    color: tuple[int, int, int, int] | None = None  # RGBA 颜色
    short_name: str = ""  # 短名称
    percussion_articulations: list[PercussionArticulation] = field(
        default_factory=list
    )  # 鼓轨 articulation 映射表

    @property
    def string_count(self) -> int:
        """弦的数量"""
        return len(self.strings)

    @property
    def total_measures(self) -> int:
        """总小节数"""
        return len(self.measures)

    def get_tuning_name(self) -> str:
        """
        获取调弦方案的名称（英文硬编码）

        原理:
          遍历预设调弦方案元组与当前调弦匹配，返回英文名称。
          英语受众更广，无需国际化翻译。

        返回: 已知调弦返回英文标准名称，否则返回自定义描述

        匹配方式: 遍历所有预设调弦方案，逐一比较元组值。
                 比字典键方式更健壮，可处理元组子类/类型差异等边界情况。
        """
        from ..utils.constants import StandardTunings

        tuning_tuple = self.strings

        # 调弦名称映射表: (英文显示名, 预设值元组)
        tuning_map = [
            ("Standard", StandardTunings.STANDARD),
            ("Drop D", StandardTunings.DROP_D),
            ("Open G", StandardTunings.OPEN_G),
            ("Open D", StandardTunings.OPEN_D),
            ("DADGAD", StandardTunings.DADGAD),
            ("Half Step Down", StandardTunings.HALF_STEP_DOWN),
        ]

        for name, stuning in tuning_map:
            if tuning_tuple == stuning:
                return name

        return f"Custom ({len(self.strings)} strings)"

    def get_total_beats(self) -> int:
        """获取该轨道所有小节的总拍数"""
        return sum(len(m.beats) for m in self.measures)

    def apply_lyrics(self, lyrics_list: list["Lyrics"]) -> None:
        """
        将多条歌词行分配到各拍 - 移植自 alphaTab Track.applyLyrics

        对每条歌词行:
          1. 调用 lyric.finish() 把原始文本解析为 chunks
          2. 从 start_bar 小节的第一拍开始，按顺序把每个 chunk
             分配到一个"有音符"的拍（跳过休止符和空拍）
          3. beat.lyrics 是 list[str]，长度 = 歌词行数；
             第 li 行的 chunk 写入 beat.lyrics[li]，缺失处为 ''

        参数:
          lyrics_list: 歌词行列表（每条对应一个 <Line>）
        """
        for lyric in lyrics_list:
            lyric.finish()

        if not self.measures or not lyrics_list:
            return

        # 拍扁平序列：跨小节顺序的所有拍（对应 alphaTab 的 beat.nextBeat 链）
        all_beats = [beat for measure in self.measures for beat in measure.beats]
        if not all_beats:
            return

        # 每个起始小节在扁平序列中的起始索引
        bar_start_indices: list[int] = []
        idx = 0
        for measure in self.measures:
            bar_start_indices.append(idx)
            idx += len(measure.beats)

        num_lines = len(lyrics_list)
        for li, lyric in enumerate(lyrics_list):
            if lyric.start_bar < 0 or lyric.start_bar >= len(self.measures):
                continue
            bi = bar_start_indices[lyric.start_bar]
            for chunk in lyric.chunks:
                # 跳过休止符和空拍（无音符的拍不承载歌词）
                while bi < len(all_beats) and not all_beats[bi].notes:
                    bi += 1
                if bi >= len(all_beats):
                    break
                beat = all_beats[bi]
                if beat.lyrics is None:
                    beat.lyrics = [""] * num_lines
                # 保证列表长度足够容纳第 li 行
                while len(beat.lyrics) <= li:
                    beat.lyrics.append("")
                beat.lyrics[li] = chunk
                bi += 1
