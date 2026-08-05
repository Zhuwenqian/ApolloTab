"""
============================================================
文件名: song.py
功能描述: 完整歌曲(Song)数据模型 - 顶层容器，包含所有音轨和全局信息
         是解析器输出和渲染器输入的核心中介数据结构

创建日期: 2026-06-06
最后更新: 2026-06-28 (v0.4.0: 扩展 GP7/GP8 字段)
依赖: Python 3.11+ dataclasses
============================================================
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .track import GTPTrack


@dataclass
class GTPSong:
    """
    完整歌曲(Song)的数据模型 - Guitar Pro 文件的顶层表示

    属性说明:
      title:       歌曲标题
      artist:      艺术家/作曲者
      album:       专辑名称
      tempo:       速度(BPM, 每分钟拍数)
      tempo_name:  速度标记名称 (如 "Moderate", "Fast")
      key:         全局调号 (0=C大调/a小调)
      subtitle:    副标题
      copyright:   版权信息
      instructions: 演奏说明
      tracks:      所有音轨列表

    GP7/GP8 扩展字段 (v0.4.0 新增):
      gp_version:    文件版本 ("7.0"/"8.0"，GP3-5 为空字符串)
      words:         作词者
      music:         作曲者
      tabber:        制谱者
      notices:       附加说明
      stylesheet:    BinaryStylesheet 解析结果字典（音轨名显示策略、页眉页脚模板等）
                     None 表示未解析或非 GP7/GP8 文件
      default_systems_layout: 默认每页系统数（GP7 ScoreSystemsDefaultLayout）
    """

    title: str = ""  # 歌曲标题
    artist: str = ""  # 艺术家
    album: str = ""  # 专辑
    tempo: int = 120  # BPM速度
    tempo_name: str = ""  # 速度标记名
    key: int = 0  # 全局调号
    subtitle: str = ""  # 副标题
    copyright: str = ""  # 版权信息
    instructions: str = ""  # 演奏说明
    tracks: list[GTPTrack] = field(default_factory=list)  # 音轨列表

    # === GP7/GP8 扩展字段 (v0.4.0) ===
    gp_version: str = ""  # 文件版本 ("7.0"/"8.0")
    words: str = ""  # 作词者
    music: str = ""  # 作曲者
    tabber: str = ""  # 制谱者
    notices: str = ""  # 附加说明
    stylesheet: dict[str, Any] | None = None  # BinaryStylesheet 解析结果
    default_systems_layout: int = 3  # 默认每页系统数

    @property
    def track_count(self) -> int:
        """音轨总数"""
        return len(self.tracks)

    @property
    def visible_tracks(self) -> list[GTPTrack]:
        """获取所有可见音轨"""
        return [t for t in self.tracks if t.is_visible]

    @property
    def total_measures(self) -> int:
        """总小节数（取第一条轨道的小节数）"""
        if self.tracks:
            return self.tracks[0].total_measures
        return 0

    def get_track_by_name(self, name: str) -> GTPTrack | None:
        """按名称查找音轨"""
        for track in self.tracks:
            if track.name.lower() == name.lower():
                return track
        return None

    def get_primary_guitar_track(self) -> GTPTrack | None:
        """
        获取主要吉他轨道（优先选择可见的非打击乐轨道）
        策略: 第一个可见的电吉他/木吉他轨道
        """
        guitar_instruments = {24, 25, 26, 27, 28, 29, 30}
        for track in self.tracks:
            if track.is_visible and not track.is_mute and track.instrument in guitar_instruments:
                return track
        # 回退: 第一个可见轨道
        for track in self.tracks:
            if track.is_visible:
                return track
        return None
