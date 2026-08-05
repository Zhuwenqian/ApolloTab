"""
============================================================
gtp_engine.audio - 音频播放引擎包
============================================================

功能概述:
  提供 GTP 吉他谱文件的音频播放能力，基于 FluidSynth 合成引擎。

核心模块:
  - midi_converter:  GTPSong → MIDI事件序列转换器
  - synth_engine:    FluidSynth 音频合成引擎(SoundFont驱动)

快速开始:
    from gtp_engine.audio import MidiConverter, SynthEngine

    # 转换为MIDI事件
    converter = MidiConverter()
    events = converter.convert(song, track_index=0)

    # 初始化合成器并播放
    engine = SynthEngine()
    engine.initialize()
    engine.load_soundfont()          # 自动搜索SoundFont
    engine.set_instrument(0, 27)     # 设置电吉他音色
    engine.load_events(events, bpm=song.tempo)
    engine.play()

依赖库:
  - fluidsynth >= 1.2.0 (python-fluidsynth, 开源项目: FluidSynth)

版本: v0.1.0 (Phase 3 - 音频播放)
创建日期: 2026-06-07
============================================================
"""

from .metronome import MetronomeConfig, MetronomeGenerator
from .midi_converter import MidiConverter, MidiEvent
from .synth_engine import SynthEngine

__all__ = [
    'MidiConverter',
    'MidiEvent',
    'SynthEngine',
    'MetronomeConfig',
    'MetronomeGenerator',
]
