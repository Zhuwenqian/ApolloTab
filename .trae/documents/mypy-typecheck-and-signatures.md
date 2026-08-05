# 实施计划：补齐函数签名 + 增加 mypy 类型检查

## Context（背景）

ApolloTab v1.4.1 已配 ruff + pytest + CI，但 mypy 虽在 dev 依赖、`pyproject.toml` 有最小 `[tool.mypy]` 配置，却**从未真正运行**（venv 未装 mypy，CI 无 mypy step）。`py.typed` 已存在（PEP 561），但大量函数缺类型注解，类型契约形同虚设。

摸底发现：
- mypy 2.3.0 + PyQt5-stubs 5.15.6.0 现已装入 venv。
- **mypy 目录模式 `mypy ApolloTab` 不全量检查子模块**（mypy 设计：仅命令行显式 source 全量检查，被导入模块只报"影响 source"的错误）。实测 `mypy ApolloTab/__init__.py` 作为入口可全量覆盖核心库（__init__ 导入所有子模块）。
- 真实错误 **217 个**，分布在 13 个核心文件；models/（dataclass）类型完备无错。
- 错误码分布：`no-untyped-def` 42、`annotation-unchecked` 29（修 import 后会显现新错）、`assignment` 28、`union-attr` 25、`no-any-return` 16、`attr-defined` 7、`var-annotated` 5、PyQt5 相关 2、`import-untyped` 2。
- `pyguitarpro`(import 名 `guitarpro`) / `pyfluidsynth`(import 名 `fluidsynth`) 均无 `py.typed`；venv 的 numpy 新版 stub 用 Py3.12 `type` 语句，在 3.11 target 报 syntax 错。

目标：补齐核心库 + examples 的函数签名（全粒度含私有），配齐 mypy 配置，CI 加 mypy job 失败即红，一次性把核心库 mypy 错误修到 0。版本 1.4.0 → 1.5.0。

用户决策（已确认）：Q1 a / Q2 b / Q3 a / Q4 a / Q5 我定=per-module ignore_missing_imports / Q6 a / Q7 a / Q8 a / Q9 a / Q10 a / Q11 允许装依赖。

---

## 步骤 1：补全 `pyproject.toml` 的 `[tool.mypy]` 配置

文件：[pyproject.toml](file:///e:/Projects/Python%20Workspace/ApolloTab/pyproject.toml)（142-147 行段）

替换为：
```toml
[tool.mypy]
python_version = "3.11"
# 入口用 __init__.py：mypy 目录模式不全量检查子模块，
# 以 __init__.py 为 source 可经 follow_imports 全量覆盖核心库导入链
files = ["ApolloTab/__init__.py"]
exclude = ['ApolloTab/tests/', 'ApolloTab/examples/', 'venv/', 'dist/', 'build/']
show_error_codes = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
no_implicit_optional = true   # mypy 2.x 默认已开，显式声明

# 无 py.typed 的第三方依赖：忽略缺失 stub
[[tool.mypy.overrides]]
module = ["guitarpro", "guitarpro.*", "fluidsynth", "numpy", "numpy.*"]
ignore_missing_imports = true
```
- `dev` 依赖组新增 `"PyQt5-stubs>=5.15.6.0"`（Q6 a）。mypy 已在 dev，无需重复。
- 注：`numpy` 用 `ignore_missing_imports` 抑制其自带 stub 在 3.11 target 的 syntax 错。

## 步骤 2：CI 加 mypy job

文件：[.github/workflows/ci.yml](file:///e:/Projects/Python%20Workspace/ApolloTab/.github/workflows/ci.yml)

在 `lint` job 后新增独立 `typecheck` job（Q7 a：ubuntu + Py3.12）：
- `pip install -e ".[dev]"` → 装 mypy（dev 组）
- `pip install PyQt5 PyQt5-stubs` → Qt stub
- `run: mypy`（读 pyproject `files` 配置）
- `QT_QPA_PLATFORM=offscreen` 不需要（mypy 不运行 Qt，仅静态分析）

## 步骤 3：修复 217 个 mypy 错误（按文件从小到大）

修复模式（标准化）：
- `no-untyped-def` / `var-annotated`：补全参数与返回类型注解（Q8 a 全粒度含私有）。返回类型拿不准时用 `-> None` / 实际类型；`self`/`cls` 不需注解。
- `assignment`（implicit Optional）：`def f(x: T = None)` → `def f(x: T | None = None)`。
- `union-attr` / `attr-defined`（None 上访问）：加 `assert obj is not None` 或显式 `if obj is not None:` 分支收窄类型。**禁止用 `if hasattr` 掩盖**（用户规则）。
- `no-any-return`：property 返回 `self._theme.X`（Any）→ `return cast(str, self._theme.X)` 或修 ThemeConfig 属性为 `str`。
- `import-untyped`：由步骤 1 per-module ignore 覆盖。
- PyQt5 `QPoint`/`call-overload`：按 PyQt5-stubs 签名调整调用。

执行顺序（从小到大，便于迭代验证）：
1. `audio/metronome.py` (1) / `parser/part_configuration.py` (1) / `parser/__init__.py` (2)
2. `parser/gp7_parser.py` (3) / `parser/binary_stylesheet.py` (3) / `renderer/layout_engine.py` (4) / `parser/gtp_parser.py` (5)
3. `utils/constants.py` (16) / `audio/synth_engine.py` (16) / `audio/midi_converter.py` (21)
4. `player.py` (35) / `renderer/tab_renderer.py` (46) / `parser/gpif_parser.py` (64)

每修一批跑 `mypy ApolloTab/__init__.py --show-error-codes` 验证。注意：`annotation-unchecked` 29 个在 import 配置生效后会转为实检，可能新增错误，需迭代到 0。

## 步骤 4：examples 补签名（Q2 b，本地验证）

文件：`ApolloTab/examples/audio_playback.py` / `basic_parse.py` / `render_tab.py`

补全函数签名。examples 不纳入 CI mypy（Q9 a），本地用 `mypy ApolloTab/examples/<file>.py` 逐个验证 0 错（配 ignore_missing_imports 生效）。

## 步骤 5：版本 + 功能更新.md

- `ApolloTab/__init__.py`：`__version__ = "1.4.0"` → `"1.5.0"`；`pyproject.toml` `version = "1.4.0"` → `"1.5.0"`。
- [readme/功能更新.md](file:///e:/Projects/Python%20Workspace/ApolloTab/readme/功能更新.md)：顶部新增 `## v1.5.0 (2026-08-05)` 段，记录 mypy 配置补全、CI mypy job、PyQt5-stubs 入 dev、217 错修复、examples 补签名。控制文件 ≤250 行（当前 ~130 行，新增 ~40 行，OK）。

---

## 关键文件

| 文件 | 改动 |
|------|------|
| [pyproject.toml](file:///e:/Projects/Python%20Workspace/ApolloTab/pyproject.toml) | `[tool.mypy]` 补全 + overrides；dev 加 PyQt5-stubs；版本 1.5.0 |
| [.github/workflows/ci.yml](file:///e:/Projects/Python%20Workspace/ApolloTab/.github/workflows/ci.yml) | 新增 typecheck job |
| `ApolloTab/parser/gpif_parser.py` 等 13 文件 | 修复类型错误 |
| `ApolloTab/examples/*.py` (3 文件) | 补签名 |
| `ApolloTab/__init__.py` | 版本号 |
| `readme/功能更新.md` | v1.5.0 段 |

## 验证

1. **mypy 0 错**（核心库）：`.\venv\Scripts\python.exe -m mypy ApolloTab/__init__.py --show-error-codes` → `Success: no issues found`。
2. **examples 0 错**：逐个 `mypy ApolloTab/examples/<f>.py`。
3. **ruff 不回归**：`ruff check ApolloTab && ruff format --check ApolloTab`。
4. **pytest 不回归**：`pytest`（40% 覆盖率门槛）。
5. **CI 配置自检**：ci.yml 语法 + mypy job 步骤完整。

## 风险与备注

- `annotation-unchecked` 转 checked 后可能新增错误，步骤 3 需迭代。
- PyQt5-stubs 与实际 PyQt5 行为偶有出入，少数 `# type: ignore[xxx]` 带错误码可接受（不滥用）。
- mypy 目录模式行为已摸清，CI 用 `files = ["ApolloTab/__init__.py"]` 稳健覆盖核心库；未来新增子模块需在 `__init__.py` 导出，否则不被检查（已在配置注释说明）。
