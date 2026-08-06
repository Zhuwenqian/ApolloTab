"""
============================================================
文件名: gpif_parser.py
功能描述: GP7/GP8 GPIF XML 解析器（两遍解析核心）
         解析 .gp 文件 ZIP 包中的 Content/score.gpif XML 文件，
         将其转换为 ApolloTab 的 GTPSong 数据模型。

原理:
  GPIF XML 采用 ID 引用方式组织数据结构：
    <MasterBar><Bars>0 1</Bars></MasterBar>  ← 引用 Bar id="0" 和 "1"
    <Bar><Voices>0 -1 -1 -1</Voices></Bar>   ← 引用 Voice id="0"
    <Voice><Beats>0 1</Beats></Voice>        ← 引用 Beat id="0" 和 "1"
    <Beat><Notes>0</Notes><Rhythm ref="0"/></Beat>  ← 引用 Note 和 Rhythm

  因此采用两遍解析策略（参照 alphaTab GpifParser.ts）:
    第一遍 _parse_dom:   遍历 XML 收集所有元素到 Map（按 ID 索引）
    第二遍 _build_model: 按 ID 引用关系组装 GTPSong 模型

  GP6 复用设计:
    Beat/Note 对象在 GPX 文件中可能被多个 Voice 引用（共享），
    组装时必须使用 copy.deepcopy 克隆，避免修改共享对象引发副作用。

  数值转换因子:
    推弦位置: GPX 0-100 → 内部 0-60 (因子 0.6)
    推弦幅度: GPX 25/四分音符 → 内部 1/四分音符 (因子 1/25=0.04)

调用来源: alphaTab-develop/packages/alphatab/src/importer/GpifParser.ts
调用入口: 由 gp7_parser.py 调用，传入 score.gpif 的 XML 字符串

创建日期: 2026-06-28 (v0.4.0: GP7/GP8 支持)
最后更新: 2026-06-30 (v1.3.0: 解析 <Articulations> 节点;
                   修复 GP7/GP8 GPIF String 弦号映射方向)
依赖: Python 3.11+ 标准库 xml.etree.ElementTree + copy
============================================================
"""

import contextlib
import copy
import xml.etree.ElementTree as ET
from typing import Any, Optional

from ..models.beat import GTPBeat
from ..models.chord import Chord
from ..models.lyrics import Lyrics
from ..models.measure import GTPMeasure
from ..models.note import BendData, GTPNote

# 导入数据模型
from ..models.song import GTPSong
from ..models.track import GTPTrack, PercussionArticulation
from ..utils.constants import BendStyle, BendType, NoteDuration, TechniqueType, VibratoType

# ============================================================
# 常量定义
# ============================================================

# GPIF 中无效 ID（表示该位置无元素，例如多 Voice 中未使用的 Voice）
_INVALID_ID = '-1'

# 推弦位置转换因子: GPX 范围 0-100 → 内部范围 0-60
# 调整效果: GPX 中的位置 100 对应内部最大位置 60
_BEND_POINT_POSITION_FACTOR = 60.0 / 100.0

# 推弦值转换因子: GPX 单位 25/四分音符 → 内部 1/四分音符
# 调整效果: GPX 中 25 = 1/4 音 → 内部 1；GPX 中 100 = Full → 内部 4
_BEND_POINT_VALUE_FACTOR = 1.0 / 25.0

# GPIF 升降号字符串 → 实际符号
_CHORD_ACCIDENTAL_MAP: dict[str, str] = {
    'Natural': '',
    'Sharp': '#',
    'Flat': 'b',
    'DoubleSharp': '##',
    'DoubleFlat': 'bb',
}

# ============================================================
# GPIF 内部数据结构（仅用于解析阶段暂存）
# ============================================================


class _GpifRhythm:
    """GPIF Rhythm 元素的临时存储结构（解析阶段使用）"""

    def __init__(self) -> None:
        self.rhythm_id: str = ''
        self.dots: int = 0  # 附点数量(0/1/2)
        self.tuplet_numerator: int = -1  # 连音分子(-1=无)
        self.tuplet_denominator: int = -1  # 连音分母(-1=无)
        self.duration: NoteDuration = NoteDuration.QUARTER  # 时值


class _GpifSound:
    """GPIF Sound 元素的临时存储结构（用于 MIDI 编号提取）"""

    def __init__(self) -> None:
        self.program: int = 0  # MIDI Program Change
        self.bank: int = 0  # MIDI Bank


# ============================================================
# 和弦解析辅助函数 (v1.4.0 新增)
# ============================================================


def _chord_note_name(note_el: ET.Element | None) -> str:
    """
    把 <KeyNote> 或 <BassNote> 节点转为可读名 (e.g. 'C', 'F#', 'Bb')

    参数:
        note_el: <KeyNote> 或 <BassNote> XML 元素, None 返回空字符串
    """
    if note_el is None:
        return ''
    step = note_el.get('step', 'C')
    acc = note_el.get('accidental', 'Natural')
    return step + _CHORD_ACCIDENTAL_MAP.get(acc, '')


def _build_chord_from_xml(chord_el: ET.Element) -> Chord | None:
    """
    把一个 GPIF <Chord> 定义节点转换为 Chord 数据对象

    后缀优先级:
      13 抑制 7/9;  6 抑制 7;  m7b5 不重复 7
      sus4 不带 7/9/11/13 扩展

    参数:
        chord_el: 包含 <KeyNote>/<BassNote>/<Degree> 子节点的 <Chord> 元素

    返回:
        Chord 对象, 若缺 KeyNote 返回 None
    """
    key_el = chord_el.find('KeyNote')
    if key_el is None:
        return None
    key_name = _chord_note_name(key_el)
    if not key_name:
        return None
    bass_name = _chord_note_name(chord_el.find('BassNote'))

    # 收集 (interval, alteration) 映射
    degree_map: dict[str, str] = {}
    for d in chord_el.iter('Degree'):
        interval = d.get('interval', '')
        alt = d.get('alteration', '')
        if interval:
            degree_map[interval] = alt

    # 基础后缀
    third = degree_map.get('Third', '')
    fifth = degree_map.get('Fifth', '')
    fourth = degree_map.get('Fourth', '')

    suffix = ''
    if fourth == 'Perfect':
        suffix = 'sus4'
    elif third == 'Minor':
        suffix = 'm'
    # else: 大三和弦无后缀

    # 第五音变化
    if fifth == 'Diminished':
        suffix = 'm7b5' if 'Seventh' in degree_map and degree_map['Seventh'] == 'Minor' else 'dim'
    elif fifth == 'Augmented':
        suffix = 'aug'

    # 扩展后缀
    if suffix == 'sus4':
        return Chord(key=key_name, bass=bass_name, suffix='sus4', extensions='')

    ext_parts: list[str] = []
    # 13th 优先
    if 'Thirteenth' in degree_map:
        ext_parts.append('13' if degree_map['Thirteenth'] == 'Major' else 'b13')
    # 11th
    if 'Eleventh' in degree_map:
        ext_parts.append('11' if degree_map['Eleventh'] == 'Perfect' else '#11')
    # 6th 抑制 7th
    if 'Sixth' in degree_map and degree_map['Sixth'] == 'Major' and '6' not in ext_parts:
        ext_parts.append('6')
    # 7th (被 6/13 抑制, 或 m7b5 已有 7 后缀)
    if (
        'Seventh' in degree_map
        and 'Thirteenth' not in degree_map
        and 'Sixth' not in degree_map
        and '7' not in suffix
    ):
        seventh = degree_map['Seventh']
        if seventh == 'Major':
            ext_parts.append('maj7')
        elif seventh == 'Minor':
            ext_parts.append('7')
    # 9th (被 13 抑制)
    if 'Ninth' in degree_map and 'Thirteenth' not in degree_map:
        ninth = degree_map['Ninth']
        if ninth == 'Major':
            ext_parts.append('9')
        elif ninth == 'Minor':
            ext_parts.append('b9')

    return Chord(key=key_name, bass=bass_name, suffix=suffix, extensions=''.join(ext_parts))


# ============================================================
# 时值/力度/泛音等映射表
# ============================================================

# GPIF NoteValue 文本 → NoteDuration 枚举
_NOTE_VALUE_MAP = {
    'Long': NoteDuration.WHOLE,  # 四倍全音符(按全音符处理)
    'DoubleWhole': NoteDuration.WHOLE,  # 二倍全音符(按全音符处理)
    'Whole': NoteDuration.WHOLE,  # 全音符
    'Half': NoteDuration.HALF,  # 二分音符
    'Quarter': NoteDuration.QUARTER,  # 四分音符
    'Eighth': NoteDuration.EIGHTH,  # 八分音符
    '16th': NoteDuration.SIXTEENTH,  # 十六分音符
    '32nd': NoteDuration.THIRTY_SECOND,  # 三十二分音符
    '64th': NoteDuration.THIRTY_SECOND,  # 六十四分音符(降级处理)
    '128th': NoteDuration.THIRTY_SECOND,  # 一百二十八分音符(降级)
    '256th': NoteDuration.THIRTY_SECOND,  # 二百五十六分音符(降级)
}

# GPIF Dynamic 文本 → MIDI 力度值(0-127)
# 调整效果: PPP=极弱 15, FF=极强 127，符合 GM 标准力度梯度
_DYNAMIC_MAP = {
    'PPP': 15,  # pianississimo 极弱
    'PP': 31,  # pianissimo 弱
    'P': 47,  # piano 弱
    'MP': 63,  # mezzo-piano 中弱
    'MF': 79,  # mezzo-forte 中强
    'F': 95,  # forte 强
    'FF': 111,  # fortissimo 极强
    'FFF': 127,  # fortississimo 最强
}

# GPIF TripletFeel 文本 → 字符串标识
_TRIPLET_FEEL_MAP = {
    'NoTripletFeel': None,
    'Triplet8th': 'Triplet8th',
    'Triplet16th': 'Triplet16th',
    'Dotted8th': 'Dotted8th',
    'Dotted16th': 'Dotted16th',
    'Scottish8th': 'Scottish8th',
    'Scottish16th': 'Scottish16th',
}

# GPIF HarmonicType HType 文本 → 字符串标识
_HARMONIC_TYPE_MAP = {
    'noharmonic': None,
    'natural': 'Natural',
    'artificial': 'Artificial',
    'pinch': 'Pinch',
    'tap': 'Tap',
    'semi': 'Semi',
    'feedback': 'Feedback',
}

# GPIF Slide Flags 位标志 → 滑音类型
# bit0=Shift, bit1=Legato, bit2=OutDown, bit3=OutUp, bit4=IntoFromBelow, bit5=IntoFromAbove
# bit6=PickSlideDown, bit7=PickSlideUp
_SLIDE_FLAG_MAP_OUT = {
    1: 'Shift',
    2: 'Legato',
    4: 'OutDown',
    8: 'OutUp',
    64: 'PickSlideDown',
    128: 'PickSlideUp',
}
_SLIDE_FLAG_MAP_IN = {
    16: 'IntoFromBelow',
    32: 'IntoFromAbove',
}

# ============================================================
# 主解析器类
# ============================================================


class GpifParser:
    """
    GPIF XML 解析器 - 将 score.gpif XML 解析为 GTPSong

    用法:
        parser = GpifParser()
        song = parser.parse_xml(xml_string)

    解析流程:
      1. parse_xml() 入口方法
      2. _parse_dom() 第一遍: 收集所有元素到 Map
      3. _build_model() 第二遍: 按 ID 引用组装 GTPSong
    """

    def __init__(self) -> None:
        """初始化解析器，准备各 ID 映射表"""
        self._reset_state()

    def _reset_state(self) -> None:
        """重置所有 ID 映射表（供 __init__ 与 parse_xml 复用，避免重复调用 __init__）"""
        # 解析结果
        self._song: GTPSong | None = None

        # 第一遍收集的 ID → 元素映射表
        self._tracks_mapping: list[str] = []  # MasterTrack/Tracks 文本(轨道ID顺序)
        self._tracks_by_id: dict[str, GTPTrack] = {}  # Track id → GTPTrack
        self._master_bars: list[dict[str, Any]] = []  # MasterBar 属性列表
        self._bars_of_master_bar: list[list[str]] = []  # 每个 MasterBar 的 Bar ID 列表
        self._bars_by_id: dict[str, dict[str, Any]] = {}  # Bar id → 属性字典(Voices/Clef)
        self._voices_of_bar: dict[str, list[str]] = {}  # Bar id → Voice ID 列表
        self._voice_by_id: dict[str, dict[str, Any]] = {}  # Voice id → 属性字典
        self._beats_of_voice: dict[str, list[str]] = {}  # Voice id → Beat ID 列表
        self._beat_by_id: dict[str, GTPBeat] = {}  # Beat id → GTPBeat
        self._rhythm_of_beat: dict[str, str] = {}  # Beat id → Rhythm id
        self._rhythm_by_id: dict[str, _GpifRhythm] = {}  # Rhythm id → _GpifRhythm
        self._notes_of_beat: dict[str, list[str]] = {}  # Beat id → Note ID 列表
        self._note_by_id: dict[str, GTPNote] = {}  # Note id → GTPNote

        # 和弦表 (v1.4.0 新增)
        # _chord_definitions: 按 document order 索引的 Chord 列表 (index → Chord)
        # _chord_idx_of_beat: beat_id → 和弦在 _chord_definitions 中的索引 (-1=无)
        self._chord_definitions: list[Chord] = []
        self._chord_idx_of_beat: dict[str, int] = {}

        # 轨道附加信息
        self._track_sounds: dict[str, _GpifSound] = {}  # Track id → Sound 信息
        self._track_is_percussion: dict[str, bool] = {}  # Track id → 是否打击乐
        self._has_anacrusis: bool = False  # 是否弱起小节
        self._master_tempo: int = 120  # 主轨道 tempo 自动化
        self._master_tempo_name: str = ''  # tempo 文本(如 "Moderate")

        # 歌词 (移植 alphaTab GpifParser) - track 级歌词按轨道收集，beat 级歌词直接写 beat.lyrics
        self._lyrics_by_track: dict[str, list[Lyrics]] = {}  # Track id → 歌词行列表
        self._skip_apply_lyrics: bool = False  # beat 级歌词存在时跳过 track 级 apply

    # ============================================================
    # 公共入口
    # ============================================================

    def parse_xml(self, xml_string: str) -> GTPSong:
        """
        解析 GPIF XML 字符串，返回 GTPSong 对象

        参数:
            xml_string: score.gpif 文件的完整 XML 字符串

        返回:
            GTPSong 对象（包含所有轨道、小节、拍、音符信息）

        执行步骤:
          1. 初始化所有映射表
          2. 解析 XML 字符串为 ElementTree DOM
          3. 第一遍 _parse_dom: 收集所有元素到映射表
          4. 第二遍 _build_model: 按 ID 组装 GTPSong 模型
        """
        # 重置映射表（防止多次调用累积）
        self._reset_state()

        # 解析 XML
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as e:
            raise ValueError(f"GPIF XML 解析失败: {e}") from e

        # 验证根节点
        if root.tag != 'GPIF':
            raise ValueError(f"XML 根节点不是 GPIF: {root.tag}")

        # 创建空的 GTPSong
        self._song = GTPSong()
        self._song.gp_version = "7.0"  # 默认版本，后续会被 GPRevision 覆盖

        # === 第一遍: 解析 DOM 收集元素 ===
        self._parse_dom(root)

        # === 第二遍: 按 ID 组装模型 ===
        self._build_model()

        # === 歌词分配 (移植 alphaTab: track 级歌词在模型组装完成后 apply) ===
        # beat 级 <Lyrics> 存在时 _skip_apply_lyrics=True，跳过 track 级 apply
        if not self._skip_apply_lyrics and self._lyrics_by_track:
            for track_id, lyrics in self._lyrics_by_track.items():
                track = self._tracks_by_id.get(track_id)
                if track is not None and lyrics:
                    track.apply_lyrics(lyrics)

        return self._song

    # ============================================================
    # 第一遍: 解析 DOM 收集元素
    # ============================================================

    def _parse_dom(self, root: ET.Element) -> None:
        """
        遍历 GPIF 根节点的所有子元素，按类型分发到对应解析方法

        参数:
            root: GPIF XML 根元素
        """
        # parse_xml 调用本方法前已初始化 self._song，此处断言以辅助类型检查
        assert self._song is not None
        for child in root:
            tag = child.tag
            if tag == 'GPVersion':
                # GP 版本号: 7=GP7, 8=GP8
                ver_text = (child.text or '').strip()
                self._song.gp_version = f"{ver_text}.0" if ver_text else "7.0"
            elif tag == 'GPRevision':
                # GP 修订号(如 12020) - 暂存到 stylesheet 字段(后续可扩展)
                pass
            elif tag == 'Score':
                self._parse_score_node(child)
            elif tag == 'MasterTrack':
                self._parse_master_track_node(child)
            elif tag == 'Tracks':
                self._parse_tracks_node(child)
            elif tag == 'MasterBars':
                self._parse_master_bars_node(child)
            elif tag == 'Bars':
                self._parse_bars(child)
            elif tag == 'Voices':
                self._parse_voices(child)
            elif tag == 'Beats':
                self._parse_beats(child)
            elif tag == 'Notes':
                self._parse_notes(child)
            elif tag == 'Rhythms':
                self._parse_rhythms(child)
            # 其他节点(Assets/LayoutConfiguration 等)暂不解析

        # [v1.4.0] 解析和弦表 - 在所有节点遍历完成后执行
        # 原因: 和弦定义嵌在 <Tracks>/<PartConfiguration>/<Item> 等嵌套位置,
        #       用 .//Chord 一次性拿到所有定义, 过滤有 <KeyNote> 的真定义,
        #       按 document order 建立索引, 供 _build_model 查表。
        self._parse_chord_definitions(root)

    def _parse_chord_definitions(self, root: ET.Element) -> None:
        """
        [v1.4.0] 解析 GPIF 中所有 <Chord> 定义节点, 建立按 document order 的索引表

        GPIF 结构:
          <Tracks>
            <Track>
              <PartConfiguration>      ← (GP7/8 实际是 PartSounding, 但 Chord 嵌入位置多变)
                <Item>
                  <Chord><KeyNote.../>...</Chord>  ← 真定义 (有 KeyNote)
                </Item>
              </PartConfiguration>
            </Track>
          </Tracks>
          <Beats>
            <Beat>
              <Chord>0</Chord>          ← 引用 (空文本, 无 KeyNote)
            </Beat>
          </Beats>

        重要: .//Chord 会同时拿到定义节点和引用节点, 必须用 KeyNote 子节点过滤

        存储到 self._chord_definitions (list[Chord], 按 document order 索引)
        """
        for chord_el in root.findall('.//Chord'):
            # 引用节点无 KeyNote, 跳过
            if chord_el.find('KeyNote') is None:
                continue
            chord = _build_chord_from_xml(chord_el)
            if chord is not None:
                self._chord_definitions.append(chord)

    # ----- Score 节点解析 -----

    def _parse_score_node(self, node: ET.Element) -> None:
        """
        解析 <Score> 节点 - 提取标题/艺术家/制谱者等元信息

        提取字段映射:
          Title        → song.title
          SubTitle     → song.subtitle
          Artist       → song.artist
          Album        → song.album
          Words        → song.words
          Music        → song.music
          Copyright    → song.copyright
          Tabber       → song.tabber
          Instructions→ song.instructions
          Notices      → song.notices
          ScoreSystemsDefaultLayout → song.default_systems_layout
        """
        # 调用前 self._song 已由 parse_xml 初始化
        assert self._song is not None
        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''

            if tag == 'Title':
                self._song.title = text
            elif tag == 'SubTitle':
                self._song.subtitle = text
            elif tag == 'Artist':
                self._song.artist = text
            elif tag == 'Album':
                self._song.album = text
            elif tag == 'Words':
                self._song.words = text
            elif tag == 'Music':
                self._song.music = text
            elif tag == 'WordsAndMusic':
                # WordsAndMusic 字段: 若 Words/Music 为空则填充
                if text:
                    if not self._song.words:
                        self._song.words = text
                    if not self._song.music:
                        self._song.music = text
            elif tag == 'Copyright':
                self._song.copyright = text
            elif tag == 'Tabber':
                self._song.tabber = text
            elif tag == 'Instructions':
                self._song.instructions = text
            elif tag == 'Notices':
                self._song.notices = text
            elif tag == 'ScoreSystemsDefaultLayout':
                # 默认每页系统数(影响渲染布局)
                try:
                    self._song.default_systems_layout = int(text) if text else 3
                except ValueError:
                    self._song.default_systems_layout = 3

    # ----- MasterTrack 节点解析 -----

    def _parse_master_track_node(self, node: ET.Element) -> None:
        """
        解析 <MasterTrack> 节点 - 提取轨道顺序、tempo 自动化、弱起标记

        关键子节点:
          Tracks:     空格分隔的 Track ID 列表(决定轨道显示顺序)
          Automations: tempo 速度自动化
          Anacrusis:  弱起小节标记
        """
        for c in node:
            tag = c.tag
            if tag == 'Tracks':
                # 轨道 ID 顺序列表(空格分隔)
                text = (c.text or '').strip()
                self._tracks_mapping = text.split() if text else []
            elif tag == 'Anacrusis':
                self._has_anacrusis = True
            elif tag == 'Automations':
                self._parse_master_automations(c)

    def _parse_master_automations(self, node: ET.Element) -> None:
        """
        解析 MasterTrack/Automations - 提取 tempo 自动化

        原理:
          tempo 自动化格式: <Value>120 2</Value> (BPM + 拍号引用)
          只取第一个 tempo 自动化作为歌曲全局 tempo
        """
        for automation in node:
            if automation.tag != 'Automation':
                continue

            auto_type = ''
            auto_value = ''
            for c in automation:
                if c.tag == 'Type':
                    auto_type = (c.text or '').strip()
                elif c.tag == 'Value':
                    auto_value = (c.text or '').strip()
                elif c.tag == 'Text':
                    # tempo 文本标记(如 "Moderate")
                    self._master_tempo_name = (c.text or '').strip()

            if auto_type == 'Tempo' and auto_value:
                # 解析 "120 2" 格式，取第一个数字作为 BPM
                parts = auto_value.split()
                if parts:
                    with contextlib.suppress(ValueError):
                        self._master_tempo = int(parts[0])

    # ----- Tracks 节点解析 -----

    def _parse_tracks_node(self, node: ET.Element) -> None:
        """解析 <Tracks> 节点 - 遍历所有 <Track> 元素"""
        for c in node:
            if c.tag == 'Track':
                self._parse_track(c)

    def _parse_track(self, node: ET.Element) -> None:
        """
        解析单个 <Track> 元素 - 提取轨道信息并创建 GTPTrack

        关键子节点:
          Name:        轨道名称
          ShortName:   短名称(多轨混排时显示)
          Color:       RGB 颜色 "R G B"
          InstrumentSet: 乐器类型(drumKit=打击乐)
          MidiConnection: MIDI 通道/Program/Bank
          Sounds:      音色定义(MIDI Program/Bank)
          PlaybackState: 播放状态(Solo/Mute/Default)
          Staves:      调弦信息(Staff/Properties/Tuning/Pitches)
          Articulations: 打击乐 articulation 列表(GP7/GP8 鼓轨)
        """
        track_id = node.get('id', '')
        track = GTPTrack()
        track.number = len(self._tracks_by_id) + 1  # 1-based 编号

        # 默认显示配置(GP7 默认显示五线谱+TAB)
        track.show_standard_notation = True
        track.show_tablature = True

        # 临时变量
        is_percussion = False
        sound = _GpifSound()

        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''

            if tag == 'Name':
                track.name = text
            elif tag == 'ShortName':
                track.short_name = text
            elif tag == 'Color':
                # 颜色格式: "R G B" (空格分隔)
                parts = text.split()
                if len(parts) >= 3:
                    try:
                        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                        track.color = (r, g, b, 255)
                    except ValueError:
                        pass
            elif tag == 'InstrumentSet':
                # 检测打击乐轨道
                is_percussion = self._parse_instrument_set(c, track)
            elif tag == 'MidiConnection':
                # 解析 MIDI 连接信息
                self._parse_midi_connection(c, track, sound)
            elif tag == 'GeneralMidi':
                # 旧版 MIDI 配置
                self._parse_general_midi(c, track, sound)
            elif tag == 'PlaybackState':
                # 播放状态: Solo/Mute/Default
                if text == 'Solo':
                    track.is_solo = True
                elif text == 'Mute':
                    track.is_mute = True
            elif tag == 'Staves':
                # 解析调弦信息
                self._parse_staves(c, track)
            elif tag == 'Sounds':
                # 解析音色(提取 MIDI Program)
                self._parse_sounds(c, sound)
            elif tag == 'Articulations':
                # [v1.1.2] 解析打击乐 articulation 列表
                self._parse_articulations(c, track)
            elif tag == 'Lyrics':
                # track 级歌词 <Lyrics><Line><Offset>..<Text>..</Line></Lyrics>
                self._parse_lyrics(track_id, c)

        # 存储到映射表
        track.is_percussion = is_percussion
        self._tracks_by_id[track_id] = track
        self._track_sounds[track_id] = sound
        self._track_is_percussion[track_id] = is_percussion

        # 设置 MIDI 乐器编号(若未从 Sounds 解析到则用默认)
        if sound.program > 0:
            track.instrument = sound.program

        # 设置 MIDI Bank MSB/LSB (GP7/GP8 支持音色库选择)
        # sound.bank = (MSB << 7) | LSB, 范围 0-16383
        # 拆分为 7-bit MSB/LSB 存入 track, 供 midi_converter 发送 Bank Select
        track.midi_bank_msb = (sound.bank >> 7) & 0x7F
        track.midi_bank_lsb = sound.bank & 0x7F

    def _parse_instrument_set(self, node: ET.Element, track: GTPTrack) -> bool:
        """
        解析 <InstrumentSet> 节点 - 检测是否打击乐

        返回:
            True=打击乐轨道, False=普通轨道
        """
        is_percussion = False
        for c in node:
            if c.tag == 'Type':
                # Type=drumKit 表示打击乐
                if (c.text or '').strip().lower() == 'drumkit':
                    is_percussion = True
            elif c.tag == 'LineCount':
                # 五线谱行数(普通吉他5行，鼓谱5行/3行)
                # 调整效果: 行数影响五线谱渲染(本程序不渲染五线谱，仅记录)
                with contextlib.suppress(ValueError):
                    int((c.text or '').strip())
        return is_percussion

    def _parse_articulations(self, node: ET.Element, track: GTPTrack) -> None:
        """
        解析 <Articulations> 节点 - GP7/GP8 鼓轨打击乐 articulation 列表

        GPIF XML 结构示例:
          <Articulations>
            <Articulation>
              <Name>Snare (hit)</Name>
              <InputMidiNumbers>38</InputMidiNumbers>
              <OutputMidiNumber>38</OutputMidiNumber>
              <StaffLine>0</StaffLine>
              ...
            </Articulation>
            ...
          </Articulations>

        每个 <Articulation> 在 alphaTab 中对应一个 InstrumentArticulation，
        其中 <OutputMidiNumber> 是 GM 标准 MIDI 鼓音编号。

        <InstrumentArticulation> 在 <Note> 中是一个整数索引，指向本列表。
        midi_converter 会根据该索引从 track.percussion_articulations 中
        取出 output_midi_number 作为真实 MIDI pitch。

        参数:
            node:  <Articulations> XML 节点
            track: 当前 GTPTrack 对象
        """
        articulations: list[PercussionArticulation] = []
        for c in node:
            if c.tag != 'Articulation':
                continue

            articulation = PercussionArticulation()
            for sub in c:
                sub_text = (sub.text or '').strip() if sub.text else ''
                if sub.tag == 'Name':
                    articulation.name = sub_text
                elif sub.tag == 'OutputMidiNumber':
                    try:
                        articulation.output_midi_number = int(sub_text)
                    except ValueError:
                        articulation.output_midi_number = -1
                elif sub.tag == 'InputMidiNumbers':
                    # alphaTab 用第一个值作为 articulation 内部 id
                    parts = sub_text.split()
                    if parts:
                        with contextlib.suppress(ValueError):
                            articulation.unique_id = parts[0]
                elif sub.tag == 'StaffLine':
                    with contextlib.suppress(ValueError):
                        articulation.staff_line = int(sub_text)
                elif sub.tag == 'Type':
                    # 元素类型，与 Name 组合成唯一键
                    articulation.element_type = sub_text

            articulations.append(articulation)

        track.percussion_articulations = articulations

    def _parse_midi_connection(self, node: ET.Element, track: GTPTrack, sound: _GpifSound) -> None:
        """解析 <MidiConnection> 节点 - MIDI 通道设置"""
        for c in node:
            tag = c.tag
            (c.text or '').strip() if c.text else ''
            if tag == 'PrimaryChannel':
                # 主通道(暂存到 track.number 用于后续 MIDI 转换分配)
                pass
            elif tag == 'SecondaryChannel':
                # 副通道(用于吉他双重音色)
                pass

    def _parse_general_midi(self, node: ET.Element, track: GTPTrack, sound: _GpifSound) -> None:
        """解析 <GeneralMidi> 节点 - 旧版 MIDI 配置"""
        for c in node:
            if c.tag == 'Program':
                with contextlib.suppress(ValueError):
                    sound.program = int((c.text or '').strip())

    def _parse_staves(self, node: ET.Element, track: GTPTrack) -> None:
        """
        解析 <Staves> 节点 - 提取调弦信息

        结构:
          <Staves><Staff><Properties>
            <Property name="Tuning"><Pitches>40 45 50 55 59 64</Pitches></Property>
            <Property name="CapoFret"><Fret>0</Fret></Property>
            <Property name="FretCount"><Number>24</Number></Property>
          </Properties></Staff></Staves>
        """
        for staff in node:
            if staff.tag != 'Staff':
                continue
            for props in staff:
                if props.tag != 'Properties':
                    continue
                for prop in props:
                    if prop.tag != 'Property':
                        continue
                    name = prop.get('name', '')
                    if name == 'Tuning':
                        # 提取调弦: <Pitches>40 45 50 55 59 64</Pitches>
                        for sub in prop:
                            if sub.tag == 'Pitches':
                                text = (sub.text or '').strip()
                                if text:
                                    try:
                                        pitches = [int(p) for p in text.split()]
                                        # 调弦元组(从1弦到6弦的MIDI音高)
                                        # GPIF 中 Pitches 顺序: 40 45 50 55 59 64 = 6弦到1弦(粗到细)
                                        # ApolloTab 约定: 0=1弦(高音E), 5=6弦(低音E)
                                        # 所以需要反转: [64, 59, 55, 50, 45, 40]
                                        track.strings = tuple(reversed(pitches))
                                    except ValueError:
                                        pass
                    elif name == 'CapoFret':
                        # 变调夹位置
                        for sub in prop:
                            if sub.tag == 'Fret':
                                with contextlib.suppress(ValueError):
                                    track.capo = int((sub.text or '0').strip())
                    elif name == 'FretCount':
                        # 品格数
                        for sub in prop:
                            if sub.tag == 'Number':
                                with contextlib.suppress(ValueError):
                                    track.fret_count = int((sub.text or '24').strip())

    def _parse_sounds(self, node: ET.Element, sound: _GpifSound) -> None:
        """解析 <Sounds> 节点 - 提取 MIDI Program/Bank"""
        for sound_node in node:
            if sound_node.tag != 'Sound':
                continue
            for c in sound_node:
                if c.tag == 'MIDI':
                    for midi_c in c:
                        if midi_c.tag == 'Program':
                            with contextlib.suppress(ValueError):
                                sound.program = int((midi_c.text or '0').strip())
                        elif midi_c.tag == 'MSB':
                            # Bank MSB(高7位)
                            with contextlib.suppress(ValueError):
                                sound.bank = (sound.bank & 0x7F) | (
                                    int((midi_c.text or '0').strip()) << 7
                                )
                        elif midi_c.tag == 'LSB':
                            # Bank LSB(低7位)
                            with contextlib.suppress(ValueError):
                                sound.bank = (sound.bank & 0x3F80) | (
                                    int((midi_c.text or '0').strip()) & 0x7F
                                )
            # 只取第一个 Sound 的 MIDI 信息
            break

    # ----- MasterBars 节点解析 -----

    def _parse_master_bars_node(self, node: ET.Element) -> None:
        """解析 <MasterBars> 节点 - 遍历所有 <MasterBar>"""
        for c in node:
            if c.tag == 'MasterBar':
                self._parse_master_bar(c)

    def _parse_master_bar(self, node: ET.Element) -> None:
        """
        解析单个 <MasterBar> - 提取拍号/反复/调号/段落标记等

        关键子节点:
          Time:              拍号 "4/4"
          Key:               调号(AccidentalCount + Mode)
          Bars:              包含的 Bar ID 列表(每轨道一个)
          Repeat:            反复记号(start/end + count)
          DoubleBar:         双竖线
          Section:           段落标记(Letter + Text)
          AlternateEndings:  反复交替结束
          TripletFeel:       三连音感
          Directions:        跳转方向(DaCapo/DalSegno/Coda/Fine)
        """
        # MasterBar 属性字典(用于第二遍组装)
        mb_data: dict[str, Any] = {
            'time_signature': (4, 4),
            'key_signature': 0,
            'is_repeat_open': False,
            'repeat_close': -1,
            'is_double_bar': False,
            'marker': None,
            'section_text': None,
            'alternate_endings': 0,
            'triplet_feel': None,
            'directions': [],
            'is_anacrusis': False,
        }

        # 第一个 MasterBar 若有弱起标记则设置
        if len(self._master_bars) == 0 and self._has_anacrusis:
            mb_data['is_anacrusis'] = True

        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''

            if tag == 'Time':
                # 拍号 "4/4"
                parts = text.split('/')
                if len(parts) == 2:
                    with contextlib.suppress(ValueError):
                        mb_data['time_signature'] = (int(parts[0]), int(parts[1]))
            elif tag == 'Key':
                # 调号
                for kc in c:
                    if kc.tag == 'AccidentalCount':
                        with contextlib.suppress(ValueError):
                            mb_data['key_signature'] = int((kc.text or '0').strip())
            elif tag == 'Bars':
                # Bar ID 列表(空格分隔)
                bar_ids = text.split() if text else []
                self._bars_of_master_bar.append(bar_ids)
            elif tag == 'Repeat':
                # 反复记号: <Repeat start="true" end="false" count="2"/>
                if c.get('start', '').lower() == 'true':
                    mb_data['is_repeat_open'] = True
                if c.get('end', '').lower() == 'true':
                    count_str = c.get('count', '1')
                    try:
                        mb_data['repeat_close'] = int(count_str)
                    except ValueError:
                        mb_data['repeat_close'] = 1
            elif tag == 'DoubleBar':
                mb_data['is_double_bar'] = True
            elif tag == 'Section':
                # 段落标记
                letter = ''
                section_text = ''
                for sc in c:
                    if sc.tag == 'Letter':
                        letter = (sc.text or '').strip()
                    elif sc.tag == 'Text':
                        section_text = (sc.text or '').strip()
                # 合并 letter + text 作为段落名
                mb_data['section_text'] = section_text if section_text else letter
                mb_data['marker'] = section_text if section_text else letter
            elif tag == 'AlternateEndings':
                # 反复交替结束: 位标志
                parts = text.split() if text else []
                bits = 0
                for p in parts:
                    try:
                        n = int(p)
                        if n >= 1:
                            bits |= 1 << (n - 1)
                    except ValueError:
                        pass
                mb_data['alternate_endings'] = bits
            elif tag == 'TripletFeel':
                mb_data['triplet_feel'] = _TRIPLET_FEEL_MAP.get(text)
            elif tag == 'Directions':
                # 跳转方向(DaCapo/DalSegno/Coda/Fine 等)
                for dc in c:
                    if dc.tag in ('Target', 'Jump'):
                        dir_text = (dc.text or '').strip()
                        if dir_text:
                            mb_data['directions'].append(dir_text)

        self._master_bars.append(mb_data)

    # ----- Bars 节点解析 -----

    def _parse_bars(self, node: ET.Element) -> None:
        """解析 <Bars> 节点 - 遍历所有 <Bar>"""
        for c in node:
            if c.tag == 'Bar':
                self._parse_bar(c)

    def _parse_bar(self, node: ET.Element) -> None:
        """
        解析单个 <Bar> - 提取 Voice 引用和谱号

        关键子节点:
          Voices: Voice ID 列表(空格分隔, -1=未使用)
          Clef:   谱号(G2/F4/C4/C3/Neutral)
          Ottavia: 八度移调
        """
        bar_id = node.get('id', '')
        bar_data: dict[str, Any] = {
            'voice_ids': [],
            'clef': 'G2',
            'ottava': None,
        }

        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''
            if tag == 'Voices':
                bar_data['voice_ids'] = text.split() if text else []
            elif tag == 'Clef':
                bar_data['clef'] = text or 'G2'
            elif tag == 'Ottavia':
                bar_data['ottava'] = text or None

        self._bars_by_id[bar_id] = bar_data

    # ----- Voices 节点解析 -----

    def _parse_voices(self, node: ET.Element) -> None:
        """解析 <Voices> 节点 - 遍历所有 <Voice>"""
        for c in node:
            if c.tag == 'Voice':
                self._parse_voice(c)

    def _parse_voice(self, node: ET.Element) -> None:
        """
        解析单个 <Voice> - 提取 Beat 引用列表

        结构: <Voice id="0"><Beats>0 1 2</Beats></Voice>
        """
        voice_id = node.get('id', '')
        beat_ids: list[str] = []
        for c in node:
            if c.tag == 'Beats':
                text = (c.text or '').strip()
                beat_ids = text.split() if text else []
        self._voices_of_bar[voice_id] = beat_ids
        self._voice_by_id[voice_id] = {'beat_ids': beat_ids}

    # ----- Beats 节点解析 -----

    def _parse_beats(self, node: ET.Element) -> None:
        """解析 <Beats> 节点 - 遍历所有 <Beat>"""
        for c in node:
            if c.tag == 'Beat':
                self._parse_beat(c)

    def _parse_beat(self, node: ET.Element) -> None:
        """
        解析单个 <Beat> - 提取拍属性(Note 引用/Rhythm 引用/力度/装饰音等)

        关键子节点:
          Notes:      Note ID 列表(空格分隔)
          Rhythm:     Rhythm 引用(ref 属性)
          Dynamic:    力度标记(PPP/PP/P/MP/MF/F/FF/FFF)
          Fadding:    淡入淡出(FadeIn/FadeOut/VolumeSwell)
          Tremolo:    震音拨弦(1/2/1/4/1/8)
          Hairpin:    渐强渐弱(Crescendo/Decrescendo)
          Arpeggio:   扫弦方向(Up/Down)
          FreeText:   自由文字标注
          Chord:      和弦图引用
          TransposedPitchStemOrientation: 符干方向
        """
        beat_id = node.get('id', '')
        beat = GTPBeat()

        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''

            if tag == 'Notes':
                # Note ID 列表
                self._notes_of_beat[beat_id] = text.split() if text else []
            elif tag == 'Rhythm':
                # Rhythm 引用 <Rhythm ref="0"/>
                self._rhythm_of_beat[beat_id] = c.get('ref', '')
            elif tag == 'Dynamic':
                # 力度标记 → 写入 beat.dynamics
                if text in _DYNAMIC_MAP:
                    beat.dynamics = text
            elif tag == 'Fadding':
                # 淡入淡出
                if text in ('FadeIn', 'FadeOut', 'VolumeSwell'):
                    beat.fade = text
            elif tag == 'Tremolo':
                # 震音拨弦: "1/2"=1线, "1/4"=2线, "1/8"=3线
                if text == '1/2':
                    beat.tremolo_picking = 2
                elif text == '1/4':
                    beat.tremolo_picking = 4
                elif text == '1/8':
                    beat.tremolo_picking = 8
            elif tag == 'Hairpin':
                # 渐强渐弱
                if text in ('Crescendo', 'Decrescendo'):
                    beat.crescendo = text
            elif tag == 'Arpeggio':
                # 扫弦方向
                if text == 'Up':
                    beat.brush_type = 'Up'
                elif text == 'Down':
                    beat.brush_type = 'Down'
            elif tag == 'FreeText':
                # 自由文字标注
                beat.text = text
            elif tag == 'Chord':
                # [v1.4.0] 和弦图引用 - text 是 _chord_definitions 中的索引
                # 真正的定义解析在 _parse_chord_definitions (第一遍末尾)
                # 这里只记录索引, _build_model 阶段再 attach 到 beat.chord
                if text:
                    try:
                        self._chord_idx_of_beat[beat_id] = int(text)
                    except ValueError:
                        self._chord_idx_of_beat[beat_id] = -1
                else:
                    self._chord_idx_of_beat[beat_id] = -1
            elif tag == 'GraceNotes' and text in ('OnBeat', 'BeforeBeat'):
                # 装饰音(OnBeat/BeforeBeat)
                beat.grace_type = text
            elif tag == 'Lyrics':
                # beat 级歌词 <Lyrics><Line>...</Line></Lyrics> - 直接写 beat.lyrics
                # 存在 beat 级歌词时跳过 track 级 apply（与 alphaTab 一致）
                beat.lyrics = self._parse_beat_lyrics(c)
                self._skip_apply_lyrics = True

        self._beat_by_id[beat_id] = beat

    # ----- 歌词节点解析 (移植 alphaTab GpifParser) -----

    def _parse_lyrics(self, track_id: str, node: ET.Element) -> None:
        """
        解析 track 级 <Lyrics> 节点

        GPIF 结构:
          <Lyrics>
            <Line><Offset>0</Offset><Text>Hel-lo world</Text></Line>
            <Line>...</Line>
          </Lyrics>

        每条 <Line> 转为一个 Lyrics 对象（start_bar=Offset, text=Text），
        收集到 _lyrics_by_track[track_id]，待 _build_model 后由 track.apply_lyrics 分配。
        """
        tracks: list[Lyrics] = []
        for c in node:
            if c.tag == 'Line':
                tracks.append(self._parse_lyrics_line(c))
        self._lyrics_by_track[track_id] = tracks

    def _parse_lyrics_line(self, node: ET.Element) -> Lyrics:
        """解析单条 <Line> → Lyrics(start_bar, text)"""
        lyrics = Lyrics()
        for c in node:
            tag = c.tag
            if tag == 'Offset':
                # GPIF Offset 为 0-based 起始小节索引（与 alphaTab 一致）
                # GP 文件为不可信数据，Offset 可能缺失/非数字，失败时回退 0
                offset_text = (c.text or '').strip()
                try:
                    lyrics.start_bar = int(offset_text)
                except ValueError:
                    lyrics.start_bar = 0
            elif tag == 'Text':
                lyrics.text = c.text or ''
        return lyrics

    def _parse_beat_lyrics(self, node: ET.Element) -> list[str]:
        """
        解析 beat 级 <Lyrics> 节点 → list[str]（每条 <Line> 一项）

        GPIF 结构:
          <Lyrics><Line>Hel-lo</Line><Line>world</Line></Lyrics>

        返回各 <Line> 的纯文本（未经 chunk 解析，逐拍已分好）。
        """
        lines: list[str] = []
        for c in node:
            if c.tag == 'Line':
                lines.append(c.text or '')
        return lines

    # ----- Notes 节点解析 -----

    def _parse_notes(self, node: ET.Element) -> None:
        """解析 <Notes> 节点 - 遍历所有 <Note>"""
        for c in node:
            if c.tag == 'Note':
                self._parse_note(c)

    def _parse_note(self, node: ET.Element) -> None:
        """
        解析单个 <Note> - 提取音符属性(弦/品/技巧/推弦等)

        关键子节点:
          Properties:    音符属性集合(String/Fret/Bended/Slide/HarmonicType 等)
          AntiAccent:    幽灵音(normal)
          LetRing:       延音标记
          Trill:         颤音
          Accent:        重音位标志(bit0=staccato, bit2=heavy, bit3=normal, bit4=tenuto)
          Tie:           连音(destination 属性)
          Vibrato:       颤音(Slight/Wide)
          LeftFingering: 左手指法(P/I/M/A/C)
          RightFingering:右手指法
          InstrumentArticulation: 打击乐编号

        Properties 中的关键 Property:
          String:     弦号(GPIF 0-based, 内部 0-based)
          Fret:       品格数
          Bended:     推弦标志
          BendOriginValue/Offset: 推弦起点
          BendMiddleValue/Offset1/Offset2: 推弦中点
          BendDestinationValue/Offset: 推弦终点
          Muted:      Dead note
          PalmMuted:  闷音
          HarmonicType: 泛音类型
          HarmonicFret:  泛音品位
          HopoOrigin: 击弦/勾弦起始
          Slide:      滑音(Flags 位标志)
          ConcertPitch/TransposedPitch: 音高信息
          Octave/Tone: 八度/音级
        """
        note_id = node.get('id', '')
        note = GTPNote()

        # 临时变量(用于推弦曲线组装)
        is_bended = False
        bend_origin = None  # (offset, value)
        bend_middle_value = None
        bend_middle_offset1 = None
        bend_middle_offset2 = None
        bend_destination = None  # (offset, value)
        midi_pitch_from_prop = None  # 从 ConcertPitch/TransposedPitch 提取的 MIDI

        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''

            if tag == 'Properties':
                # Properties 包含多个 Property 子节点
                for prop in c:
                    if prop.tag != 'Property':
                        continue
                    name = prop.get('name', '')

                    if name == 'String':
                        # 弦号: <String>0</String>(GPIF 0-based, 0=最低音弦/底线)
                        # 注意: 这里暂存原始 GPIF 值, 第二遍 _build_model 会根据
                        # track.strings 反转为 ApolloTab 内部约定(0=最高音弦/顶线).
                        for sub in prop:
                            if sub.tag == 'String':
                                with contextlib.suppress(ValueError):
                                    note.string = int((sub.text or '0').strip())
                    elif name == 'Fret':
                        # 品格数
                        for sub in prop:
                            if sub.tag == 'Fret':
                                with contextlib.suppress(ValueError):
                                    note.fret = int((sub.text or '0').strip())
                    elif name == 'Element':
                        # GP6 打击乐 element
                        for sub in prop:
                            if sub.tag == 'Element':
                                with contextlib.suppress(ValueError):
                                    int((sub.text or '-1').strip())
                    elif name == 'Variation':
                        # GP6 打击乐 variation
                        for sub in prop:
                            if sub.tag == 'Variation':
                                with contextlib.suppress(ValueError):
                                    int((sub.text or '-1').strip())
                    elif name == 'Muted':
                        # Dead note 闷音弹奏
                        for sub in prop:
                            if sub.tag == 'Enable':
                                note.is_dead = True
                                note.add_technique(TechniqueType.PALM_MUTE)
                    elif name == 'PalmMuted':
                        # 闷音(Palm Mute)
                        for sub in prop:
                            if sub.tag == 'Enable':
                                note.is_palm_mute = True
                                note.add_technique(TechniqueType.PALM_MUTE)
                    elif name == 'HopoOrigin':
                        # 击弦/勾弦起始
                        for sub in prop:
                            if sub.tag == 'Enable':
                                note.is_hammer_pull_origin = True
                                note.add_technique(TechniqueType.HAMMER_ON)
                    elif name == 'HarmonicType':
                        # 泛音类型
                        for sub in prop:
                            if sub.tag == 'HType':
                                htype_text = (sub.text or '').strip().lower()
                                note.harmonic_type = _HARMONIC_TYPE_MAP.get(htype_text)
                                # 同步到 techniques 列表(便于 midi_converter 检测)
                                if note.harmonic_type == 'Natural':
                                    note.add_technique(TechniqueType.NATURAL_HARMONIC)
                                elif note.harmonic_type in ('Artificial', 'Tap', 'Pinch'):
                                    note.add_technique(TechniqueType.ARTIFICIAL_HARMONIC)
                    elif name == 'HarmonicFret':
                        # 泛音品位
                        for sub in prop:
                            if sub.tag == 'HFret':
                                with contextlib.suppress(ValueError):
                                    note.harmonic_value = float((sub.text or '0').strip())
                    elif name == 'Slide':
                        # 滑音(位标志)
                        for sub in prop:
                            if sub.tag == 'Flags':
                                try:
                                    flags = int((sub.text or '0').strip())
                                except ValueError:
                                    flags = 0
                                # 滑出类型
                                for bit, slide_type in _SLIDE_FLAG_MAP_OUT.items():
                                    if flags & bit:
                                        note.slide_out_type = slide_type
                                        if (
                                            slide_type in ('Slide Up', 'Shift')
                                            or slide_type == 'Legato'
                                        ):
                                            note.add_technique(TechniqueType.SLIDE_UP)
                                        break
                                # 滑入类型
                                for bit, slide_type in _SLIDE_FLAG_MAP_IN.items():
                                    if flags & bit:
                                        note.slide_in_type = slide_type
                                        if slide_type == 'IntoFromBelow':
                                            note.add_technique(TechniqueType.SLIDE_UP)
                                        elif slide_type == 'IntoFromAbove':
                                            note.add_technique(TechniqueType.SLIDE_DOWN)
                                        break
                    elif name == 'Bended':
                        # 推弦标志
                        is_bended = True
                    elif name == 'BendOriginValue':
                        # 推弦起点值
                        if bend_origin is None:
                            bend_origin = [0, 0]
                        bend_origin[1] = self._to_bend_value(self._read_float_property(prop))
                    elif name == 'BendOriginOffset':
                        # 推弦起点偏移
                        if bend_origin is None:
                            bend_origin = [0, 0]
                        bend_origin[0] = self._to_bend_offset(self._read_float_property(prop))
                    elif name == 'BendMiddleValue':
                        # 推弦中点值
                        bend_middle_value = self._to_bend_value(self._read_float_property(prop))
                    elif name == 'BendMiddleOffset1':
                        # 推弦中点偏移1
                        bend_middle_offset1 = self._to_bend_offset(self._read_float_property(prop))
                    elif name == 'BendMiddleOffset2':
                        # 推弦中点偏移2
                        bend_middle_offset2 = self._to_bend_offset(self._read_float_property(prop))
                    elif name == 'BendDestinationValue':
                        # 推弦终点值
                        if bend_destination is None:
                            bend_destination = [60, 0]  # 默认终点位置=最大
                        bend_destination[1] = self._to_bend_value(self._read_float_property(prop))
                    elif name == 'BendDestinationOffset':
                        # 推弦终点偏移
                        if bend_destination is None:
                            bend_destination = [0, 0]
                        bend_destination[0] = self._to_bend_offset(self._read_float_property(prop))
                    elif name == 'ConcertPitch':
                        # 音高信息(从 Pitch 子节点提取 MIDI)
                        pitch = self._extract_pitch(prop)
                        if pitch is not None and midi_pitch_from_prop is None:
                            midi_pitch_from_prop = pitch
                    elif name == 'TransposedPitch':
                        # 转调后音高(优先使用)
                        pitch = self._extract_pitch(prop)
                        if pitch is not None:
                            midi_pitch_from_prop = pitch

            elif tag == 'AntiAccent':
                # 幽灵音: <AntiAccent>normal</AntiAccent>
                if text.lower() == 'normal':
                    note.is_ghost = True
                    note.add_technique(TechniqueType.GHOST_NOTE)
            elif tag == 'LetRing':
                # 延音
                note.is_let_ring = True
                note.add_technique(TechniqueType.LET_RING)
            elif tag == 'Trill':
                # 颤音
                try:
                    note.trill_value = int(text)
                    note.trill_speed = NoteDuration.SIXTEENTH
                    note.add_technique(TechniqueType.TRILL)
                except ValueError:
                    pass
            elif tag == 'Accent':
                # 重音位标志: bit0=staccato, bit2=heavy, bit3=normal, bit4=tenuto
                try:
                    flags = int(text)
                except ValueError:
                    flags = 0
                if flags & 0x01:
                    note.is_staccato = True
                    note.add_technique(TechniqueType.STACCATO)
                if flags & 0x04:
                    note.accentuated_type = 2  # Heavy
                    note.add_technique(TechniqueType.ACCENTUATED)
                if flags & 0x08:
                    note.accentuated_type = 1  # Normal
                    note.add_technique(TechniqueType.ACCENTUATED)
                if flags & 0x10:
                    note.is_tenuto = True
            elif tag == 'Tie':
                # 连音: <Tie destination="true"/>
                if c.get('destination', '').lower() == 'true':
                    note.is_tie_destination = True
            elif tag == 'Vibrato':
                # 颤音(Slight/Wide)
                # [v1.1.0] 同时写入 vibrato 枚举, 供 MIDI 合成正弦波揉弦使用
                if text == 'Slight':
                    note.add_technique(TechniqueType.VIBRATO)
                    note.vibrato = VibratoType.SLIGHT
                elif text == 'Wide':
                    note.add_technique(TechniqueType.VIBRATO)
                    note.vibrato = VibratoType.WIDE
            elif tag == 'LeftFingering':
                # 左手指法: P/I/M/A/C
                finger_map = {'P': 1, 'I': 2, 'M': 3, 'A': 4, 'C': 5}
                note.left_hand_finger = finger_map.get(text, 0)
            elif tag == 'RightFingering':
                # 右手指法
                finger_map = {'P': 1, 'I': 2, 'M': 3, 'A': 4, 'C': 5}
                note.right_hand_finger = finger_map.get(text, 0)
            elif tag == 'InstrumentArticulation':
                # 打击乐编号
                try:
                    note.percussion_articulation = int(text)
                    note.is_percussion = True
                except ValueError:
                    pass

        # === 推弦曲线组装 ===
        # [v1.1.0] 使用 BendType 枚举 + BendStyle, 单位已为 alphaTab(0~60, 1/4半音)
        if is_bended:
            note.add_technique(TechniqueType.BEND)
            if bend_origin is None:
                bend_origin = [0, 0]
            if bend_destination is None:
                bend_destination = [60, 0]

            # 构建 BendData
            points = []
            points.append((bend_origin[0], bend_origin[1]))
            if bend_middle_value is not None:
                if bend_middle_offset1 is not None:
                    points.append((bend_middle_offset1, bend_middle_value))
                if bend_middle_offset2 is not None:
                    points.append((bend_middle_offset2, bend_middle_value))
                if bend_middle_offset1 is None and bend_middle_offset2 is None:
                    # 无偏移时放在中间位置
                    points.append((30, bend_middle_value))
            points.append((bend_destination[0], bend_destination[1]))

            # 计算推弦峰值(用于显示文字, 1/4 半音单位)
            max_val = max(p[1] for p in points) if points else 0

            # 推断 BendType: 有释放(终点<峰值)→BEND_RELEASE, 否则→BEND
            dest_val = bend_destination[1] if bend_destination else 0
            origin_val = bend_origin[1] if bend_origin else 0
            if origin_val > 0 and dest_val >= origin_val:
                bend_type = BendType.PREBEND
            elif dest_val < max_val and max_val > 0:
                bend_type = BendType.BEND_RELEASE
            else:
                bend_type = BendType.BEND

            note.bend = BendData(
                bend_type=bend_type,
                bend_style=BendStyle.DEFAULT,
                value=dest_val,
                max_value=max_val,
                points=points,
            )

        # === MIDI 音高设置 ===
        if midi_pitch_from_prop is not None:
            note.midi_pitch = midi_pitch_from_prop
        else:
            # 无 ConcertPitch/TransposedPitch 时，根据弦+品+调弦计算
            # 注意: 这里只是占位，第二遍 _build_model 会根据 track.strings 重新计算
            note.midi_pitch = 0

        # === GP6 打击乐 element/variation 映射 ===
        # (本程序暂不实现 GP6 元素映射，保留 element/variation 字段以备扩展)

        self._note_by_id[note_id] = note

    def _read_float_property(self, prop_node: ET.Element) -> float:
        """
        读取 Property 中的 <Float> 值

        参数:
            prop_node: <Property name="BendOriginValue"><Float>12.5</Float></Property>

        返回:
            浮点数值(解析失败返回 0.0)
        """
        for sub in prop_node:
            if sub.tag == 'Float':
                try:
                    return float((sub.text or '0').strip())
                except ValueError:
                    return 0.0
        return 0.0

    def _extract_pitch(self, prop_node: ET.Element) -> int | None:
        """
        从 ConcertPitch/TransposedPitch Property 中提取 MIDI 音高

        结构:
          <Property name="ConcertPitch">
            <Pitch><Step>E</Step><Octave>2</Octave><Accidental></Accidental></Pitch>
          </Property>

        简化策略: 直接读取 Octave+Step 计算近似 MIDI 音高
        (精确转换需要考虑调号和升降号，此处采用近似算法)
        """
        for pitch_node in prop_node:
            if pitch_node.tag != 'Pitch':
                continue
            step = ''
            octave = 0
            accidental = ''
            for sub in pitch_node:
                if sub.tag == 'Step':
                    step = (sub.text or '').strip()
                elif sub.tag == 'Octave':
                    with contextlib.suppress(ValueError):
                        octave = int((sub.text or '0').strip())
                elif sub.tag == 'Accidental':
                    accidental = (sub.text or '').strip()
            # 计算 MIDI 音高: (octave+1)*12 + step_offset + accidental_offset
            step_offset = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
            if step in step_offset:
                midi = (octave + 1) * 12 + step_offset[step]
                # 升降号调整
                if accidental == '#':
                    midi += 1
                elif accidental == 'b':
                    midi -= 1
                elif accidental == 'x':
                    midi += 2
                elif accidental == 'bb':
                    midi -= 2
                return midi
        return None

    # ----- Rhythms 节点解析 -----

    def _parse_rhythms(self, node: ET.Element) -> None:
        """解析 <Rhythms> 节点 - 遍历所有 <Rhythm>"""
        for c in node:
            if c.tag == 'Rhythm':
                self._parse_rhythm(c)

    def _parse_rhythm(self, node: ET.Element) -> None:
        """
        解析单个 <Rhythm> - 提取时值/附点/连音

        关键子节点:
          NoteValue:     时值(Long/Whole/Half/Quarter/Eighth/16th/32nd/...)
          PrimaryTuplet: 连音(num/den 属性)
          AugmentationDot: 附点(count 属性)
        """
        rhythm = _GpifRhythm()
        rhythm.rhythm_id = node.get('id', '')

        for c in node:
            tag = c.tag
            text = (c.text or '').strip() if c.text else ''
            if tag == 'NoteValue':
                rhythm.duration = _NOTE_VALUE_MAP.get(text, NoteDuration.QUARTER)
            elif tag == 'PrimaryTuplet':
                # 连音: <PrimaryTuplet num="3" den="2"/>
                try:
                    rhythm.tuplet_numerator = int(c.get('num', '-1'))
                    rhythm.tuplet_denominator = int(c.get('den', '-1'))
                except ValueError:
                    pass
            elif tag == 'AugmentationDot':
                # 附点: <AugmentationDot count="1"/>
                with contextlib.suppress(ValueError):
                    rhythm.dots = int(c.get('count', '0'))

        self._rhythm_by_id[rhythm.rhythm_id] = rhythm

    # ============================================================
    # 数值转换辅助方法
    # ============================================================

    @staticmethod
    def _to_bend_value(gpx_value: float) -> int:
        """
        GPX 推弦值 → 内部推弦值
        GPX 单位: 25/四分音 (25=1/4音, 100=Full)
        内部单位: 1/四分音 (1=1/4音, 4=Full)

        转换公式: internal = gpx × (1/25)
        """
        return int(gpx_value * _BEND_POINT_VALUE_FACTOR)

    @staticmethod
    def _to_bend_offset(gpx_offset: float) -> int:
        """
        GPX 推弦位置 → 内部推弦位置
        GPX 范围: 0-100
        内部范围: 0-60

        转换公式: internal = gpx × (60/100)
        """
        return int(gpx_offset * _BEND_POINT_POSITION_FACTOR)

    # ============================================================
    # 第二遍: 按 ID 组装 GTPSong 模型
    # ============================================================

    def _build_model(self) -> None:
        """
        按 ID 引用关系组装 GTPSong 模型

        组装流程:
          1. 设置歌曲全局信息(tempo/tempo_name)
          2. 按 _tracks_mapping 顺序添加音轨到 song.tracks
          3. 遍历每个 MasterBar:
             a. 创建 GTPMeasure 并设置拍号/调号/反复等
             b. 按 Bar ID 找到 Bar 属性
             c. 按 Voice ID 找到 Beat ID 列表
             d. 按 Beat ID 克隆 Beat 并设置 Rhythm 属性
             e. 按 Note ID 克隆 Note 并添加到 Beat
          4. 计算每个音符的实际 MIDI 音高(根据弦+品+调弦)
          5. 应用打击乐字段互斥处理

        关键设计:
          - Beat/Note 必须使用 copy.deepcopy 克隆(防止共享对象被修改)
          - 打击乐轨道: note.fret=-1, note.string=-1
          - 非打击乐: note.percussion_articulation=-1
          - 空 Voice(-1 ID) 创建空拍保持小节结构
        """
        # 调用前 self._song 已由 parse_xml 初始化
        assert self._song is not None
        # === Step 1: 设置全局信息 ===
        self._song.tempo = self._master_tempo
        self._song.tempo_name = self._master_tempo_name

        # === Step 2: 添加音轨 ===
        for track_id in self._tracks_mapping:
            if not track_id or track_id == _INVALID_ID:
                continue
            track = self._tracks_by_id.get(track_id)
            if track:
                self._song.tracks.append(track)

        # === Step 3: 遍历 MasterBars 组装小节 ===
        for mb_idx, mb_data in enumerate(self._master_bars):
            # 获取该 MasterBar 的 Bar ID 列表(每个轨道对应一个 Bar ID)
            if mb_idx >= len(self._bars_of_master_bar):
                continue
            bar_ids = self._bars_of_master_bar[mb_idx]

            # 按轨道顺序添加小节
            track_idx = 0
            for bar_id in bar_ids:
                if track_idx >= len(self._song.tracks):
                    break

                if bar_id == _INVALID_ID:
                    # 该轨道在此 MasterBar 无小节(空轨道)
                    track_idx += 1
                    continue

                bar_data = self._bars_by_id.get(bar_id)
                if not bar_data:
                    track_idx += 1
                    continue

                track = self._song.tracks[track_idx]
                is_percussion = track.is_percussion

                # 创建 GTPMeasure
                measure = GTPMeasure()
                measure.number = len(track.measures) + 1  # 1-based 小节序号
                measure.time_signature = mb_data['time_signature']
                measure.key_signature = mb_data['key_signature']
                measure.is_repeat_open = mb_data['is_repeat_open']
                measure.repeat_close = mb_data['repeat_close']
                measure.is_double_bar = mb_data['is_double_bar']
                measure.marker = mb_data['marker']
                measure.section_text = mb_data['section_text']
                measure.alternate_endings = mb_data['alternate_endings']
                measure.triplet_feel = mb_data['triplet_feel']
                measure.directions = list(mb_data['directions'])
                measure.is_anacrusis = mb_data['is_anacrusis']

                # === 处理 Bar 的 Voices ===
                # GPIF 中每个 Bar 最多 4 个 voice (<Voices>0 -1 -1 -1</Voices>):
                #   - 有效 voice ID: 该 voice 有 beats, 正常处理
                #   - 无效 voice ID (-1): 未使用槽位, 直接跳过
                #   - 若 Bar 内所有 voice 都无效: 添加 1 个空拍占位(保持小节结构)
                # 注意: 不要为每个 -1 voice 各加 1 个空拍, 否则会污染小节节奏
                #   (例如 <Voices>0 -1 -1 -1</Voices> 会错误地多出 3 个空拍,
                #    4/4 小节内 2 个 half 音 + 3 个空 quarter rest = 7 quarter beats 溢出)
                voice_ids = bar_data.get('voice_ids', [])
                has_active_voice = False
                for voice_id in voice_ids:
                    if voice_id == _INVALID_ID:
                        # 无效 voice 槽位 → 跳过, 不添加空拍
                        continue

                    beat_ids = self._voices_of_bar.get(voice_id, [])
                    for beat_id in beat_ids:
                        if beat_id == _INVALID_ID:
                            continue

                        # 克隆 Beat(防止共享对象被修改)
                        beat_template = self._beat_by_id.get(beat_id)
                        if beat_template is None:
                            continue
                        beat = copy.deepcopy(beat_template)

                        # 设置 Rhythm 属性
                        rhythm_id = self._rhythm_of_beat.get(beat_id, '')
                        rhythm = self._rhythm_by_id.get(rhythm_id)
                        if rhythm:
                            beat.duration = rhythm.duration
                            beat.is_dotted = rhythm.dots > 0
                            beat.tuplet_numerator = rhythm.tuplet_numerator
                            beat.tuplet_denominator = rhythm.tuplet_denominator

                        # [v1.4.0] 设置和弦 (按 _chord_idx_of_beat 查 _chord_definitions)
                        chord_idx = self._chord_idx_of_beat.get(beat_id, -1)
                        if 0 <= chord_idx < len(self._chord_definitions):
                            beat.chord = self._chord_definitions[chord_idx]

                        # === 添加 Notes ===
                        note_ids = self._notes_of_beat.get(beat_id, [])
                        for note_id in note_ids:
                            if note_id == _INVALID_ID:
                                continue
                            note_template = self._note_by_id.get(note_id)
                            if note_template is None:
                                continue
                            note = copy.deepcopy(note_template)

                            # === 弦号转换(GPIF → ApolloTab 内部约定) ===
                            # GPIF XML 中 <String> 是 0-based, 但 0=最低音弦(底线),
                            # 例如 6 弦吉他: GPIF String=0 → 6弦(底线), String=5 → 1弦(顶线)
                            # 这与 alphaTab 的 1-based 模型一致(alphaTab string=1=底线),
                            # 但与 ApolloTab 的 0-based 约定相反(ApolloTab string=0=顶线).
                            # 因此需要根据实际弦数做反转映射:
                            #   ApolloTab string = (string_count - 1) - GPIF string
                            string_count = len(track.strings)
                            if (
                                not is_percussion
                                and string_count > 0
                                and 0 <= note.string < string_count
                            ):
                                note.string = (string_count - 1) - note.string

                            # === 打击乐字段互斥处理 ===
                            if is_percussion:
                                # 打击乐: 清除弦/品(使用 percussion_articulation)
                                note.fret = -1
                                note.string = -1
                                note.is_percussion = True
                                # 打击乐 MIDI 音高由 percussion_articulation 决定
                                # (midi_converter 会处理)
                                if note.percussion_articulation >= 0:
                                    # 简单映射: articulation 直接作为 MIDI 音高
                                    # GM 鼓音范围 35-81
                                    note.midi_pitch = note.percussion_articulation
                            else:
                                # 非打击乐: 清除 percussion_articulation
                                note.percussion_articulation = -1
                                note.is_percussion = False
                                # 根据 弦+品+调弦 计算 MIDI 音高
                                # (若 note.midi_pitch 已从 ConcertPitch 设置则保留)
                                if note.midi_pitch == 0 and 0 <= note.string < string_count:
                                    open_pitch = track.strings[note.string]
                                    note.midi_pitch = open_pitch + note.fret

                            beat.notes.append(note)

                        # 设置拍的休止符标记(无音符且非空拍)
                        if not beat.notes and not beat.is_rest:
                            beat.is_rest = True

                        measure.beats.append(beat)
                        has_active_voice = True

                # 所有 voice 都无效时, 添加 1 个空拍占位(保持小节结构)
                if not has_active_voice:
                    empty_beat = GTPBeat()
                    empty_beat.is_rest = True
                    measure.beats.append(empty_beat)

                track.measures.append(measure)
                track_idx += 1

        # === Step 4: 清理最后一个 MasterBar 的 DoubleBar 标记 ===
        # alphaTab 兼容性: 最后一个小节的 DoubleBar 标记需要清除
        if self._master_bars and self._song.tracks:
            last_mb = self._master_bars[-1]
            if last_mb.get('is_double_bar'):
                last_mb['is_double_bar'] = False
                # 同步清除最后一个 measure 的标记
                for track in self._song.tracks:
                    if track.measures:
                        track.measures[-1].is_double_bar = False
