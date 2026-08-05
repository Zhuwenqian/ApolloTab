# ApolloTab Release Notes

## v1.5.0

This release introduces **static type checking with mypy** across the entire
core library, hardens function signatures throughout the codebase, and wires a
dedicated **typecheck job** into CI.

---

## Type Checking

### mypy Configuration and CI
- A complete `[tool.mypy]` section is added to `pyproject.toml` with
  `python_version = "3.11"`, `disallow_untyped_defs`, `no_implicit_optional`,
  `warn_return_any`, and `warn_unused_configs`. The check entry point is
  `ApolloTab/__init__.py`, which through `follow_imports` exercises the full
  core import graph.
- Third-party libraries without `py.typed` stubs (`guitarpro`, `fluidsynth`)
  are silenced via per-module `ignore_missing_imports`. Unused override
  entries (`guitarpro.*`, `numpy`, `numpy.*`) were pruned to keep
  `warn_unused_configs` clean.
- A new `typecheck` job in `.github/workflows/ci.yml` runs `mypy` on
  `ubuntu-latest` with Python 3.12 and fails the build on any type error.
- `PyQt5-stubs>=5.15.6.0` is added to the `dev` dependency group so Qt-related
  functions receive full type checking.

### Signature and Type Fixes
- **`player.py`**: 14 parameters changed from implicit-`None` defaults to
  explicit `T | None = None`; `measure_entries` / `num_to_global` / `groups`
  annotated as `dict[...]`; five dictionary-value returns wrapped in `float()`
  to satisfy `no-any-return`; the `if note_callback:` guard switched to
  `is not None` to fix `truthy-function`; `__del__` / `set_theme` /
  `update_playhead` and the module-level `render_gtp_to_images` gained return
  annotations.
- **`tab_renderer.py`**: eleven drawing methods (`_draw_note_fret`,
  `_draw_technique_marks`, `_draw_slide_line`, `_draw_hammer_on_arc`,
  `_draw_bend_indicator`, `_draw_technique_text_labels`, `_draw_stem`,
  `_draw_beam_flags`, `_draw_rest_symbol`, `_draw_info_bar`,
  `_draw_time_signature`) now declare `note` / `beat` / `measure` parameters as
  `GTPNote` / `GTPBeat` / `GTPMeasure`; `m_layout` widened to
  `MeasureLayout | None`; `drawText` flags wrapped with `int()` and
  `drawPolygon` wrapped with `QPolygon(...)` to match PyQt5-stubs overloads;
  `tech_beats` annotated; `chord` access guarded against `None`.
- **`gpif_parser.py`**: `_GpifRhythm`, `_GpifSound`, and `GP7Parser.__init__`
  annotated `-> None`; the `self.__init__()` reset call was refactored into a
  dedicated `_reset_state()` method to eliminate the
  `instance.__init__` unsoundness warning; `_parse_dom`, `_parse_score_node`,
  and `_build_model` open with `assert self._song is not None` to narrow the
  `GTPSong | None` type; `mb_data` / `bar_data` annotated as `dict[str, Any]`.

### Data Model Adjustments
- **`GTPNote.vibrato`** (`models/note.py`): new `VibratoType | None` field.
  GP7/GP8 `<Vibrato>` Slight/Wide markers now land as the enum, ready for
  sine-wave vibrato synthesis in the MIDI path.
- **`BendData.bend_type`** type changed from `str` to `BendType | str` so the
  GP3-5 path (which passes a string) and the GP7/GP8 path (which passes a
  `BendType` enum) are both type-correct.

### Dynamic Attribute Handling
- The render-time `_parent_beat` reference is now assigned via `setattr` and
  read via `getattr(note, '_parent_beat', None)` in `_draw_slide_line` and
  `_draw_hammer_on_arc`. This avoids the mypy `attr-defined` error without
  polluting the `GTPNote` dataclass with a render-only field, while a
  targeted `# noqa: B010` keeps ruff happy.

---

## Examples

The three example scripts gained return-type annotations:
- `examples/basic_parse.py` — `main() -> None`, `format_tuning(tuple[int, ...]) -> str`
- `examples/render_tab.py` — `main() -> None`, `batch_render(...) -> None`
- `examples/audio_playback.py` — `main() -> None`, `show_progress(...) -> None`

---

## Maintenance

- Version bumped from `1.4.0` to `1.5.0` in `pyproject.toml` and
  `ApolloTab/__init__.py`.
- `readme/功能更新.md` records the v1.5.0 changelog.

---

## Verification

- `mypy` — Success: no issues found (1 source file).
- `ruff check` / `ruff format --check` — all green.
- `pytest` — 305 passed, 6 skipped (coverage 57.11%, above the 40% gate).
  The 6 skips are pre-existing platform limitations (Qt offscreen on
  macOS/Windows, missing `.gp3` samples), not regressions.

---

## Dependencies

No runtime dependency changes. `PyQt5-stubs` is a dev-only addition.
