"""
============================================================
文件名: midi_converter.py
功能描述: GTP歌曲数据 → MIDI事件序列转换器
         将 GTPSong 数据模型转换为带时间戳的 MIDI 事件列表，
         用于驱动 FluidSynth 合成器进行音频播放
         [v0.4.0] 新增反复记号(Repeat Signs)展开支持，含嵌套反复
         [v0.4.1] 兼容 GP7/GP8 新字段(打击乐/力度/连音/重音)

创建日期: 2026-06-07
最后更新: 2026-06-30 (v1.3.0: 鼓轨 Bank Select 拆分为 CC#0=1/CC#32=0;
                   节拍器混入事件; 修复 GP7/GP8 GPIF String 弦号映射方向)
依赖: Python 3.11+ dataclasses, gtp_engine.models
设计原则:
  - 时间精度: 使用 tick(脉冲)作为最小时间单位，避免浮点累积误差
  - 标准MIDI格式: 兼容标准 MIDI 事件(note_on/note_off/tempo)
  - 可扩展: 支持未来添加控制变化/弯音轮等事件
  - 反复展开: 基于栈结构处理嵌套反复记号，确保MIDI与时间线同步

调用示例:
    from gtp_engine.audio.midi_converter import MidiConverter
    converter = MidiConverter()
    events = converter.convert(song, track_index=0)
    for evt in events:
        print(f"tick={evt.time}: {evt.type} ch={evt.channel} pitch={evt.pitch} vel={evt.velocity}")

反复记号展开原理:
  Guitar Pro 文件中的反复记号(||: 和 :||)在播放时需要重复对应的小节段。
  本模块使用基于栈的展开算法将带反复记号的线性小节序列展开为实际播放顺序。

  示例（简单反复）:
    原始小节: [A] [||:B] [C] [:||2] [D]
    展开序列: [A, B, C, B, C, D]   (B-C段重复2次, 首次+1次重复)

  示例（嵌套反复）:
    原始小节: [A] [||:B] [||:C] [D][:||2] [E][:||2] [F]
    展开序列: [A, B, C, D, C, D, E, B, C, D, C, D, E, F]
              (内层C-D重复2次, 外层B到E整体重复2次)

  算法复杂度: O(n × k), n=原始小节数, k=平均反复次数

GP7/GP8 兼容性 (v0.4.1-v1.0.1):
  - 鼓轨识别: 优先检查 track.is_percussion 字段，再回退到名称匹配
  - 力度映射: beat.dynamics 字段(PPP/PP/P/MP/MF/F/FF/FFF) → MIDI velocity
  - 连音时值: beat.tuplet_numerator/denominator 修正实际时长
  - 重音类型: note.accentuated_type (1=Normal, 2=Heavy) 调整力度
  - 打击乐音高(v1.0.1+): note.is_percussion + percussion_articulation
    通过 track.percussion_articulations 映射到 OutputMidiNumber
  - 音色映射(v1.0.1): 每个音轨开头发送 Bank Select(MSB/LSB) + Program Change,
    GP7/GP8 解析的 bank 保存到 track.midi_bank_msb/lsb
  - 鼓轨 Bank(v1.0.1+): 鼓组 bank 128 拆分为 CC#0=1 / CC#32=0,
    避免发送 0-127 范围外的非法 CC 值
============================================================
"""

from dataclasses import dataclass
from typing import Any

from ..models.beat import GTPBeat
from ..models.measure import GTPMeasure
from ..models.note import BendData, GTPNote
from ..models.song import GTPSong
from ..models.track import GTPTrack

# 导入技巧枚举（用于力度计算和断奏判断）
from ..utils.constants import TechniqueType

# [解耦] 节拍器事件已从 MidiConverter 中分离。
# MetronomeGenerator 现在由 SynthEngine.load_metronome_events() 独立调用，
# 不再通过 MidiConverter 注入。


@dataclass
class MidiEvent:
    """
    单个 MIDI 事件的数据模型

    属性说明:
      time:     绝对时间位置(tick单位, 从歌曲开头开始累加)
      type:     事件类型 ('note_on'|'note_off'|'tempo'|'pitch_bend'|'program_change'|'control_change')
      channel:  MIDI通道 (0-15), 吉他通常用通道0-3
      pitch:    音高/MIDI音高值(0-127); pitch_bend时为弯音值(0-16383,中值8192);
                control_change时为控制器号(CC#); program_change时为乐器号
      velocity: 力度/击弦强度(0-127), note_off时为0; control_change时为控制器值
      value:    附加值(tempo事件用, BPM值)

    调用来源: MidiConverter.convert() 生成的所有MIDI事件
    """

    time: int = 0  # 绝对时间位置(tick)
    type: str = "note_on"  # 事件类型 (note_on/note_off/tempo/pitch_bend)
    channel: int = 0  # MIDI通道
    pitch: int = 0  # MIDI音高 (或pitch_bend值)
    velocity: int = 0  # 力度
    value: int = 0  # 附加值(BPM等)


class MidiConverter:
    """
    GTP歌曲 → MIDI事件序列转换器

    功能:
      将 GTPSong 数据模型转换为按时间排序的 MIDI 事件列表。
      每个音符生成一对 note_on + note_off 事件，
      并在开头插入 tempo 事件设置播放速度。

    时间计算原理:
      - 使用 tick 作为内部时间单位（类似 MIDI 文件的 ticks-per-beat）
      - ticks_per_beat 默认为 480（MIDI 标准分辨率）
      - 每个 Beat 的时长 = (ticks_per_beat) × (四分音符基准 / 时值比例)
      - 附点音符时长 × 1.5

    力度处理:
      - 正常音符: 使用 note.velocity (默认95=mf中强)
      - 幽灵音(Ghost Note): 力度降低到 60 (pp-弱)
      - 断奏(Staccato): 时值缩短50%，力度不变

    参数说明:
      ticks_per_beat: 每个四分音符的tick数，调整效果:
                       越大则时间精度越高(推荐480/960)，越小计算越快
    """

    # MIDI 标准分辨率：每四分音符的 tick 数
    TICKS_PER_BEAT = 480  # 调整效果: 标准 MIDI 分辨率，足够精确

    # 吉他音色 MIDI 通道映射
    GUITAR_CHANNEL = 0  # 主吉他轨道使用通道0

    # 多轨并轨时每轨分配的起始通道号（最多支持16个音轨，通道0-15）
    MULTI_TRACK_START_CHANNEL = 0

    # MIDI标准中通道9(第10通道)是打击乐/鼓组保留通道，不能用于旋律乐器
    # 多轨并轨时自动跳过此通道（非鼓轨）
    PERCUSSION_CHANNEL = 9

    # GM标准鼓音映射范围: MIDI note 35-81 对应不同鼓/镲音色
    # 常用值: 36=底鼓, 38/40=军鼓, 42=闭镲, 44=开镲, 49=碎镲, 51=吊镲
    DRUM_NOTE_RANGE = (35, 82)

    # 鼓轨识别关键词(轨道名称匹配)
    DRUM_KEYWORDS = ('drum', 'percussion', 'drums', 'perc', '鼓')

    # GP7/GP8 力度标记(Dynamics) → MIDI velocity 映射表 (v0.4.1 新增)
    # 调整效果: 修改此表可改变 GP7/GP8 文件的力度解析基准值
    # 来源: alphaTab Dynamic 枚举值 (与 GP7/GP8 GPIF XML <Dynamic> 节点对应)
    DYNAMICS_TO_VELOCITY = {
        'PPP': 15,  # pianississimo (极弱) - 几乎无声
        'PP': 31,  # pianissimo (很弱)
        'P': 47,  # piano (弱)
        'MP': 63,  # mezzo-piano (中弱)
        'MF': 79,  # mezzo-forte (中强) - 默认力度
        'F': 95,  # forte (强)
        'FF': 111,  # fortissimo (很强)
        'FFF': 127,  # fortississimo (极强) - 最大力度
    }

    # GP7/GP8 重音类型常量 (v0.4.1 新增)
    # 来源: alphaTab AccentType 枚举 (GP7 GPIF <Accent> 节点 bit flag)
    ACCENT_NORMAL = 1  # 普通重音 (Normal Accent) → 力度 +15
    ACCENT_HEAVY = 2  # 强重音 (Heavy Accent) → 力度 +25

    def __init__(self, ticks_per_beat: int | None = None):
        """
        初始化转换器

        参数:
            ticks_per_beat: 自定义每拍tick数(None=使用默认480)
        """
        self.ticks_per_beat = ticks_per_beat or self.TICKS_PER_BEAT

    # ================================================================
    # 反复记号展开 (Repeat Signs Expansion)
    # ================================================================

    @staticmethod
    def expand_measure_indices(measures: list) -> list[int]:
        """
        将带反复记号的小节列表展开为实际播放顺序的索引序列

        原理:
          使用栈(Stack)数据结构处理嵌套反复记号:
          1. 遍历每个小节，将索引追加到结果列表
          2. 遇到 is_repeat_open=True → 将当前位置压入栈（记录反复起点）
          3. 遇到 repeat_close > 0 → 弹出最近的open位置，将该段复制 (repeat_close-1) 次
             (repeat_close值表示总重复次数, 首次已播过, 故需额外 repeat_close-1 次)

        嵌套支持:
          栈结构天然支持嵌套: 内层close先弹出内层open, 外层close再弹外层open。
          外层复制时自动包含内层已展开的内容。

        参数:
            measures: GTPMeasure 对象列表（需有 is_repeat_open 和 repeat_close 属性）

        返回:
            list[int]: 展开后的原始小节索引序列
              例如 [0,1,2,1,2,3] 表示第0小节→第1小节→第2小节→回到第1小节→...

        示例:
          简单反复 ||: A B :||2 C
          → 输入: [A(open), B, C(close=2), D]
          → 输出: [0, 1, 0, 1, 3]  (A-B段播放2次后继续D)

          嵌套反复 ||: A ||: B C :||2 D :||2 E
          → 输出: [0, 1, 2, 3, 2, 3, 4, 1, 2, 3, 2, 3, 4, 5]

        调用来源:
          - MidiConverter.convert(): 单轨MIDI转换时使用
          - MidiConverter.convert_all_tracks(): 多轨并轨时使用
          - GTPPlayer.build_timeline(): 时间线构建时同步使用
        """
        result: list[int] = []  # 展开后的索引序列
        stack: list[int] = []  # 反复起点栈(存储result中的位置)

        for idx, measure in enumerate(measures):
            # === 步骤1: 将当前小节加入结果 ===
            result.append(idx)

            # === 步骤2: 检查反复起始记号(||:) ===
            if measure.is_repeat_open:
                # 记录当前result位置(指向刚加入的这个小节)
                stack.append(len(result) - 1)

            # === 步骤3: 检查反复结束记号(:||n) ===
            if measure.repeat_close > 0 and stack:
                # 弹出匹配的反复起点
                start_pos = stack.pop()

                # 提取需要重复的片段(从起点到当前末尾)
                segment = result[start_pos:]

                # 复制 (repeat_close - 1) 次
                # repeat_close=总次数, 首次已播, 故额外 repeat_close-1 次
                extra_repeats = measure.repeat_close - 1
                for _ in range(max(0, extra_repeats)):
                    result.extend(segment)

        return result

    def convert(self, song: GTPSong, track_index: int = 0) -> list[MidiEvent]:
        """
        将 GTPSong 的指定音轨转换为 MIDI 事件序列

        [解耦] 此方法不再接受 metronome_config 参数。节拍器事件已分离到
              SynthEngine.load_metronome_events() 中独立加载和播放。
              这样切换音轨/模式时不必重建主事件流。

        参数:
            song:         GTPSong 歌曲数据对象
            track_index:  要转换的音轨索引(0-based)

        返回:
            list[MidiEvent]: 按时间排序的 MIDI 事件列表

        执行步骤:
          1. 验证输入参数有效性
          2. 在 tick=0 处插入 tempo 事件(BPM)
          3. [v0.4.0] 展开反复记号获取实际播放顺序
          4. 按展开顺序遍历小节→每个拍→每个音符，计算绝对 tick 位置
          5. 为每个非休止符音符生成 note_on + note_off 事件对
          6. 按时间排序返回完整事件列表
        """
        events: list[MidiEvent] = []

        # === 参数验证 ===
        if not song or not song.tracks:
            return events
        if track_index < 0 or track_index >= len(song.tracks):
            return events

        track = song.tracks[track_index]

        # === Step 1: 插入 tempo 事件(歌曲开头的速度标记) ===
        events.append(
            MidiEvent(
                time=0,
                type="tempo",
                channel=self.GUITAR_CHANNEL,
                pitch=0,
                velocity=song.tempo,
                value=song.tempo,
            )
        )

        # === [v1.0.1] Step 1.5: 插入音色事件(Bank Select + Program Change) ===
        # 单轨模式也使用 GUITAR_CHANNEL, 根据 track 实际类型发送鼓组或旋律音色
        program_events = self._create_program_events(track, self.GUITAR_CHANNEL)
        events.extend(program_events)

        # === Step 2: [v0.4.0] 展开反复记号，获取实际播放顺序 ===
        expanded_indices = self.expand_measure_indices(track.measures)

        # [v1.1.2] 获取 GP7/GP8 鼓轨 articulation 映射表
        percussion_articulations = getattr(track, 'percussion_articulations', None)

        # === Step 3: 按展开顺序遍历小节，生成音符事件 ===
        current_tick = 0  # 当前绝对 tick 位置（从歌曲开头开始累加）

        for orig_idx in expanded_indices:
            measure = track.measures[orig_idx]
            measure_events = self._convert_measure(
                measure,
                current_tick,
                self.GUITAR_CHANNEL,
                percussion_articulations=percussion_articulations,
            )
            events.extend(measure_events)

            # 当前小节结束，累加小节总时长到 current_tick
            measure_ticks = self._measure_to_ticks(measure)
            current_tick += measure_ticks

        # [解耦] 原 Step 6 (混入节拍器) 已删除。节拍器事件由
        # SynthEngine.load_metronome_events() 独立加载。

        # === Step 6 (旧)/ Step 5 (新): 按 time 排序确保时序正确 ===
        # 同一时间的事件顺序: 控制/音色设置 → tempo → pitch_bend → note_on → note_off
        # 必须先发送 program_change, 再发送该时刻的音符
        events.sort(
            key=lambda e: (
                e.time,
                {
                    'control_change': 0,
                    'program_change': 1,
                    'tempo': 2,
                    'pitch_bend': 3,
                    'note_on': 4,
                    'note_off': 5,
                }.get(e.type, 99),
            )
        )

        return events

    @staticmethod
    def is_drum_track(track: GTPTrack) -> bool:
        """
        检测一个音轨是否为鼓轨(打击乐轨道)

        检测策略(按优先级):
          1. [v0.4.1] GP7/GP8 显式标记: track.is_percussion 字段
             (GP7 GPIF XML 中 <DrumKit> 节点设置，最权威的鼓轨判定)
          2. 名称匹配: 轨道名称包含 drum/percussion/鼓 等关键词
          3. 音符特征: 鼓轨的音符特征是 fret == midi_pitch
             （因为鼓轨不使用弦/品格系统，直接存储MIDI鼓音编号）
             且所有音符的pitch都在GM鼓音范围(35-81)内

        参数:
            track: GTPTrack 音轨对象

        返回:
            True=是鼓轨, False=普通旋律轨
        """
        # === 策略1: [v0.4.1] GP7/GP8 is_percussion 显式标记 ===
        # GP7/GP8 文件在 GPIF XML 中通过 <DrumKit> 节点显式标记鼓轨
        # 这是权威判定，应优先于名称/特征匹配
        if getattr(track, 'is_percussion', False):
            return True

        # === 策略2: 名称匹配 ===
        name_lower = track.name.lower()
        if any(kw in name_lower for kw in MidiConverter.DRUM_KEYWORDS):
            return True

        # === 策略3: 音符特征检测 ===
        # 鼓轨关键特征: fret == midi_pitch (鼓音直接存为MIDI编号，非品格计算值)
        #              且 pitch 在 GM 鼓音范围(35-81)
        sample_notes = []
        for m in track.measures[:8]:  # 取前8小节样本
            for b in m.beats:
                for n in b.notes:
                    sample_notes.append((n.midi_pitch, getattr(n, 'fret', None)))
                    if len(sample_notes) >= 20:
                        break
                if len(sample_notes) >= 20:
                    break
            if len(sample_notes) >= 20:
                break

        if len(sample_notes) >= 5:  # 至少5个音符才做特征判断
            lo, hi = MidiConverter.DRUM_NOTE_RANGE
            # 检查是否所有样本都满足: (1)在鼓音范围 (2)fret==pitch
            all_drum_like = all(lo <= p <= hi and f == p for p, f in sample_notes)
            if all_drum_like:
                return True

        return False

    def _create_program_events(self, track: GTPTrack, channel: int) -> list[MidiEvent]:
        """
        创建音轨开头的音色设置事件(Bank Select + Program Change)

        发送顺序(符合 MIDI 规范):
          1. Bank Select MSB (CC#0)
          2. Bank Select LSB (CC#32)
          3. Program Change

        特殊处理:
          - 鼓轨: 强制 Bank MSB=128 + Program=0, 启用 GM 鼓组
          - 旋律轨: 若 track.midi_bank_msb/lsb 非零则发送 Bank Select,
                    再发送 track.instrument 作为 Program Change

        参数:
            track:   GTPTrack 音轨对象
            channel: 该音轨分配的 MIDI 通道

        返回:
            音色事件列表, 按发送顺序排列, 时间均为 0
        """
        events: list[MidiEvent] = []

        if self.is_drum_track(track):
            # === 鼓轨: 强制切换到打击乐 Bank 并选择鼓组 Program ===
            # GM 14-bit Bank Select 编码: bank = (MSB << 7) | LSB
            #   鼓组 bank 128 → MSB=128>>7=1, LSB=128&0x7F=0
            # 注意: CC 值合法范围是 0-127, 直接发 128 会被 FluidSynth 忽略。
            # 必须先发 CC#0(MSB) + CC#32(LSB), 再发 Program Change。
            # 发送顺序: CC#0(MSB) → CC#32(LSB) → Program Change
            events.append(
                MidiEvent(
                    time=0,
                    type='control_change',
                    channel=channel,
                    pitch=0,  # CC#0 = Bank Select MSB
                    velocity=1,  # 128 >> 7 = 1 (鼓组 Bank MSB)
                    value=0,
                )
            )
            events.append(
                MidiEvent(
                    time=0,
                    type='control_change',
                    channel=channel,
                    pitch=32,  # CC#32 = Bank Select LSB
                    velocity=0,  # 128 & 0x7F = 0 (鼓组 Bank LSB)
                    value=0,
                )
            )
            events.append(
                MidiEvent(
                    time=0,
                    type='program_change',
                    channel=channel,
                    pitch=0,  # Program 0 = Standard Drum Kit (GM 标准)
                    velocity=0,
                    value=0,
                )
            )
        else:
            # === 旋律轨: Bank Select (可选) + Program Change ===
            msb = getattr(track, 'midi_bank_msb', 0)
            lsb = getattr(track, 'midi_bank_lsb', 0)

            if msb != 0 or lsb != 0:
                # CC#0 Bank Select MSB
                events.append(
                    MidiEvent(
                        time=0,
                        type='control_change',
                        channel=channel,
                        pitch=0,
                        velocity=msb & 0x7F,
                        value=0,
                    )
                )
                # CC#32 Bank Select LSB
                events.append(
                    MidiEvent(
                        time=0,
                        type='control_change',
                        channel=channel,
                        pitch=32,
                        velocity=lsb & 0x7F,
                        value=0,
                    )
                )

            # Program Change (乐器号)
            program = getattr(track, 'instrument', 0)
            if 0 <= program <= 127:
                events.append(
                    MidiEvent(
                        time=0,
                        type='program_change',
                        channel=channel,
                        pitch=program,
                        velocity=0,
                        value=0,
                    )
                )

        return events

    def convert_all_tracks(self, song: GTPSong) -> tuple[list[MidiEvent], list[int]]:
        """
        转换歌曲所有音轨为合并的 MIDI 事件序列（并轨模式）

        原理:
          遍历 GTPSong 中所有音轨，每个音轨分配独立的 MIDI 通道，
          将所有音轨的音符事件合并到一个列表中按时间排序。
          这样播放时所有音轨同时发声，实现"乐队合奏"效果。
          [v0.4.0] 每个音轨独立展开反复记号（不同音轨可能有不同的反复结构）。

        通道分配规则:
          - 音轨0 → MIDI通道0
          - 音轨1 → MIDI通道1
          - ...以此类推，最多支持16个音轨(通道0-15)
          - 超过16个音轨时循环使用通道(取模)
          - [解耦] 节拍器通道(15)由 SynthEngine 独立管理，不参与旋律通道循环

        [解耦] 此方法不再接受 metronome_config 参数。节拍器事件由
              SynthEngine.load_metronome_events() 独立加载和播放。

        参数:
            song:  GTPSong 歌曲数据对象

        返回:
            tuple[events, track_channels]:
              - events: 合并后的全部MIDI事件列表(已排序)
              - track_channels: 每个音轨对应的MIDI通道号列表

        使用场景:
          全轨并轨播放模式 - 用户想听到完整乐队效果而非单轨独奏
        """
        all_events: list[MidiEvent] = []
        track_channels: list[int] = []

        if not song or not song.tracks:
            return all_events, track_channels

        # 只在开头插入一次 tempo 事件（避免重复）
        all_events.append(
            MidiEvent(time=0, type="tempo", channel=0, pitch=0, velocity=0, value=song.tempo)
        )

        # 遍历每个音轨，各自转换后合并
        # 通道分配规则:
        #   - 鼓轨(检测到) → 固定分配通道9(MIDI打击乐保留通道)
        #   - 旋律轨 → 从可用通道中循环分配(0-8, 10-15，跳过通道9)
        # [解耦] 不再为节拍器预留通道15，节拍器由 SynthEngine 独立管理
        _melody_channels = [c for c in range(16) if c != self.PERCUSSION_CHANNEL]
        _melody_idx = 0  # 旋律轨通道计数器

        for _track_idx, track in enumerate(song.tracks):
            # === [v0.4.0] 每个音轨独立展开反复记号 ===
            expanded_indices = self.expand_measure_indices(track.measures)

            # === 鼓轨检测与通道分配 ===
            if self.is_drum_track(track):
                ch = self.PERCUSSION_CHANNEL  # 鼓轨固定使用通道9
            else:
                ch = _melody_channels[_melody_idx % len(_melody_channels)]
                _melody_idx += 1

            track_channels.append(ch)

            # === [v1.0.1] 每个音轨开头插入音色事件 ===
            # 必须先设置 Bank/Program, 再发送该轨道的音符事件
            program_events = self._create_program_events(track, ch)
            all_events.extend(program_events)

            # [v1.1.2] 获取 GP7/GP8 鼓轨 articulation 映射表(用于索引→MIDI note)
            percussion_articulations = getattr(track, 'percussion_articulations', None)

            # 按展开顺序转换该音轨的所有小节
            current_tick = 0
            for orig_idx in expanded_indices:
                measure = track.measures[orig_idx]
                measure_events = self._convert_measure(
                    measure, current_tick, ch, percussion_articulations=percussion_articulations
                )
                all_events.extend(measure_events)
                measure_ticks = self._measure_to_ticks(measure)
                current_tick += measure_ticks

        # [解耦] 原节拍器混入逻辑已删除。节拍器事件由
        # SynthEngine.load_metronome_events() 独立加载。

        # 全局排序：所有轨道的事件按时间统一排序
        # 同一 tick 下: 控制/音色 → tempo → pitch_bend → note_on → note_off
        all_events.sort(
            key=lambda e: (
                e.time,
                {
                    'control_change': 0,
                    'program_change': 1,
                    'tempo': 2,
                    'pitch_bend': 3,
                    'note_on': 4,
                    'note_off': 5,
                }.get(e.type, 99),
            )
        )

        return all_events, track_channels

    def get_all_tracks_duration_ms(self, song: GTPSong) -> float:
        """
        获取所有音轨中最长的总时长(毫秒)

        用于全轨并轨模式下的进度条计算，
        取最长轨道的时长作为总时长。

        参数:
            song: GTPSong 对象

        返回:
            最长音轨的时长(毫秒)
        """
        if not song or not song.tracks:
            return 0.0

        max_duration = 0.0
        for idx in range(len(song.tracks)):
            duration = self.get_total_duration_ms(song, idx)
            max_duration = max(max_duration, duration)

        return max_duration

    def _convert_measure(
        self,
        measure: GTPMeasure,
        start_tick: int,
        channel: int,
        percussion_articulations: list[Any] | None = None,
    ) -> list[MidiEvent]:
        """
        转换单个小节的所有音符为 MIDI 事件

        [v1.1.2] 新增 percussion_articulations 参数:
          GP7/GP8 鼓轨的 note.percussion_articulation 是索引，
          需要通过该列表映射到 OutputMidiNumber。
          不传则保持旧行为（直接用 idx 作为 MIDI pitch）。

        参数:
            measure:    GTPMeasure 小节数据
            start_tick: 该小节开始的绝对 tick 位置
            channel:    MIDI 通道
            percussion_articulations: [v1.1.2] GP7/GP8 鼓轨 articulation 映射表

        返回:
            该小节内所有音符的 note_on/note_off 事件列表
        """
        events: list[MidiEvent] = []
        beat_tick = start_tick  # 当前拍的起始 tick（在小节内相对+绝对）

        for beat in measure.beats:
            # 计算当前拍的 tick 时长
            beat_ticks = self._beat_duration_to_ticks(beat)

            # 跳过空拍和纯休止符拍
            if beat.is_empty or (beat.is_rest and not beat.notes):
                beat_tick += beat_ticks
                continue

            # 为该拍中的每个音符生成 MIDI 事件
            for note in beat.notes:
                if note.is_rest:
                    continue

                # === 计算力度(velocity) ===
                # [v0.4.1] 传入 beat 以支持 GP7/GP8 dynamics 力度标记
                velocity = self._calculate_velocity(note, beat)

                # === [v0.4.1] 计算实际 MIDI 音高 ===
                # GP7/GP8 打击乐音符: 使用 percussion_articulation 作为 MIDI 音高
                # (鼓轨每个 articulation 编号对应 GM 标准鼓音，如 36=底鼓, 38=军鼓)
                # 普通音符: 使用 note.midi_pitch (弦+品+调弦计算得出)
                if getattr(note, 'is_percussion', False) and note.percussion_articulation >= 0:
                    # [v1.1.2] GP7/GP8 鼓轨: percussion_articulation 是索引，
                    # 需通过 track.percussion_articulations 映射到 OutputMidiNumber
                    idx = note.percussion_articulation
                    articulations = percussion_articulations or []
                    if 0 <= idx < len(articulations):
                        effective_pitch = articulations[idx].output_midi_number
                    else:
                        # fallback: 老格式或缺失映射时直接使用 idx
                        effective_pitch = idx
                else:
                    effective_pitch = note.midi_pitch

                # === 计算实际时长(考虑断奏等技巧) ===
                actual_duration = beat_ticks
                if TechniqueType.STACCATO in note.techniques:
                    actual_duration = int(beat_ticks * 0.5)  # 断奏缩短一半

                # === 生成 note_on 事件 ===
                # 重要: 在note_on之前检查是否需要复位pitch_bend!
                # 原因: MIDI Pitch Bend是通道级全局状态，一旦设置会影响后续所有同通道音符。
                #       如果前一个音符有推弦且未正确复位，当前音符就会以错误音高发声。
                # 策略: 检查当前音符是否需要"干净"的弯音状态(即当前音符没有推弦，
                #       或有推弦但起始值为0)，如果是则在note_on前发送pitch_bend=8192复位。
                needs_clean_bend = True  # 默认需要在note_on前复位
                if note.bend and note.bend.value > 0:
                    # 当前音符有推弦 → 检查推弦曲线起点是否为0(从原位开始推)
                    if note.bend.points:
                        first_val = note.bend.points[0]
                        if isinstance(first_val, tuple):
                            start_val = float(first_val[1])
                        else:
                            start_val = getattr(first_val, 'value', 0)
                        if abs(start_val) < 5:  # 起点接近0 → 从原位开始，不需要预先复位
                            needs_clean_bend = False
                    else:
                        # 无曲线点数据但max_value>0 → 推弦从原位开始
                        needs_clean_bend = False

                if needs_clean_bend:
                    # 在note_on前发送pitch_bend=8192复位，确保当前音符以正确音高开始
                    events.append(
                        MidiEvent(
                            time=beat_tick,
                            type="pitch_bend",
                            channel=channel,
                            pitch=8192,  # 无弯音中值
                            velocity=0,
                        )
                    )

                events.append(
                    MidiEvent(
                        time=beat_tick,
                        type="note_on",
                        channel=channel,
                        pitch=effective_pitch,  # [v0.4.1] 打击乐用 articulation, 旋律用 midi_pitch
                        velocity=velocity,
                    )
                )

                # === 生成推弦(Pitch Bend)渐变事件序列 ===
                #
                # 原理: MIDI Pitch Bend是瞬时值变化，只发一个事件会"咔"地突变到目标音高。
                #       真实推弦效果需要发送多个中间值，从8192逐渐过渡到目标值。
                #
                # 调用开源项目: guitarpro (PyGuitarPro库) 解析的BendEffect数据
                # BendData.points 包含曲线点 [(position, value), ...]:
                #   position: 在音符时长的相对位置(0.0~1.0)
                #   value:   四分之一音偏移量(0=无, 25=1/4, 50=1/2, 100=Full)
                #
                # 重要: 无论是否有释放段(has_release)，都必须在note_off后复位pitch_bend到8192！
                #       否则弯音状态会持续影响后续同通道的所有音符
                #
                if note.bend and note.bend.value > 0 and note.bend.max_value > 0:
                    self._generate_bend_events(
                        events, beat_tick, actual_duration, beat_ticks, channel, note.bend
                    )

                # === 生成 note_off 事件(在音符结束时触发) ===
                events.append(
                    MidiEvent(
                        time=beat_tick + actual_duration,
                        type="note_off",
                        channel=channel,
                        pitch=effective_pitch,  # [v0.4.1] 必须与 note_on 的 pitch 一致
                        velocity=0,  # note_off 的 velocity 固定为0
                    )
                )

            # 移动到下一拍
            beat_tick += beat_ticks

        return events

    def _bend_to_midi_pitch(self, bend_data: BendData) -> int:
        """
        将GTP推弦数据(BendData)转换为MIDI Pitch Bend值

        原理:
          MIDI Pitch Bend是14位值(0-16383)，中值8192表示无弯音。
          默认灵敏度范围是±2半音(即±8192)，所以：
            - 1个全音(Full bend) = +8192 (从中值8192到16384，但最大16383)
            - 1/2音 = +4096
            - 1/4音 = +2048

          BendData.value单位是四分之一音(cent):
            - 25 = 1/4音 → MIDI偏移+2048 → 最终值=8192+2048=10240
            - 50 = 1/2音 → MIDI偏移+4096 → 最终值=8192+4096=12288
            - 100 = Full(全音) → MIDI偏移+8192 → 最终值=8192+8192=16384(限制为16383)

        参数:
            bend_data: BendData对象(含value/max_value属性)

        返回:
            MIDI Pitch Bend值(0-16383, 中值8192=无弯音)
        """
        # MIDI中值(无弯音)
        midi_center = 8192

        # 获取推弦量(使用max_value作为峰值，value作为初始值)
        bend_cents = getattr(bend_data, 'max_value', 0) or getattr(bend_data, 'value', 0) or 0

        if bend_cents <= 0:
            return midi_center

        # 转换: 四分之一音 → MIDI偏移量
        # 每个全音(100 cents) = 8192 MIDI单位 (默认灵敏度±2半音)
        # 所以每个四分之一音 = 8192 / 4 = 2048
        midi_offset = int(bend_cents * 81.92)  # 8192 / 100 = 81.92 per cent

        # 计算最终值并限制在有效范围内
        result = midi_center + midi_offset
        return max(0, min(result, 16383))  # 限制: 0 ≤ value ≤ 16383

    def _generate_bend_events(
        self,
        events: list[MidiEvent],
        beat_tick: int,
        actual_duration: int,
        beat_ticks: int,
        channel: int,
        bend_data: BendData,
    ) -> None:
        """
        生成推弦的渐变Pitch Bend事件序列

        原理: MIDI Pitch Bend是瞬时值变化，只发一个事件会"咔"地突变到目标音高。
              真实推弦效果需要发送多个中间值，从8192逐渐过渡到目标值，
              模拟吉他手推弦时音高平滑上升的过程。

        策略:
          - 如果bend_data.points有曲线点数据 → 使用这些点生成渐变序列
          - 如果没有points → 自动生成线性渐变(从0到max_value)
          - 无论是否有释放段 → 在note_off时间点后强制发送pitch_bend=8192复位
              (防止弯音状态泄漏到后续音符)

        参数:
            events: MIDI事件列表(往里面append新事件)
            beat_tick: 当前拍的起始tick位置
            actual_duration: 音符实际时长(tick)
            beat_ticks: 一拍的tick数
            channel: MIDI通道
            bend_data: BendData对象(含value/max_value/points/has_release属性)
        """
        midi_center = 8192  # MIDI无弯音中值

        # === 步骤1: 构建推弦曲线点序列 ===
        # points格式: [(position, value), ...] position∈[0,1], value=四分之一音
        curve_points = getattr(bend_data, 'points', None) or []

        if not curve_points:
            # 无曲线点数据 → 自动生成线性渐变: (0,0) → (0.7, max_value) → (1.0, max_value)
            max_val = bend_data.max_value or bend_data.value or 100
            curve_points = [
                (0.0, 0),  # 起始: 无弯音
                (0.3, max_val * 0.5),  # 30%处: 到达一半
                (0.7, max_val),  # 70%处: 到达峰值
            ]
            # 检查是否有释放段
            if bend_data.has_release:
                curve_points.append((1.0, 0))  # 结束: 回到原位
            else:
                curve_points.append((1.0, max_val))  # 结束: 保持峰值
        else:
            # 有曲线点数据 → 使用原始点(确保起点和终点完整)
            # 解析GTP的点数据: 可能是tuple(position,value)或对象
            parsed_points = []
            for pt in curve_points:
                if isinstance(pt, tuple):
                    parsed_points.append((float(pt[0]), float(pt[1])))
                elif hasattr(pt, 'position') and hasattr(pt, 'value'):
                    parsed_points.append((float(pt.position), float(pt.value)))

            if len(parsed_points) >= 2:
                curve_points = parsed_points
            else:
                # 点数据不足 → 降级为自动生成
                max_val = bend_data.max_value or bend_data.value or 100
                curve_points = [(0.0, 0), (0.7, max_val), (1.0, max_val)]

        # === 步骤2: 将曲线点转换为MIDI pitch_bend事件 ===
        # 最小间隔: 防止事件过于密集(至少10ms间隔，约30ticks@120BPM)
        min_interval = max(beat_ticks // 16, 5)  # 至少1/16拍或5ticks
        last_event_time = 0

        for i, (pos, val) in enumerate(curve_points):
            # 计算此点的绝对时间(tick)
            point_time = beat_tick + int(actual_duration * pos)

            # 确保最小间隔
            if i > 0 and (point_time - last_event_time) < min_interval:
                continue

            # 四分之一音 → MIDI值
            midi_val = self._cents_to_midi(val)

            events.append(
                MidiEvent(
                    time=point_time, type="pitch_bend", channel=channel, pitch=midi_val, velocity=0
                )
            )
            last_event_time = point_time

            # 如果最后一个点的value接近0，说明已有释放段
            if i == len(curve_points) - 1 and abs(val) < 5:
                pass  # 已经回到原位了

        # === 步骤3: 强制复位(在note_off之后) ===
        # 无论曲线是否包含释放段，都在音符结束后+2ticks发送一次8192复位
        # 这是最安全的防线，防止任何情况下弯音泄漏到后续音符
        reset_time = beat_tick + actual_duration + max(beat_ticks // 4, 2)
        events.append(
            MidiEvent(
                time=reset_time, type="pitch_bend", channel=channel, pitch=midi_center, velocity=0
            )
        )

    def _cents_to_midi(self, cents: float) -> int:
        """
        将四分之一音(cent)值转换为MIDI Pitch Bend值

        参数:
            cents: 四分之一音偏移量(正=升高, 负=降低)
                    25=1/4半音, 50=1/2半音, 100=Full全音

        返回:
            MIDI Pitch Bend值(0-16383, 中值8192=无弯音)
        """
        midi_center = 8192
        if abs(cents) < 0.5:
            return midi_center  # 接近0则返回中值

        # 转换公式: 每个四分之一音 = 81.92 MIDI单位
        # Full(100) = 8192, Half(50) = 4096, Quarter(25) = 2048
        midi_offset = int(cents * 81.92)
        result = midi_center + midi_offset
        return max(0, min(result, 16383))

    def _beat_duration_to_ticks(self, beat: GTPBeat) -> int:
        """
        将拍的时值转换为 tick 数

        [v0.4.1] 复用 beat.duration_value 属性，自动支持连音(Tuplet)时值修正
                避免在此重复实现附点/连音逻辑(DRY 原则)

        参数:
            beat: GTPBeat 对象

        返回:
            该拍的时长(以 tick 为单位)

        计算公式:
          ticks = TICKS_PER_BEAT × beat.duration_value
          其中 duration_value 已包含附点(is_dotted)和连音(tuplet)修正

        示例:
          四分音符       → 480 ticks (480 × 1.0)
          八分音符       → 240 ticks (480 × 0.5)
          附点四分       → 720 ticks (480 × 1.5)
          三连音(八分)   → 160 ticks (480 × 0.5 × 2/3)
          五连音(四分)   → 384 ticks (480 × 1.0 × 4/5)
        """
        # 复用 GTPBeat.duration_value 属性(已实现附点+连音修正)
        # 这样修改附点/连音逻辑只需改一处，保证一致性
        ticks = int(self.ticks_per_beat * beat.duration_value)
        return max(ticks, 1)  # 最少1 tick，防止除零

    def _measure_to_ticks(self, measure: GTPMeasure) -> int:
        """
        计算一个小节的总 tick 时长

        基于拍号计算: 总时长 = 分子 × (TICKS_PER_BEAT × 4 / 分母)
        例如 4/4拍 = 4 × 480 = 1920 ticks
        """
        numerator, denominator = measure.time_signature
        return int(numerator * self.ticks_per_beat * 4.0 / denominator)

    def _calculate_velocity(self, note: GTPNote, beat: GTPBeat | None = None) -> int:
        """
        根据音符属性计算实际演奏力度

        [v0.4.1] GP7/GP8 兼容:
          - beat.dynamics 字段(PPP/PP/P/MP/MF/F/FF/FFF) → MIDI velocity 基础值
          - note.accentuated_type (1=Normal, 2=Heavy) 调整力度增量

        规则(按优先级):
          1. 基础力度确定:
             - [v0.4.1] GP7/GP8: beat.dynamics 存在时，使用 DYNAMICS_TO_VELOCITY 映射
             - GP3-5 路径: 使用 note.velocity (默认95=mf中强)
          2. 幽灵音(Ghost Note): 力度上限降到 60 (弱声)
          3. 重音提升力度:
             - [v0.4.1] accentuated_type=1 (Normal) → +15
             - [v0.4.1] accentuated_type=2 (Heavy)  → +25
             - GP3-5 兼容: TechniqueType.ACCENTUATED → +25 (等同 Heavy)

        参数:
            note: GTPNote 音符对象
            beat: GTPBeat 拍对象(可选，用于 GP7/GP8 dynamics 力度解析)

        返回:
            实际力度值 (0-127)
        """
        # === 步骤1: 确定基础力度 ===
        # [v0.4.1] GP7/GP8: 优先使用 beat.dynamics 力度标记
        if beat is not None and getattr(beat, 'dynamics', None):
            base_vel = self.DYNAMICS_TO_VELOCITY.get(beat.dynamics or "", note.velocity)
        else:
            # GP3-5 路径或 GP7/GP8 无 dynamics 标记时使用音符自带力度
            base_vel = note.velocity

        # === 步骤2: 幽灵音降低力度 ===
        if note.is_ghost or TechniqueType.GHOST_NOTE in note.techniques:
            base_vel = min(base_vel, 60)

        # === 步骤3: 重音提升力度 ===
        # [v0.4.1] GP7/GP8: accentuated_type 字段优先
        accent_type = getattr(note, 'accentuated_type', 0)
        if accent_type == self.ACCENT_HEAVY:
            base_vel = min(base_vel + 25, 127)  # Heavy Accent: 强重音 +25
        elif accent_type == self.ACCENT_NORMAL:
            base_vel = min(base_vel + 15, 127)  # Normal Accent: 普通重音 +15
        elif TechniqueType.ACCENTUATED in note.techniques:
            # GP3-5 兼容路径: 技巧列表中的重音视为 Heavy(等同旧逻辑 +25)
            base_vel = min(base_vel + 25, 127)

        # 确保在合法范围内
        return max(0, min(127, int(base_vel)))

    def tick_to_ms(self, tick: int, bpm: int) -> float:
        """
        将 tick 数转换为毫秒时间

        公式: ms = tick × (60000 / (bpm × ticks_per_beat))

        参数:
            tick: tick 数值
            bpm:  每分钟拍数

        返回:
            对应的毫秒数
        """
        if bpm <= 0:
            bpm = 120
        return tick * 60000.0 / (bpm * self.ticks_per_beat)

    def get_total_duration_ms(self, song: GTPSong, track_index: int = 0) -> float:
        """
        获取指定音轨的总播放时长(毫秒)

        用于进度条显示和同步计算

        参数:
            song:         GTPSong 对象
            track_index:  音轨索引

        返回:
            总时长(毫秒)
        """
        if not song or not song.tracks:
            return 0.0
        if track_index >= len(song.tracks):
            return 0.0

        total_ticks = 0
        track = song.tracks[track_index]
        for measure in track.measures:
            total_ticks += self._measure_to_ticks(measure)

        return self.tick_to_ms(total_ticks, song.tempo)
