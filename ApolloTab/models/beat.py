# -*- coding: utf-8 -*-
"""
============================================================
文件名: beat.py
功能描述: 拍(Beat)数据模型 - 存储一拍内同时发声的所有音符
         一拍可以包含多个音符(和弦)或一个休止符

创建日期: 2026-06-06
最后更新: 2026-06-28 (v0.4.0: 扩展 GP7/GP8 字段)
依赖: Python 3.8+ dataclasses
============================================================
"""

from dataclasses import dataclass, field
from typing import List, Optional
from .note import GTPNote
from .chord import Chord
from ..utils.constants import NoteDuration


@dataclass
class GTPBeat:
    """
    一拍(Beat)的数据模型 - 对应 Guitar Pro 中 Voice 内的一个 Beat

    属性说明:
      notes:     该拍内的所有音符列表（同时发声构成和弦）
      duration:  时值类型 (四分/八分/十六分等)
      is_dotted: 是否附点
      text:      该拍上的文字标注（如演奏提示）
      is_rest:   是否为休止符拍
      chord:     该拍关联的和弦(Chord 对象), None=无和弦

    GP7/GP8 扩展字段 (v0.4.0 新增):
      dynamics:          力度标记 (None/PPP/PP/P/MP/MF/F/FF/FFF，对应MIDI力度15~127)
      tuplet_numerator:  连音分子（如三连音=3，五连音=5），-1=无连音
      tuplet_denominator:连音分母（三连音=2），-1=无连音
      brush_type:        扫弦方向 (None/Up/Down)
      brush_duration:    扫弦总时长（tick）
      grace_type:        装饰音类型 (None/OnBeat/BeforeBeat)
      ottava:            八度移调 (None/8va/15ma/8vb/15mb)
      fade:              淡入淡出 (None/FadeIn/FadeOut/VolumeSwell)
      crescendo:         渐强渐弱 (None/Crescendo/Decrescendo)
      tremolo_picking:   震音拨弦标记 (None/2=1线/4=2线/8=3线)
      is_dead_slapped:   Dead Slapped 标记
      lyrics:            逐拍歌词（GP7 支持拍级歌词，None=使用轨道歌词）
      is_slashed:        斜线谱标记
      wah_pedal:         哇音踏板 (None/Open/Closed)
    """

    notes: List[GTPNote] = field(default_factory=list)  # 音符列表（同时发声）
    duration: NoteDuration = NoteDuration.QUARTER        # 时值
    is_dotted: bool = False                              # 是否附点
    text: Optional[str] = None                           # 文字标注
    is_rest: bool = False                                # 是否休止符
    chord: Optional[Chord] = None                        # 和弦 (v1.4.0 新增)

    # === GP7/GP8 扩展字段 (v0.4.0) ===
    dynamics: Optional[str] = None                       # 力度标记: PPP/PP/P/MP/MF/F/FF/FFF
    tuplet_numerator: int = -1                           # 连音分子(-1=无)
    tuplet_denominator: int = -1                         # 连音分母(-1=无)
    brush_type: Optional[str] = None                     # 扫弦方向: Up/Down
    brush_duration: int = 0                              # 扫弦总时长(tick)
    grace_type: Optional[str] = None                     # 装饰音: OnBeat/BeforeBeat
    ottava: Optional[str] = None                         # 八度移调: 8va/15ma/8vb/15mb
    fade: Optional[str] = None                           # 淡入淡出: FadeIn/FadeOut/VolumeSwell
    crescendo: Optional[str] = None                      # 渐强渐弱: Crescendo/Decrescendo
    tremolo_picking: Optional[int] = None                # 震音拨弦: 2/4/8 (1线/2线/3线)
    is_dead_slapped: bool = False                        # Dead Slapped 标记
    lyrics: Optional[List[str]] = None                   # 逐拍歌词列表
    is_slashed: bool = False                             # 斜线谱标记
    wah_pedal: Optional[str] = None                      # 哇音踏板: Open/Closed

    @property
    def is_empty(self) -> bool:
        """判断该拍是否为空（无音符且非休止符）"""
        return len(self.notes) == 0 and not self.is_rest

    @property
    def duration_value(self) -> float:
        """
        获取以四分音符为基准的实际时长
        返回: 浮点数时长，如四分音符=1.0, 附点八分音符=0.75

        连音(Tuplet)处理 (v0.4.0):
          三连音: numerator=3, denominator=2 → 时长 × (2/3)
          五连音: numerator=5, denominator=4 → 时长 × (4/5)
          公式: base × (denominator / numerator)
        """
        from ..utils.constants import DURATION_RATIO, DOTTED_MULTIPLIER
        base = DURATION_RATIO.get(self.duration.value, 1.0)
        if self.is_dotted:
            base *= DOTTED_MULTIPLIER
        # 连音时值修正 (GP7/GP8)
        if self.tuplet_numerator > 0 and self.tuplet_denominator > 0:
            base = base * (self.tuplet_denominator / self.tuplet_numerator)
        return base

    def get_highest_string(self) -> int:
        """获取该拍中最高(最细)弦的索引，无音符返回-1"""
        if not self.notes:
            return -1
        return min(n.string for n in self.notes)

    def get_lowest_string(self) -> int:
        """获取该拍中最低(最粗)弦的索引，无音符返回-1"""
        if not self.notes:
            return -1
        return max(n.string for n in self.notes)
