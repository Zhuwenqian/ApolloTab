"""
ApolloTab.utils.cvd
色觉缺陷 (CVD / Color Vision Deficiency) 模拟模块 (自包含, 不依赖 app core.cvd).

背景:
  全球约 8% 男性 + 0.5% 女性有色觉缺陷, 主要是红绿色盲 (P/D).
  本模块让 TabRenderer 在渲染前对 ThemeConfig 的颜色套 CVD 矩阵变换,
  使设计师/开发者能在自己屏幕上看到 CVD 用户视角的谱面.

技术方案:
  Brettel/Vienot/Mollon (1997) 模型 - 将正常视觉 RGB 通过 3x3 矩阵变换
  模拟 6 种色觉缺陷 (3 protanopia/deuteranopia/tritanopia + 3 对应 anomaly 色弱).
  矩阵数值参考 Machado et al. (2009), 误差 < 5%.

注意:
  矩阵数据与项目 app 层 core/cvd.py 完全一致 (一次写定).
  本模块存在是为了让 ApolloTab 包自包含 (不反向依赖 app core),
  用户合并回 ApolloTab 源码仓库时保持两处同步即可.
"""

from __future__ import annotations

from PyQt5.QtGui import QColor

# ============================================================
# 6 种 CVD 类型的 3x3 模拟矩阵 (Brettel/Vienot/Mollon 1997 + Machado 2009 校准)
# ============================================================
# 矩阵行为: input_rgb_normalized = matrix @ output_rgb_cvd
# 即: 把原始 RGB 当作列向量, 矩阵左乘, 得到 CVD 人眼看到的 RGB
# 数据来源: https://www.inf.ufrgs.br/~oliveira/pubs_files/CVD_Simulation/CVD_Simulation.html
# 矩阵已规整化, 每行之和 = 1.0 (亮度守恒)

CVD_NONE: str = "none"
CVD_PROTANOPIA: str = "protanopia"  # 红色盲 (no L-cones)
CVD_DEUTERANOPIA: str = "deuteranopia"  # 绿色盲 (no M-cones)
CVD_TRITANOPIA: str = "tritanopia"  # 蓝色盲 (no S-cones)
CVD_PROTANOMALY: str = "protanomaly"  # 红色弱 (weak L-cones)
CVD_DEUTERANOMALY: str = "deuteranomaly"  # 绿色弱 (weak M-cones)
CVD_TRITANOMALY: str = "tritanomaly"  # 蓝色弱 (weak S-cones)

# 矩阵字典: key = CVD 类型标识, value = 3x3 tuple of tuples
CVD_MATRICES: dict[str, tuple[tuple[float, ...], ...]] = {
    CVD_PROTANOPIA: (
        (0.567, 0.433, 0.000),
        (0.558, 0.442, 0.000),
        (0.000, 0.242, 0.758),
    ),
    CVD_DEUTERANOPIA: (
        (0.625, 0.375, 0.000),
        (0.700, 0.300, 0.000),
        (0.000, 0.300, 0.700),
    ),
    CVD_TRITANOPIA: (
        (0.950, 0.050, 0.000),
        (0.000, 0.433, 0.567),
        (0.000, 0.475, 0.525),
    ),
    CVD_PROTANOMALY: (
        (0.817, 0.183, 0.000),
        (0.333, 0.667, 0.000),
        (0.000, 0.125, 0.875),
    ),
    CVD_DEUTERANOMALY: (
        (0.800, 0.200, 0.000),
        (0.258, 0.742, 0.000),
        (0.000, 0.142, 0.858),
    ),
    CVD_TRITANOMALY: (
        (0.967, 0.033, 0.000),
        (0.000, 0.733, 0.267),
        (0.000, 0.183, 0.817),
    ),
}


def is_valid_cvd(cvd_type: str) -> bool:
    """检查是否为有效的 CVD 类型标识 (含 'none')."""
    return cvd_type in CVD_MATRICES or cvd_type == CVD_NONE


def apply_cvd_to_color(color: QColor, cvd_type: str) -> QColor:
    """
    对单个 QColor 应用 CVD 模拟, 返回变换后的 QColor.

    算法:
      1. RGB 归一化到 [0, 1]
      2. 矩阵左乘: rgb_cvd = M @ rgb_normal
      3. clip 到 [0, 1] 再 × 255 转回 0-255 整数
      4. 保留原 alpha 通道

    异常:
      - cvd_type='none' 或未知: 直接返回原 color
      - color 无效: 直接返回
    """
    if cvd_type == CVD_NONE or cvd_type not in CVD_MATRICES:
        return color
    if not color.isValid():
        return color

    matrix = CVD_MATRICES[cvd_type]
    r, g, b, a = color.getRgb()

    rn, gn, bn = r / 255.0, g / 255.0, b / 255.0

    out_r = matrix[0][0] * rn + matrix[0][1] * gn + matrix[0][2] * bn
    out_g = matrix[1][0] * rn + matrix[1][1] * gn + matrix[1][2] * bn
    out_b = matrix[2][0] * rn + matrix[2][1] * gn + matrix[2][2] * bn

    out_r = max(0.0, min(1.0, out_r))
    out_g = max(0.0, min(1.0, out_g))
    out_b = max(0.0, min(1.0, out_b))

    return QColor(int(out_r * 255), int(out_g * 255), int(out_b * 255), a)


def apply_cvd_to_hex(hex_color: str, cvd_type: str) -> str:
    """
    对 hex 字符串 (#RRGGBB 或 #AARRGGBB) 应用 CVD 变换, 返回变换后的 hex 字符串.

    异常:
      - cvd_type='none' 或未知: 返回原 hex
      - hex 格式无效: 返回原 hex (不抛异常, 不影响业务)
    """
    if cvd_type == CVD_NONE or cvd_type not in CVD_MATRICES:
        return hex_color
    if not isinstance(hex_color, str) or not hex_color.startswith("#"):
        return hex_color

    qc = QColor(hex_color)
    if not qc.isValid():
        return hex_color
    transformed = apply_cvd_to_color(qc, cvd_type)
    return transformed.name() if transformed.alpha() == 255 else transformed.name(QColor.HexArgb)
