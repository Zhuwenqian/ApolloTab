"""
============================================================
文件名: measure.py
功能描述: 小节(Measure)数据模型 - 存储一个小节的完整内容
         包含拍号、调号、重复记号等信息

创建日期: 2026-06-06
最后更新: 2026-06-28 (v0.4.0: 扩展 GP7/GP8 字段)
依赖: Python 3.11+ dataclasses
============================================================
"""

from dataclasses import dataclass, field
from typing import Optional

from .beat import GTPBeat


@dataclass
class GTPMeasure:
    """
    一个小节(Measure)的数据模型

    属性说明:
      number:          小节序号 (从1开始)
      beats:           该小节内的所有拍列表
      time_signature:  拍号 (分子, 分母), 如 (4, 4) 表示4/4拍
      is_repeat_open:  是否为反复起始记号 (||:)
      repeat_close:    反复结束次数 (-1表示无, 2表示反复2次)
      marker:          小节标记/段落名 (如 "Chorus", "Solo")
      key_signature:   调号 (0=C大调/a小调, 正值=升号数量, 负值=降号数量)

    GP7/GP8 扩展字段 (v0.4.0 新增):
      is_anacrusis:      是否弱起小节（不完全小节，节拍不足）
      alternate_endings:  反复交替结束位标志（bit0=第1结束, bit1=第2结束, ...）
      triplet_feel:      三连音感 (None/Triplet8th/Triplet16th/Dotted8th/Dotted16th/Scottish8th/Scottish16th)
      is_double_bar:     是否双竖线 ||
      directions:        跳转方向标记列表 (DaCapo/DalSegno/Coda/Fine 等)
      section_text:      段落文本（GP7 Section，比 marker 更结构化）
    """

    number: int = 0  # 小节序号
    beats: list[GTPBeat] = field(default_factory=list)  # 拍列表
    time_signature: tuple[int, int] = (4, 4)  # 拍号
    is_repeat_open: bool = False  # 反复起始
    repeat_close: int = -1  # 反复结束次数(-1=无)
    marker: str | None = None  # 段落标记
    key_signature: int = 0  # 调号

    # === GP7/GP8 扩展字段 (v0.4.0) ===
    is_anacrusis: bool = False  # 弱起小节
    alternate_endings: int = 0  # 反复交替结束位标志
    triplet_feel: str | None = None  # 三连音感
    is_double_bar: bool = False  # 双竖线
    directions: list[str] = field(default_factory=list)  # 跳转方向标记
    section_text: str | None = None  # 段落文本(GP7 Section)

    @property
    def total_duration(self) -> float:
        """
        计算该小节总时长（以四分音符为单位）
        返回: 浮点数，如4/4拍=4.0, 3/4拍=3.0
        """
        numerator, denominator = self.time_signature
        return numerator * (4.0 / denominator)

    @property
    def actual_duration(self) -> float:
        """
        计算该小节实际音符总时长（各拍时值之和）
        用于验证小节是否填满
        """
        return sum(beat.duration_value for beat in self.beats)

    def is_full(self) -> bool:
        """检查小节是否已填满（允许微小误差）"""
        return abs(self.actual_duration - self.total_duration) < 0.01
