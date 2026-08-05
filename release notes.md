# ApolloTab Release Notes

The entries below cover all commits from `7152997` through `0ed11ad`.
Contributors: LiJingrong123456

## Overview

This release introduces **chord parsing and rendering** for both GP3-5 and GP7/GP8
files, decouples the **metronome onto an independent playback thread**, hardens the
**synth/player encapsulation** with a clean public API, and fixes several
**playhead alignment** and **pause/resume** bugs in the audio engine. The minimum
supported Python version is raised to **3.11**.

---

## Features

### Chord Support and Rendering
- **New `Chord` data model** (`ApolloTab/models/chord.py`): stores key note, bass
  note, suffix (`m` / `dim` / `aug` / `sus4` / `m7b5`), and extensions
  (`7` / `maj7` / `9` / `11` / `13` / `b9` / `#11`). The full chord name
  (e.g. `G/B`, `Am7b5`, `C13`) is auto-computed in `__post_init__`.
- **`GTPBeat.chord` field** added to the beat model; `Chord` is now exported from
  both `ApolloTab` and `ApolloTab.models`.
- **GPIF (GP7/GP8) chord parsing**: `_build_chord_from_xml` converts each
  `<Chord>` definition into a `Chord` object. Definitions are collected in
  document order into `_chord_definitions`, and each beat's `<Chord>` reference
  (an integer index) is resolved to `beat.chord` during model construction.
  Suffix priority rules: 13th suppresses 7th/9th, 6th suppresses 7th, and
  `m7b5` does not duplicate the 7th.
- **GP3-5 chord extraction**: `_convert_gp3_chord` maps PyGuitarPro's structured
  `Chord` fields (`root` / `bass` / `type` / `extension` / `sharp`) onto the
  ApolloTab `Chord` model, with sharp/flat pitch-class name resolution. A
  validation step cross-checks the constructed name against PyGuitarPro's
  precomputed `name`; mismatches return `None` so ambiguous chords are hidden
  rather than rendered incorrectly.
- **Chord name rendering** in `TabRenderer`: `_draw_chord_names` paints each
  chord name above the staff (12pt bold with a rounded background), and
  `_first_chord_in_sequence` deduplicates consecutive identical chords so a
  sustained chord is labeled only once per run. `RenderConfig.PAGE_MARGIN_TOP`
  is increased from `60` to `100` px to reserve space for the labels.

### Metronome Independence
- Metronome events are no longer mixed into the main MIDI event stream. They are
  loaded via `SynthEngine.load_metronome_events(...)` and driven by a dedicated
  thread with its own time base, pause/stop flags, and FluidSynth channel.
- Toggling the metronome or changing its volume no longer requires rebuilding
  the main audio events. See the updated README snippet using
  `MetronomeGenerator.generate_for_song(...)`.

---

## Improvements

### SynthEngine / GTPPlayer Encapsulation
- New public API on `SynthEngine` replaces direct access to private members:
  - `is_synth_available` (replaces `hasattr(engine, '_synth') and engine._synth`)
  - `set_channel_volume(channel, midi_volume)` / `set_master_volume(midi_volume)`
    (MIDI CC#7, real-time, no restart)
  - `loop_state`, `is_loop_enabled`, `loop_time_range` (read-only A/B loop state)
- `GTPPlayer` now calls these methods instead of reaching into
  `engine._synth`, `engine._lock`, and `engine._loop_*`. The `hasattr`-based
  guard that masked real errors has been removed.

### Instrument Auto-Inference (GP3-5)
- `GTPParser` infers the MIDI program from the track name when the file leaves
  the instrument at PyGuitarPro's default (steel acoustic guitar, program 25).
  Mapping (`NAME_BASED_INSTRUMENT_MAP`): Nylon Guitar -> 24, Acoustic/Steel
  Guitar -> 25, Overdriven Guitar -> 29, Piano/Keyboard -> 0. Drum tracks
  (`DRUM_KEYWORDS`) are skipped so their percussion mapping is preserved.

### GPIF Parsing and Bend Style
- `BendData` gains a `bend_style: BendStyle` field (uses the existing
  `BendStyle` enum) so bend rendering can vary curve shape by style.
- GPIF voice handling fixed: invalid voice slots (`-1`) no longer each emit an
  empty rest beat. Previously `<Voices>0 -1 -1 -1</Voices>` injected three extra
  rests and overflowed the measure (e.g. 7 quarter beats in 4/4). A single
  placeholder rest is now added only when every voice in a bar is invalid.

---

## Bug Fixes

### Metronome Freeze When Enabled Mid-Playback
- Enabling the metronome during playback previously froze audio for up to ~30
  seconds because the metronome thread iterated every past event to skip them.
- `load_metronome_events` now accepts a `start_index` argument.
  `GTPPlayer._refresh_metronome` computes it with `bisect.bisect_right` over
  event times so the thread begins iterating from the first upcoming event.
- `_metronome_start_perf` is initialized to
  `perf_counter() - start_offset_ms / 1000`, aligning the thread's `elapsed_ms`
  with the song timeline so the first click sounds within milliseconds.

### Playhead Jumping to the Next Measure Early
- Per-beat tick accumulation used `int()` truncation (e.g. triplet
  `int(480 * 0.3333) = 159`), so a measure's beats summed to fewer ticks than
  its time signature required. The next measure therefore started early, while
  the last note was still sounding.
- After each measure, `current_time_ticks` is now aligned to
  `measure_start_ticks + measure_ticks` so the next measure's first beat
  matches the MIDI event grid.
- A **sentinel timeline entry** is appended at the last beat's end time
  (clamped just before the measure boundary). The playhead now holds at the
  last beat during the final note's sustain, then smoothly scrolls to the next
  measure during the gap. The monotonic-`scroll_y` post-processing skips
  sentinel entries so their shared position is preserved.

### Pause/Resume Restarted Playback from the Beginning
- `pause()` previously set `_is_paused = True` before reading
  `current_time_ms`; the property then returned the stale `_current_time_ms`
  (`0`), so the paused position was lost.
- `pause()` now captures `current_time_ms` first, then freezes it.
- `resume()` recomputes `_start_time` so that `elapsed` continues from the
  paused position rather than resetting toward `_initial_time_offset`.
- `_wait_until` no longer resets `_start_time` on resume, which had overwritten
  the correction above and snapped playback back to the start.
- Pause now affects only the main MIDI stream; the metronome thread keeps its
  own time base so users can continue practicing against the click while the
  song is paused.

### Documentation
- Docstring corrections across modules.

---

## Maintenance

### Minimum Python Version Raised to 3.11
- `pyproject.toml` `requires-python` and `[tool.mypy] python_version` moved to
  3.11. All module header comments updated from `Python 3.8+` to `Python 3.11+`.
- README badges and compatibility lines updated accordingly.

### License and Metadata Corrections
- README license references corrected to **LGPL-2.1** (previously mixed
  LGPL-2.0 / MPL-2.0).
- pyguitarpro upstream URL updated to `Perlence/PyGuitarPro`.

### Housekeeping
- `.gitignore` updated; Python `__pycache__` artifacts cleaned from the tree.

---

## Tests

Three new test modules live under `ApolloTab/tests/`:
- **`test_chord.py`** - `Chord` dataclass, `_chord_note_name` accidental
  mapping, `_build_chord_from_xml` across major/minor/dim/aug/sus4/6/7/9/13
  and slash chords, priority suppression rules, and `GpifParser` end-to-end
  attachment of chords to beats.
- **`test_chord_gp3.py`** - `_pitch_class_to_name` sharp/flat resolution,
  `_convert_gp3_chord` structured-field mapping with defensive null checks
  and name-mismatch fallback, plus end-to-end parsing of a real GP3 file.
- **`test_chord_renderer.py`** - `_first_chord_in_sequence` deduplication
  edge cases and `_draw_chord_names` no-op/crash safety, with Qt forced to
  the `offscreen` platform for CI friendliness.

---

## Dependencies

No changes to runtime dependencies (`pyguitarpro>=0.10.1`, `pyfluidsynth>=1.4.0`).
PyQt5 remains an optional, non-pip dependency (install via system package
manager on ARM and create symlinks manually).
