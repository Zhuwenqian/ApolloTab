"""
gtp_engine.utils - 工具模块
导出: 常量定义、辅助函数、渲染主题配置、渲染模式枚举
"""

from .constants import (
    DOTTED_MULTIPLIER,
    DURATION_RATIO,
    TECHNIQUE_ABBREVIATION,
    NoteDuration,
    RenderConfig,
    RenderMode,  # v0.4.0新增: 渲染模式枚举(GP7/GP8 多谱表预留)
    StandardTunings,
    TechniqueType,
    ThemeConfig,  # v0.2.4新增: 渲染主题配置类
    get_string_name,
)
