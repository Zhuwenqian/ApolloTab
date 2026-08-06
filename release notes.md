# ApolloTab Release Notes

## v1.6.0

This release adds two user-facing features — **lyrics (歌词) parsing &
rendering** and **CVD (color vision deficiency) simulation** — both ported
from the alphaTab reference implementation. It is a backward-compatible
minor-version bump.

---

## Lyrics

A complete lyrics pipeline, from raw Guitar Pro text through to rendered
output below the tab staff.

### Data Model — `models/lyrics.py` (new)
- New `Lyrics` class (ported from alphaTab `Lyrics.ts`) representing a single
  lyrics line: `start_bar`, `text`, and the parsed `chunks`.
- `Lyrics.finish()` runs a character-level state machine (`_LyricsState`:
  `IGNORE_SPACES` / `BEGIN` / `TEXT` / `COMMENT` / `DASH`) that splits raw GP
  lyrics text into syllable chunks with identical semantics to alphaTab:
  - spaces / newlines / tabs → syllable boundaries
  - `+` → merge syllables (becomes space)
  - `[..]` → comment (only at chunk start)
  - `-` → hyphenation (kept trailing on the syllable)
  - trailing `_` → trimmed (`"You____"` → `"You"`)
- `Lyrics` is exported from the top-level package (`from ApolloTab import Lyrics`).

### Track Assignment — `models/track.py`
- New `GTPTrack.apply_lyrics(lyrics_list)` distributes chunked lyrics onto
  beats, mirroring alphaTab `Track.applyLyrics`:
  - each line parsed via `finish()`, then chunks assigned in order to
    **note-bearing beats** (rest beats and empty beats are skipped)
  - `start_bar` offsets the starting measure; assignment runs across the
    flattened beat sequence (cross-measure)
  - `beat.lyrics` is a `list[str]` whose length equals the number of lines;
    the `li`-th line's chunk is written to `beat.lyrics[li]`
  - gracefully stops when chunks run out or beats are exhausted (no crash)

### Parsing
- **GP7/GP8** (`parser/gpif_parser.py`): both lyrics encodings are handled
  - track-level `<Lyrics><Line><Offset>..</Offset><Text>..</Text></Line>`
    collected into `_lyrics_by_track` and applied after `_build_model`
  - beat-level `<Lyrics><Line>..</Line>` written directly to `beat.lyrics`;
    when present, `_skip_apply_lyrics` short-circuits the track-level apply
    (matching alphaTab precedence)
- **GP3-5** (`parser/gtp_parser.py`): new `_apply_song_lyrics()` maps the
  pyguitarpro song-level `Lyrics` object onto the target track via
  `trackChoice` (1-based → 0-based), taking non-empty lines and converting
  `startingMeasure` (1-based) to `start_bar` (0-based). Out-of-range
  `trackChoice` is skipped safely.

### Layout — `renderer/layout_engine.py`
- `SystemLayout` gains `y_lyrics_top` and `lyrics_line_count`.
- After measures are laid out, the engine scans the system's beats for the
  max `len(beat.lyrics)` and, when lyrics exist, reserves a lyrics band
  below the stem area (`LYRICS_TOP_PADDING` + `lyrics_line_count *
  LYRICS_LINE_PITCH`), expanding `y_bottom` so the next system doesn't
  overlap. Systems without lyrics are unaffected.

### Rendering — `renderer/tab_renderer.py`
- New `_draw_lyrics(painter, system)` draws the lyrics band (ported from
  alphaTab `LyricsGlyph` / lyrics effect band):
  - centered on each beat's `x_center`, multi-line stacked by
    `LYRICS_LINE_PITCH`, baseline computed from `y_lyrics_top`
  - achromatic text using `COLOR_TEXT` (lyrics do **not** participate in CVD)
  - no-op when `lyrics_line_count == 0` / `y_lyrics_top == 0`
- New constants in `RenderConfig`: `LYRICS_FONT_FAMILY` (`Arial`),
  `LYRICS_FONT_SIZE` (10), `LYRICS_LINE_PITCH` (16), `LYRICS_TOP_PADDING` (6).

---

## CVD (Color Vision Deficiency) Simulation

An accessibility (a11y) feature that lets designers/developers preview how
the rendered tab appears to users with color vision deficiencies (~8% of
men, ~0.5% of women).

### Module — `utils/cvd.py` (new, self-contained)
- Brettel/Viénot/Mollon (1997) + Machado et al. (2009) 3×3 simulation
  matrices for six CVD types: `protanopia` / `deuteranopia` / `tritanopia`
  (dichromacy) and `protanomaly` / `deuteranomaly` / `tritanomaly`
  (anomalous trichromacy). Matrices are row-normalized (luminance preserved).
- `apply_cvd_to_color(QColor, cvd_type)` / `apply_cvd_to_hex(hex, cvd_type)`:
  normalize RGB to [0,1], left-multiply by the matrix, clip, and restore
  alpha. `'none'` / unknown types return the input unchanged.
- `is_valid_cvd(cvd_type)` validator.
- Self-contained (depends only on PyQt5 `QColor`) so the ApolloTab package
  does not reverse-depend on the app layer's `core/cvd.py`; the two copies
  are kept in sync manually.

### Renderer Integration — `renderer/tab_renderer.py`
- `TabRenderer.set_cvd(cvd_type)` applies the CVD transform to the current
  theme's `COLOR_*` entries and stores the result in `cfg.theme` (and syncs
  the layout engine). All 30+ color read-sites pick up the new palette
  automatically — no per-call-site changes needed.
- `current_cvd_type` property.
- Internally keeps a pristine `_base_theme` (no CVD) so `set_theme` and
  `set_cvd` compose in any order: switching theme preserves the active CVD,
  and switching CVD re-applies to the new theme. Invalid `cvd_type` is
  logged and ignored (no exception, no render blocking). `cvd_type='none'`
  restores the original theme colors with zero copy.

### Player API — `player.py`
- `GTPPlayer.set_cvd(cvd_type)` and `current_cvd_type` delegate to the inner
  renderer, mirroring the existing `set_theme` / `current_theme_name`
  pattern.

---

## Tooling

- `pyproject.toml`: ruff `extend-exclude` now includes `"*.md"` so markdown
  files (e.g. `README.md`) are skipped by both `ruff check` and
  `ruff format`. This prevents ruff 0.16.x from reformatting Python code
  fences inside prose docs, without weakening source-file enforcement.

---

## Maintenance

- Version bumped from `1.5.0` to `1.6.0` in `pyproject.toml` and
  `ApolloTab/__init__.py`.
- `Lyrics` added to the public API (`ApolloTab.__all__`).
- `readme/功能更新.md` records the v1.6.0 changelog.

---

## Verification

- `mypy` — Success: no issues found (1 source file).
- `ruff check` / `ruff format --check` — all green.
- `pytest` — 331 passed, 9 skipped (coverage 59.39%, above the 40% gate).
  The 9 skips are pre-existing platform limitations (Qt offscreen rendering
  on macOS/Windows, missing `.gp3` samples) — not regressions. New
  `test_lyrics.py` contributes 26 passing cases covering chunk parsing,
  beat assignment, GP3-5 mapping, layout space reservation, and the
  renderer's `_draw_lyrics` no-op path.

---

## Dependencies

No runtime dependency changes. CVD simulation uses PyQt5's `QColor` (already
required for rendering); lyrics parsing uses only the standard library.
