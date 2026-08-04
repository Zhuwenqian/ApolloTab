# -*- coding: utf-8 -*-
"""
============================================================
ApolloTab - Guitar Pro 文件渲染与播放引擎库
============================================================

功能概述:
  本库提供 Guitar Pro (.gp3/.gp4/.gp5/.gpx/.gp) 文件的完整解析、渲染与播放能力，
  可作为独立库发布到 PyPI，也可集成到 TAB Score Viewer 主程序中。
  v0.4.0 新增 GP7/GP8 (.gp) 原生支持（基于 alphaTab 算法移植）。

核心模块:
  - parser:    GTP文件解析 (GP3-5 用 PyGuitarPro；GP7/GP8 用原生 ZIP+GPIF 解析)
  - models:    数据模型定义 (Note/Beat/Measure/Track/Song)
  - renderer:  六线谱渲染引擎 (QPainter → QPixmap)
  - audio:     音频播放引擎 (MIDI转换 + FluidSynth合成)
  - player:    高级播放器封装 (整合解析/渲染/音频/时间线的完整流程)
  - utils:     常量定义与辅助函数

快速开始:
    # 方式1: 使用高级API（推荐，最简单）
    from ApolloTab import GTPPlayer

    player = GTPPlayer()
    player.load("my_song.gp5")            # 或 .gp (GP7/GP8)
    images = player.render_track(0)       # 渲染六线谱
    player.init_audio()                   # 初始化音频（可选）
    player.play()                         # 开始播放

    # 方式2: 分步操作（更灵活）
    from ApolloTab import parse_score, TabRenderer, SynthEngine, MidiConverter

    song = parse_score("my_song.gp")      # 智能调度: 自动识别 .gp / .gp5
    print(f"标题: {song.title}, 音轨: {song.track_count}, 版本: {song.gp_version}")

    # 方式3: 一键渲染
    from ApolloTab import render_gtp
    pages = render_gtp("my_song.gp5", track_index=0)

依赖库:
  - pyguitarpro >= 0.10.1  # GP3-5 文件解析（开源项目: pyguitarpro）
  - pyfluidsynth >= 1.4.0  # 音频合成（可选，仅音频播放时需要）
  注: PyQt5 已移出 pip 依赖; ARM 架构需 apt 安装后手动创建软链接
  注: GP7/GP8 (.gp) 解析仅依赖 Python 标准库(zipfile/xml/struct)

版本: v1.3.0 (Phase 5 - GP7/GP8 原生支持 + 主题运行时注册)
许可证: GNU Lesser General Public License v2.1 (LGPL-2.1)
创建日期: 2026-06-06
最后更新: 2026-06-30 (v1.3.0: ThemeConfig 新增 register_theme() 运行时主题注册接口;
                   节拍器默认 gain 调整为1.5; 新增通道音量 CC 事件;
                   修复 GP7/GP8 GPIF String 弦号映射方向)
============================================================
"""

from .parser import (
    GTPParser, parse_gtp,                  # GP3-5 解析器
    GP7Parser, GpifParser, parse_gp7,      # GP7/GP8 解析器(v0.4.0新增)
    BinaryStylesheet, PartConfiguration,   # 二进制配置解析器(v0.4.0新增)
    parse_score,                           # 智能调度函数(v0.4.0新增)
    GP3_5_EXTENSIONS, GP7_8_EXTENSIONS, ALL_SUPPORTED_EXTENSIONS,
)
from .models import GTPNote, GTPBeat, GTPMeasure, GTPTrack, GTPSong, Chord
from .renderer import TabRenderer, render_gtp, TabLayoutEngine
from .audio import MidiConverter, MidiEvent, SynthEngine
from .audio import MetronomeConfig, MetronomeGenerator
from .player import GTPPlayer, create_gtp_player, render_gtp_to_images
from .utils import (
    StandardTunings, NoteDuration, TechniqueType,
    RenderConfig, ThemeConfig,  # v0.2.4新增: 渲染主题配置
    RenderMode,                 # v0.4.0新增: 渲染模式枚举
    TECHNIQUE_ABBREVIATION, get_string_name
)

__version__ = "1.3.1"
__all__ = [
    # ===== 高级API（推荐）=====
    'GTPPlayer',              # 高级播放器封装类（整合所有GTP功能）
    'create_gtp_player',      # 工厂函数：快速创建播放器实例
    'render_gtp_to_images',   # 便捷函数：一键渲染为图像列表

    # 解析器（GP3-5）
    'GTPParser', 'parse_gtp',
    # 解析器（GP7/GP8, v0.4.0新增）
    'GP7Parser', 'GpifParser', 'parse_gp7',
    'BinaryStylesheet', 'PartConfiguration',
    # 智能调度函数(v0.4.0新增)
    'parse_score',
    # 扩展名常量(v0.4.0新增)
    'GP3_5_EXTENSIONS', 'GP7_8_EXTENSIONS', 'ALL_SUPPORTED_EXTENSIONS',
    # 数据模型
    'GTPNote', 'GTPBeat', 'GTPMeasure', 'GTPTrack', 'GTPSong',
    # 渲染器
    'TabRenderer', 'render_gtp', 'TabLayoutEngine',
    # 音频播放
    'MidiConverter', 'MidiEvent', 'SynthEngine',
    # 节拍器 (v1.1.3新增)
    'MetronomeConfig', 'MetronomeGenerator',
    # 工具
    'StandardTunings', 'NoteDuration', 'TechniqueType',
    'RenderConfig', 'ThemeConfig',  # v0.2.4新增: 渲染主题配置
    'RenderMode',                   # v0.4.0新增: 渲染模式枚举
    'TECHNIQUE_ABBREVIATION', 'get_string_name',
]
