"""
============================================================
文件名: note.py
功能描述: 音符(Note)数据模型 - 存储单个音符的完整信息
         包括音高(MIDI)、所在弦、品格数、时值、力度、技巧等

创建日期: 2026-06-06
最后更新: 2026-06-30 (v1.3.0: 修正 string 字段注释, 明确 GP7/GP8 GPIF 弦号需反转映射)
依赖: Python 3.11+ dataclasses
设计原则:
  - 可扩展: 通过 techniques 列表支持任意数量技巧标记
  - 向后兼容: GP7/GP8 新增字段均带默认值，GP3-5 解析路径不受影响
  - 双源融合: 同一字段可被 GP3-5(PyGuitarPro) 和 GP7/GP8(原生解析) 填充
============================================================
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from ..utils.constants import BendStyle, NoteDuration, TechniqueType


@dataclass
class BendData:
    """
    推弦(Bend)详细数据 - 存储从GTP文件解析的完整推弦信息

    属性说明:
      bend_type:    推弦类型 ('bend'=普通推弦, 'bendRelease'=推弦+释放)
      value:        推弦量(四分之一音为单位): 25=1/4, 50=1/2, 75=3/4, 100=Full
      max_value:    推弦峰值(四分之一音为单位)
      points:       曲线点列表 [(position, value), ...]
                    position: 时间位置(0-12, 相对该拍的比例)
                    value: 音高偏移(四分之一音)
      has_release:  是否有释放段(终点value < 峰值)

    调用来源: guitarpro库的BendEffect对象 (开源项目 guitarpro)
    """

    bend_type: str = "bend"  # 推弦类型
    bend_style: BendStyle = BendStyle.DEFAULT  # 推弦风格(影响渲染曲线弧度)
    value: int = 0  # 推弦量
    max_value: int = 0  # 峰值
    points: list = field(default_factory=list)  # [(pos, val, vibrato), ...]

    @property
    def has_release(self) -> bool:
        """是否有释放段（终点value低于峰值）"""
        if not self.points:
            return False
        last_val = (
            self.points[-1][1]
            if isinstance(self.points[-1], tuple)
            else getattr(self.points[-1], 'value', 0)
        )
        return last_val < self.max_value and self.max_value > 0

    def get_display_text(self) -> str:
        """
        获取推弦度数显示文字

        返回值映射:
          25  → "1/4"
          50  → "1/2"
          75  → "3/4"
          100 → "Full"
          其他 → "Full"(默认)
        """
        mapping = {25: "1/4", 50: "1/2", 75: "3/4", 100: "Full"}
        return mapping.get(self.max_value, "Full")


@dataclass
class GTPNote:
    """
    单个音符的数据模型

    属性说明:
      midi_pitch:    MIDI音高值 (0-127), 40=E2(6弦空弦), 64=E4(1弦空弦)
      string:        弦号 (0-5), 0=1弦(最细/高音E/顶线), 5=6弦(最粗/低音E/底线)
                     注意:
                       - PyGuitarPro原始数据为1-based(1-6)，解析时已转为0-based
                       - GP7/GP8 GPIF 原始 <String> 是 0-based 但 0=最低音弦(底线),
                         gpif_parser 已按 track.strings 长度反转为 ApolloTab 约定
                       - GP7/GP8: 打击乐轨道此字段为 -1（alphaTab 兼容）
      fret:          品格数 (0-30), 0=空弦
                     GP7/GP8: 打击乐轨道此字段为 -1，普通轨道正常
      velocity:      力度/击弦强度 (0-127), 影响播放音量
      duration:      时值类型 (NoteDuration枚举)
      is_dotted:     是否附点音符 (附点时值 = 原时值 × 1.5)
      techniques:    演奏技巧列表 (可扩展, 支持叠加多个技巧)
      bend:          推弦详细数据 (BendData对象, 含类型/度数/曲线点)
      is_ghost:      是否幽灵音(建议弹奏但不强调)
      is_rest:       是否休止符

    GP7/GP8 扩展字段 (v0.4.0 新增，默认值保证 GP3-5 路径不受影响):
      is_tie_destination:  是否为连音(延音)目标音符（前音符时值延长到此音符）
      is_hammer_pull_origin: 是否为击弦/勾弦的起始音符
      is_palm_mute:        是否 palm mute 闷音（GP7 显式标记，与 techniques 列表冗余但便于查询）
      is_dead:             是否 dead note (闷音弹奏，无明确音高)
      is_let_ring:         是否 let ring 延音（GP7 显式标记）
      is_staccato:         是否断奏（GP7 显式标记）
      is_tenuto:           是否保持音（GP7 显式标记）
      accentuated_type:    重音类型 (0=无, 1=Normal, 2=Heavy)（GP7 Accent 位标志）
      harmonic_type:       泛音类型 (None/Natural/Artificial/Pinch/Tap/Semi/Feedback)
      harmonic_value:      泛音品位（浮点数，自然泛音的精确品位）
      trill_value:         颤音音程（ MIDI 偏移，如 1=半音, 2=全音）
      trill_speed:         颤音速度（NoteDuration 枚举，默认 Sixteenth）
      slide_in_type:       滑入类型 (None/IntoFromBelow/IntoFromAbove)
      slide_out_type:      滑出类型 (None/Shift/Legato/OutDown/OutUp/PickSlideDown/PickSlideUp)
      left_hand_finger:    左手指法 (0=未知, 1=拇指P, 2=食指I, 3=中指M, 4=无名指A, 5=小指C)
      right_hand_finger:   右手指法 (同上编号)
      percussion_articulation: 打击乐编号（GP7 drums，非打击乐为 -1）
      is_percussion:       是否打击乐音符（用于 midi_converter 鼓轨识别）
    """

    midi_pitch: int = 0  # MIDI音高值
    string: int = 0  # 弦号 (0-5)
    fret: int = 0  # 品格数
    velocity: int = 95  # 力度 (0-127, 默认95=mf中强)
    duration: NoteDuration = NoteDuration.QUARTER  # 时值
    is_dotted: bool = False  # 是否附点
    techniques: list[TechniqueType] = field(default_factory=list)  # 技巧列表
    bend: BendData | None = None  # 推弦详细数据(仅推弦音符有值)
    is_ghost: bool = False  # 幽灵音标记
    is_rest: bool = False  # 休止符标记

    # === GP7/GP8 扩展字段 (v0.4.0) ===
    is_tie_destination: bool = False  # 连音目标音符
    is_hammer_pull_origin: bool = False  # 击弦/勾弦起始
    is_palm_mute: bool = False  # 闷音 (GP7显式标记)
    is_dead: bool = False  # Dead note 闷音弹奏
    is_let_ring: bool = False  # Let Ring 延音 (GP7显式标记)
    is_staccato: bool = False  # 断奏 (GP7显式标记)
    is_tenuto: bool = False  # 保持音
    accentuated_type: int = 0  # 重音类型: 0=无, 1=Normal, 2=Heavy
    harmonic_type: str | None = None  # 泛音类型: None/Natural/Artificial/Pinch/Tap/Semi/Feedback
    harmonic_value: float = 0.0  # 泛音品位
    trill_value: int = 0  # 颤音音程 (MIDI偏移)
    trill_speed: NoteDuration = NoteDuration.SIXTEENTH  # 颤音速度
    slide_in_type: str | None = None  # 滑入类型: IntoFromBelow/IntoFromAbove
    slide_out_type: str | None = (
        None  # 滑出类型: Shift/Legato/OutDown/OutUp/PickSlideDown/PickSlideUp
    )
    left_hand_finger: int = 0  # 左手指法: 0=未知/1=P拇指/2=I食指/3=M中指/4=A无名指/5=C小指
    right_hand_finger: int = 0  # 右手指法: 同上
    percussion_articulation: int = -1  # 打击乐编号 (非打击乐为-1)
    is_percussion: bool = False  # 是否打击乐音符

    def get_display_fret(self) -> str:
        """
        获取用于显示的品格文本
        返回: 品格数字字符串，幽灵音用括号包裹
        """
        if self.is_ghost:
            return f"({self.fret})"
        return str(self.fret)

    def has_technique(self, tech_type: TechniqueType) -> bool:
        """检查是否包含指定技巧"""
        return tech_type in self.techniques

    def add_technique(self, tech_type: TechniqueType) -> None:
        """添加技巧（自动去重）"""
        if tech_type not in self.techniques:
            self.techniques.append(tech_type)
