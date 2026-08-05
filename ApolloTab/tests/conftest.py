"""
ApolloTab/tests/conftest.py

pytest 共享 fixture (ApolloTab v1.4.1)

功能:
  - 统一定位项目根目录与 guitarpro7/ 测试样本目录，消除历史硬编码的
    macOS 绝对路径 (/Users/limeng/...)，使测试可在 Windows/macOS/Linux 与 CI 上运行
  - 提供 real_gp7_file / real_gp3_file fixture: 按文件名从 guitarpro7/ 取样本，
    缺失则 pytest.skip (CI 上样本未提交时优雅跳过)
  - 统一设置 QT_QPA_PLATFORM=offscreen (无头环境渲染友好)

样本目录: <项目根>/guitarpro7/  (含 chords.gp / notes.gp / bends.gp 等 GP7 测试文件)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ============================================================
# 路径常量
# ============================================================
# conftest.py 位于 <项目根>/ApolloTab/tests/conftest.py
# 项目根 = 三级父目录
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
# guitarpro7 测试样本目录 (项目根/guitarpro7)
GUITARPRO7_DIR: Path = PROJECT_ROOT / "guitarpro7"

# 项目根加入 sys.path (兼容旧测试中 from ApolloTab import ... 的导入方式)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 强制 Qt 使用 offscreen 平台插件 (CI / 无头环境友好)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ============================================================
# Fixtures: 测试样本文件
# ============================================================


@pytest.fixture(scope="session")
def guitarpro7_dir() -> Path:
    """guitarpro7 样本目录路径 (不校验存在性，由具体 fixture 决定是否 skip)"""
    return GUITARPRO7_DIR


def _resolve_sample(filename: str) -> Path:
    """从 guitarpro7/ 解析样本文件路径，缺失则抛 FileNotFoundError"""
    return GUITARPRO7_DIR / filename


@pytest.fixture()
def real_gp7_chords_file() -> Path:
    """真实 GP7 和弦样本: guitarpro7/chords.gp

    用于 chord 解析与渲染端到端测试; 文件未提交时 skip。
    """
    p = _resolve_sample("chords.gp")
    if not p.exists():
        pytest.skip(f"chords.gp 样本未找到: {p}")
    return p


@pytest.fixture()
def real_gp3_chords_file() -> Path:
    """GP3 和弦样本: guitarpro7/ 下任意 .gp3 文件

    guitarpro7/ 目录当前仅含 GP7(.gp) 样本，无 .gp3;
    若后续放入 .gp3 文件则自动启用，否则 skip。
    """
    if not GUITARPRO7_DIR.exists():
        pytest.skip(f"guitarpro7 目录不存在: {GUITARPRO7_DIR}")
    gp3_files = sorted(GUITARPRO7_DIR.glob("*.gp3"))
    if not gp3_files:
        pytest.skip("guitarpro7 目录下无 .gp3 样本文件")
    return gp3_files[0]


@pytest.fixture()
def make_gp7_sample():
    """按文件名从 guitarpro7/ 取 GP7 样本的工厂 fixture

    用法:
        def test_x(self, make_gp7_sample):
            path = make_gp7_sample("notes.gp")
    """

    def _factory(filename: str) -> Path:
        p = _resolve_sample(filename)
        if not p.exists():
            pytest.skip(f"GP7 样本未找到: {p}")
        return p

    return _factory
