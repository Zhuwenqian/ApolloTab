"""
============================================================
文件名: lyrics.py
功能描述: 歌词(Lyrics)数据模型 - 移植自 alphaTab 的 Lyrics.ts

         表示一条歌词行。Guitar Pro 格式的原始歌词文本经
         finish() 状态机解析为 chunks（音节片段），再由
         GTPTrack.apply_lyrics() 按拍分配到各 GTPBeat。

         GP 歌词文本规则（与 alphaTab 完全一致）:
           - 空格 / 换行 / 制表符 → 分隔音节（chunk 边界）
           - '+'                  → 合并音节（解析后变为空格）
           - '[..]'               → 注释（仅在 chunk 起始处生效，忽略）
           - '-'                  → 连字符（音节带尾 dash，作为独立 chunk）
           - 末尾 '_'             → 裁剪（如 "You____" → "You"）

创建日期: 2026-08-06
依赖: Python 3.11+
============================================================
"""

from enum import IntEnum


class _LyricsState(IntEnum):
    """歌词解析状态机状态（移植 alphaTab LyricsState）"""

    IGNORE_SPACES = 0
    BEGIN = 1
    TEXT = 2
    COMMENT = 3
    DASH = 4


class Lyrics:
    """
    单条歌词行 - 对应 alphaTab 的 Lyrics 类

    属性:
      start_bar: 歌词开始的起始小节索引（0-based）
      text:      Guitar Pro 原始歌词文本
      chunks:    finish() 解析后的音节片段列表，按顺序分配到各拍
    """

    # 解析用字符常量（与 alphaTab _charCode* 对应）
    _LF = "\n"
    _CR = "\r"
    _TAB = "\t"
    _SPACE = " "
    _BRACKET_OPEN = "["
    _BRACKET_CLOSE = "]"
    _DASH = "-"

    def __init__(self, start_bar: int = 0, text: str = "") -> None:
        self.start_bar: int = start_bar
        self.text: str = text
        self.chunks: list[str] = []

    def finish(self, skip_empty: bool = False) -> None:
        """
        解析 text 为 chunks

        参数:
          skip_empty: True 时跳过空片段和单独的 '-' 片段
                      （用于把拍上自由文本当作歌词的场景）
        """
        self.chunks = []
        self._parse(self.text, 0, skip_empty)

    def _parse(self, s: str, p: int, skip_empty: bool) -> None:
        """歌词文本状态机解析（逐字符，移植 alphaTab _parse）"""
        if not s:
            return

        state = _LyricsState.BEGIN
        next_state = _LyricsState.BEGIN
        skip_space = False
        start = 0

        while p < len(s):
            c = s[p]
            if state == _LyricsState.IGNORE_SPACES:
                if c in (self._LF, self._CR, self._TAB):
                    pass
                elif c == self._SPACE:
                    if not skip_space:
                        state = next_state
                        continue  # 重新处理当前字符（不前进 p）
                else:
                    skip_space = False
                    state = next_state
                    continue
            elif state == _LyricsState.BEGIN:
                if c == self._BRACKET_OPEN:
                    state = _LyricsState.COMMENT
                else:
                    start = p
                    state = _LyricsState.TEXT
                    continue
            elif state == _LyricsState.COMMENT:
                if c == self._BRACKET_CLOSE:
                    state = _LyricsState.BEGIN
            elif state == _LyricsState.TEXT:
                if c == self._DASH:
                    state = _LyricsState.DASH
                elif c in (self._CR, self._LF, self._SPACE):
                    self._add_chunk(s[start:p], skip_empty)
                    state = _LyricsState.IGNORE_SPACES
                    next_state = _LyricsState.BEGIN
            elif state == _LyricsState.DASH and c != self._DASH:
                self._add_chunk(s[start:p], skip_empty)
                skip_space = True
                state = _LyricsState.IGNORE_SPACES
                next_state = _LyricsState.BEGIN
                continue
            p += 1

        # 文本结束时若仍处于 TEXT 态，收尾最后一个 chunk
        if state == _LyricsState.TEXT and p != start:
            self._add_chunk(s[start:p], skip_empty)

    def _add_chunk(self, txt: str, skip_empty: bool) -> None:
        txt = self._prepare_chunk(txt)
        if not skip_empty or (len(txt) > 0 and txt != "-"):
            self.chunks.append(txt)

    @staticmethod
    def _prepare_chunk(txt: str) -> str:
        # '+' 合并音节 → 空格
        chunk = txt.replace("+", " ")
        # 裁剪末尾 '_'（如 "You____" → "You"）
        end = len(chunk)
        while end > 0 and chunk[end - 1] == "_":
            end -= 1
        return chunk[:end] if end != len(chunk) else chunk

    def __repr__(self) -> str:
        return f"Lyrics(start_bar={self.start_bar}, chunks={self.chunks!r})"
