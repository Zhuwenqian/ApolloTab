from __future__ import annotations

"""
============================================================
文件名: metronome.py
功能描述: 节拍器 MIDI 事件生成器
         为 GTP 歌曲或纯手动 BPM/拍号配置生成规律的点击声 MIDI 事件，
         用于驱动 FluidSynth 合成器播放节拍器声音。

设计原则:
  - 职责单一: 只负责生成节拍器 note_on/note_off 事件，不参与 UI 或播放控制
  - 可复用: 同时支持 GTP 歌曲模式 (自动跟随 BPM/拍号) 和简单模式 (手动指定 BPM/拍号)
  - 通道隔离: 使用 MIDI 通道 15，避免与旋律轨 (0-8/10-14) 和鼓轨 (9) 冲突
  - 音色通用: 采用 GM 标准木鱼音色 (76 Low Woodblock / 77 High Woodblock)，
              绝大多数 SoundFont 都支持

事件生成规则:
  - 每个小节第一拍为重拍 (High Woodblock, pitch=77)
  - 小节内其余拍为普通拍 (Low Woodblock, pitch=76)
  - 每个点击声持续约 50ms，随后发送 note_off
  - 在事件序列开头插入 Bank Select + Program Change，将通道切换为 GM 鼓组

调用示例:
    from ApolloTab.audio.metronome import MetronomeConfig, MetronomeGenerator

    config = MetronomeConfig(enabled=True, volume=0.8)
    events = MetronomeGenerator.generate_simple(
        bpm=120, numerator=4, denominator=4,
        total_ticks=480 * 4 * 100,  # 100 小节
        ticks_per_beat=480,
        config=config
    )

版本: v1.3.0
创建日期: 2026-06-29
最后更新: 2026-06-30 (v1.3.0: 默认 gain 调整为1.5; 新增通道音量 CC 事件)
============================================================
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅类型检查时导入 MidiEvent 用于注解，避免运行时循环导入;
    # 运行时实例化由各方法内部的延迟导入完成 (见 _create_channel_setup_events 等)
    from ..models.song import GTPSong

    from .midi_converter import MidiEvent


@dataclass
class MetronomeConfig:
    """
    节拍器配置

    参数说明:
      enabled:          是否启用节拍器
      volume:           音量 (0.0 ~ 1.0)，调整效果: 0=静音, 1=最大音量
      gain:             全局音量增益 (>=0)，调整效果: 1.0=原音量, 1.5=1.5倍,
                        用于解决木鱼音色在其他乐器中不够突出、音量滑块开到最大仍被掩盖的问题
      accent_pitch:     重拍 MIDI 音高，默认 77=High Woodblock
      normal_pitch:     普通拍 MIDI 音高，默认 76=Low Woodblock
      accent_velocity:  重拍基础力度 (0-127)，默认 100
                        配合默认 gain=1.5，最大音量时可达 MIDI 上限 127
      normal_velocity:  普通拍基础力度 (0-127)，默认 70
                        配合默认 gain=1.5，最大音量时约 105，仍比重拍稍弱，保留区分度
      channel:          节拍器专用 MIDI 通道，默认 15，避开旋律/鼓通道
      click_duration_ms: 每个点击声持续时间 (毫秒)，默认 50ms
    """

    enabled: bool = False
    volume: float = 0.7
    gain: float = 1.5  # 全局增益，解决木鱼音色被掩盖问题；1.0=原音量, 1.5=1.5倍
    accent_pitch: int = 77  # GM High Woodblock，重拍
    normal_pitch: int = 76  # GM Low Woodblock，普通拍
    accent_velocity: int = 100  # 基础重拍力度，最大音量+默认增益时可达 127 上限
    normal_velocity: int = 70  # 基础普通拍力度，最大音量+默认增益时约 105，保留重拍区分度
    channel: int = 15  # 避开旋律通道 0-8/10-14 和鼓通道 9
    click_duration_ms: int = 50  # 点击声持续时间，单位毫秒

    def scaled_accent_velocity(self) -> int:
        """根据 volume 与全局 gain 缩放后的重拍力度"""
        return max(0, min(127, int(self.accent_velocity * self.volume * self.gain)))

    def scaled_normal_velocity(self) -> int:
        """根据 volume 与全局 gain 缩放后的普通拍力度"""
        return max(0, min(127, int(self.normal_velocity * self.volume * self.gain)))


class MetronomeGenerator:
    """
    节拍器 MIDI 事件生成器

    提供两类生成方式:
      1. generate_for_song: 根据 GTPSong 的小节、拍号、反复记号生成
      2. generate_simple:   根据手动指定的 BPM/拍号生成固定长度事件
    """

    @classmethod
    def _create_channel_setup_events(cls, config: MetronomeConfig) -> list[MidiEvent]:
        """
        创建通道音色设置事件

        将通道切换为 GM 鼓组，并把通道音量开到最大，避免木鱼点击声被其他乐器掩盖:
          - CC#0 (Bank Select MSB) = 1
          - CC#32 (Bank Select LSB) = 0
          - CC#7 (Channel Volume) = 127
          - Program Change = 0 (Standard Drum Kit)
        """
        # [延迟导入] 避免与 midi_converter 循环导入
        from .midi_converter import MidiEvent

        ch = config.channel
        return [
            MidiEvent(
                time=0, type="control_change", channel=ch, pitch=0, velocity=1, value=1
            ),  # CC#0 = Bank MSB
            MidiEvent(
                time=0, type="control_change", channel=ch, pitch=32, velocity=0, value=0
            ),  # CC#32 = Bank LSB
            MidiEvent(
                time=0, type="control_change", channel=ch, pitch=7, velocity=127, value=127
            ),  # CC#7 = Channel Volume, 最大音量
            MidiEvent(
                time=0, type="program_change", channel=ch, pitch=0, velocity=0, value=0
            ),  # Program = 0, Drum Kit
        ]

    @classmethod
    def _click_duration_ticks(cls, bpm: int, ticks_per_beat: int, click_duration_ms: int) -> int:
        """
        计算指定毫秒数对应的 tick 数

        公式:
          每拍时长(ms) = 60000 / BPM
          tick 数 = ticks_per_beat * click_duration_ms / 每拍时长(ms)
                  = ticks_per_beat * click_duration_ms * BPM / 60000
        """
        if bpm <= 0:
            bpm = 120
        ticks = int(ticks_per_beat * click_duration_ms * bpm / 60000.0)
        return max(1, ticks)

    @classmethod
    def _generate_beats(
        cls,
        start_tick: int,
        beat_count: int,
        ticks_per_beat: float,
        bpm: int,
        config: MetronomeConfig,
        accent_first: bool = True,
    ) -> list[MidiEvent]:
        """
        生成一段连续拍的点击事件

        参数:
          start_tick:      起始 tick 位置
          beat_count:      拍数
          ticks_per_beat:  每拍 tick 数
          config:          节拍器配置
          accent_first:    是否第一拍为重拍

        返回:
          该段内所有 note_on/note_off 事件列表
        """
        # [延迟导入] 避免与 midi_converter 循环导入
        from .midi_converter import MidiEvent

        events: list[MidiEvent] = []
        accent_vel = config.scaled_accent_velocity()
        normal_vel = config.scaled_normal_velocity()
        ch = config.channel
        duration_ticks = cls._click_duration_ticks(
            bpm=bpm,
            ticks_per_beat=int(ticks_per_beat) or 480,
            click_duration_ms=config.click_duration_ms,
        )

        for i in range(beat_count):
            is_accent = accent_first and (i == 0)
            pitch = config.accent_pitch if is_accent else config.normal_pitch
            velocity = accent_vel if is_accent else normal_vel
            beat_tick = int(start_tick + i * ticks_per_beat)
            off_tick = beat_tick + duration_ticks

            events.append(
                MidiEvent(
                    time=beat_tick,
                    type="note_on",
                    channel=ch,
                    pitch=pitch,
                    velocity=velocity,
                    value=0,
                )
            )
            events.append(
                MidiEvent(
                    time=off_tick, type="note_off", channel=ch, pitch=pitch, velocity=0, value=0
                )
            )

        return events

    @classmethod
    def generate_for_song(
        cls,
        song: GTPSong,
        track_index: int,
        config: MetronomeConfig,
        expanded_indices: list[int] | None = None,
        ticks_per_beat: int = 480,
    ) -> list[MidiEvent]:
        """
        根据 GTPSong 生成节拍器事件

        参数:
          song:             GTPSong 歌曲对象
          track_index:      用于获取拍号的音轨索引（通常取当前轨）
          config:           节拍器配置
          expanded_indices: 已展开的小节索引序列；若为空则自动展开
          ticks_per_beat:   每四分音符 tick 数，默认 480

        返回:
          按时间排序的节拍器 MIDI 事件列表
        """
        events: list[MidiEvent] = []

        if not config.enabled or not song or not song.tracks:
            return events

        if track_index < 0 or track_index >= len(song.tracks):
            return events

        track = song.tracks[track_index]
        measures = track.measures
        if not measures:
            return events

        # 如果没有传入展开序列，使用线性顺序
        if expanded_indices is None:
            from .midi_converter import MidiConverter

            expanded_indices = MidiConverter.expand_measure_indices(measures)

        bpm = getattr(song, 'tempo', 120) or 120

        # 插入通道设置事件
        events.extend(cls._create_channel_setup_events(config))

        current_tick = 0
        for orig_idx in expanded_indices:
            if orig_idx < 0 or orig_idx >= len(measures):
                continue
            measure = measures[orig_idx]
            numerator, denominator = getattr(measure, 'time_signature', (4, 4))
            if denominator <= 0:
                denominator = 4
            if numerator <= 0:
                numerator = 4

            # 每拍 tick 数 = TICKS_PER_BEAT * 4 / denominator
            ticks_per_single_beat = ticks_per_beat * 4.0 / denominator
            measure_ticks = int(numerator * ticks_per_single_beat)

            # 第一拍重拍，其余普通拍
            events.extend(
                cls._generate_beats(
                    start_tick=current_tick,
                    beat_count=numerator,
                    ticks_per_beat=ticks_per_single_beat,
                    bpm=bpm,
                    config=config,
                    accent_first=True,
                )
            )

            current_tick += measure_ticks

        return events

    @classmethod
    def generate_simple(
        cls,
        bpm: int,
        numerator: int,
        denominator: int,
        total_ticks: int,
        ticks_per_beat: int,
        config: MetronomeConfig,
    ) -> list[MidiEvent]:
        """
        根据手动指定的 BPM/拍号生成固定长度的节拍器事件

        参数:
          bpm:            每分钟拍数
          numerator:      拍号分子（每小节拍数）
          denominator:    拍号分母（以什么音符为一拍，通常为 4）
          total_ticks:    生成事件的总 tick 长度
          ticks_per_beat: 每四分音符 tick 数
          config:         节拍器配置

        返回:
          按时间排序的节拍器 MIDI 事件列表
        """
        events: list[MidiEvent] = []

        if not config.enabled or bpm <= 0 or numerator <= 0 or denominator <= 0:
            return events

        # 插入通道设置事件
        events.extend(cls._create_channel_setup_events(config))

        ticks_per_single_beat = ticks_per_beat * 4.0 / denominator
        measure_ticks = int(numerator * ticks_per_single_beat)
        if measure_ticks <= 0:
            return events

        current_tick = 0
        while current_tick < total_ticks:
            remaining_ticks = total_ticks - current_tick
            remaining_beats = min(numerator, int(remaining_ticks / max(ticks_per_single_beat, 1)))
            if remaining_beats <= 0:
                break

            events.extend(
                cls._generate_beats(
                    start_tick=current_tick,
                    beat_count=remaining_beats,
                    ticks_per_beat=ticks_per_single_beat,
                    bpm=bpm,
                    config=config,
                    accent_first=True,
                )
            )

            current_tick += measure_ticks

        return events
