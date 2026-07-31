# -*- coding: utf-8 -*-
"""
============================================================
文件名: chord.py
功能描述: 和弦(Chord)数据模型 - 存储 Guitar Pro 文件中的和弦图信息
         包含根音/低音/三度/五度/七度/扩展音等

和弦结构 (GPIF 格式):
    <Chord>          ← 和弦定义
        <KeyNote step="C" accidental="Natural" />
        <BassNote step="C" accidental="Natural" />
        <Degree interval="Third" alteration="Major" omitted="false" />
        <Degree interval="Fifth" alteration="Perfect" omitted="false" />
    </Chord>

和弦名生成 (基于 Degree 列表):
    - KeyNote + 后缀
    - 3rd Minor → "m"
    - 5th Diminished + 7th Minor → "m7b5"
    - 5th Diminished → "dim"
    - 5th Augmented → "aug"
    - 4th Perfect → "sus4"
    - 6th Major → "6"
    - 7th Major → "maj7", 7th Minor → "7"
    - 9th Major → "9"
    - 11th Perfect → "11"
    - 13th Major → "13"
    - BassNote 不同时 → "/BassName"

创建日期: 2026-07-25 (v1.4.0: 新增 Chord 数据模型)
依赖: Python 3.11+ dataclasses
============================================================
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Chord:
    """
    和弦(Chord)的数据模型 - 对应 Guitar Pro 中一个和弦图定义

    属性说明:
      key:        根音(KeyNote) 可读名 (e.g. "C", "F#", "Bb"), 永不为空
      bass:       低音(BassNote) 可读名 (e.g. "E" for G/B), None=无独立低音
      suffix:     基础后缀 ("m" / "dim" / "aug" / "sus4" / "m7b5"), 空字符串=大三和弦
      extensions: 扩展音后缀 ("7" / "maj7" / "9" / "13" / "b9" / "11"), 空字符串=无
      name:       完整和弦名 (key + suffix + extensions, 可选 "/bass"),
                  由 __post_init__ 自动计算, 调用方通常只读此字段

    使用示例:
        chord = Chord(key="C", bass=None, suffix="", extensions="")
        print(chord.name)  # "C"

        chord = Chord(key="G", bass="B", suffix="", extensions="")
        print(chord.name)  # "G/B"
    """
    key: str
    bass: Optional[str] = None
    suffix: str = ""
    extensions: str = ""

    def __post_init__(self) -> None:
        """自动计算完整和弦名 name"""
        self.name = self.key + self.suffix + self.extensions
        if self.bass and self.bass != self.key:
            self.name = f'{self.name}/{self.bass}'

    def __str__(self) -> str:
        return self.name
