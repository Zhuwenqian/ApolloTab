# ApolloTab

[![Python 3.11+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: LGPL 2.1](https://img.shields.io/badge/License-LGPL_2.1-orange.svg)](https://opensource.org/licenses/LGPL-2.1/)
[![PyPI version](https://badge.fury.io/py/ApolloTab.svg)](https://pypi.org/project/ApolloTab/)

**Guitar Pro File Parsing, Rendering, and Audio Playback Engine Library**

`ApolloTab` is a fully-featured Python library for parsing, rendering, and playing Guitar Pro (.gp3/.gp4/.gp5/.gpx/.gp) tablature files.

## Features

- **File Parsing**: Full support for GP3/GP4/GP5/GPX/GP(GP7/GP8) formats — extract song info, tracks, measures, notes, technique markings
- **Smart Dispatch**: `parse_score()` automatically selects the correct parser based on file extension
- **Tablature Rendering**: Render high-quality tablature images (QPixmap) using QPainter with multi-page output
- **Audio Playback**: Real-time MIDI synthesis engine based on FluidSynth with SoundFont support, including Bank Select + Program Change
- **Repeat Signs**: Full repeat sign (`||:` / `:||`) expansion with playhead timeline synchronization
- **Metronome**: Built-in metronome support with separate MIDI channel (15), configurable volume and beats
- **Dynamic Theme Registration**: Runtime theme registration via `ThemeConfig.register_theme()` for custom color schemes
- **Technique Support**: 18 playing techniques (hammer-on, pull-off, bend, slide, harmonic, vibrato, etc.)
- **Highly Configurable**: Fully adjustable rendering parameters (line width, spacing, colors, fonts, etc.)
- **Theme Support**: Built-in light/dark color themes with custom theme extensibility

## Installation

### Install from PyPI (Recommended)

```bash
pip install ApolloTab
```

### Install from Source

```bash
git clone https://github.com/your-repo/ApolloTab.git
cd ApolloTab

pip install -e .

# Or install dev environment (with test tools)
pip install -e ".[dev]"
```

## Quick Start

### 1. Parse a GTP File

```python
from ApolloTab import parse_gtp

song = parse_gtp("my_song.gp5")

print(f"Title: {song.title}")
print(f"Artist: {song.artist}")
print(f"BPM: {song.tempo}")
print(f"Track count: {song.track_count}")

for track in song.tracks:
    print(f"\nTrack: {track.name}")
    print(f"Tuning: {track.strings}")
    print(f"Measures: {len(track.measures)}")
```

### 2. Render Tablature Images

```python
from ApolloTab import render_gtp

pages = render_gtp("my_song.gp5", track_index=0)

for i, page in enumerate(pages):
    page.save(f"output_page_{i + 1}.png")
    print(f"Saved page {i + 1}")

# Or use TabRenderer for finer control
from ApolloTab import TabRenderer, RenderConfig

config = RenderConfig(
    page_width=2480,
    page_height=3508,
    line_color="#000000",
)

renderer = TabRenderer(config=config)
pages = renderer.render(song, track_index=0)

layouts = renderer.last_layouts  # List[PageLayout]
```

### 3. Audio Playback

```python
from ApolloTab import (
    parse_gtp,
    MidiConverter,
    SynthEngine,
)

song = parse_gtp("my_song.gp5")

converter = MidiConverter()
events = converter.convert(song, track_index=0)

engine = SynthEngine()
engine.initialize()
engine.load_soundfont()
engine.set_instrument(0, 27)  # Electric Guitar (MIDI program 27)

engine.load_events(events, bpm=song.tempo)
engine.play()

import time
time.sleep(5)
engine.pause()
time.sleep(2)
engine.resume()
time.sleep(5)

engine.stop()
```

### 4. Complete Example: Parse -> Render -> Play

```python
from ApolloTab import (
    parse_gtp,
    TabRenderer,
    MidiConverter,
    SynthEngine,
    RenderConfig,
)

def process_gtp_file(file_path: str, track_index: int = 0):
    """
    Complete workflow: Parse -> Render -> Play

    Args:
        file_path:   Path to .gp3/.gp4/.gp5/.gpx file
        track_index: Track index to process (default: first track)
    """
    # Step 1: Parse
    print(f"[1/3] Parsing: {file_path}")
    song = parse_gtp(file_path)
    print(f"  OK Title: {song.title}, BPM: {song.tempo}")

    # Step 2: Render
    print("[2/3] Rendering tablature...")
    renderer = TabRenderer(RenderConfig())
    pages = renderer.render(song, track_index=track_index)

    for i, page in enumerate(pages):
        output_file = f"{song.title}_track{track_index}_p{i + 1}.png"
        page.save(output_file)
        print(f"  OK Saved: {output_file} ({page.width()}x{page.height()}px)")

    # Step 3: Play
    print("[3/3] Initializing audio...")
    converter = MidiConverter()
    events = converter.convert(song, track_index=track_index)

    engine = SynthEngine()
    engine.initialize()
    engine.load_soundfont()
    engine.set_instrument(0, 27)
    engine.load_events(events, bpm=song.tempo)

    print("  > Playing (Ctrl+C to stop)...")
    engine.play()

    try:
        while engine.is_playing:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n  [] Stopped")
        engine.stop()

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python example.py <file_path.gp5> [track_index]")
        sys.exit(1)

    file_path = sys.argv[1]
    track_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    process_gtp_file(file_path, track_index)
```

## API Reference

### Core Functions

| Function                        | Description               | Return Value    |
| ------------------------------- | ------------------------- | --------------- |
| `parse_gtp(path)`               | Parse GTP file            | `GTPSong`       |
| `parse_score(path)`             | Smart dispatch (auto-detect format) | `GTPSong` |
| `render_gtp(path, track_index)` | One-click render GTP file | `List[QPixmap]` |

### Main Classes

#### Data Models (`ApolloTab.models`)

| Class        | Description                                             |
| ------------ | ------------------------------------------------------- |
| `GTPSong`    | Song object (title/artist/BPM/tracks list)              |
| `GTPTrack`   | Track object (name/tuning/measures list)                |
| `GTPMeasure` | Measure object (time signature/repeat marks/beats list) |
| `GTPBeat`    | Beat object (duration/dot/notes list)                   |
| `GTPNote`    | Note object (fret/string/MIDI pitch/techniques)         |

#### Parser (`ApolloTab.parser`)

| Class                | Description                              |
| -------------------- | ---------------------------------------- |
| `GTPParser`          | GTP file parser class (GP3/GP4/GP5/GPX)  |
| `GP7Parser`          | GP7/GP8 (.gp) ZIP unpack parser          |
| `GpifParser`         | GP7/GP8 GPIF XML content parser          |
| `BinaryStylesheet`   | GP7/GP8 binary stylesheet parser         |
| `PartConfiguration`  | GP7/GP8 part configuration parser        |
| `parse_score(path)`  | Smart dispatch (auto-detect by extension)|

**Usage**:

```python
from ApolloTab import parse_score

# Auto-detect format by file extension
song = parse_score("song.gp")     # GP7/GP8
song = parse_score("song.gp5")    # GP5
song = parse_score("song.gpx")    # GPX

# Or use specific parser directly
from ApolloTab.parser import GTPParser, GP7Parser

parser = GTPParser()
song = parser.parse("song.gp5")

gp7 = GP7Parser()
song = gp7.parse("song.gp")
```

#### Renderer (`ApolloTab.renderer`)

| Class             | Description                |
| ----------------- | -------------------------- |
| `TabRenderer`     | Tablature rendering engine |
| `TabLayoutEngine` | Layout calculation engine  |

**Usage**:

```python
from ApolloTab.renderer import TabRenderer, RenderConfig

renderer = TabRenderer(RenderConfig())
pages = renderer.render(song, track_index=0)
```

#### Audio Engine (`ApolloTab.audio`)

| Class                | Description                       |
| -------------------- | --------------------------------- |
| `MidiConverter`      | GTP data to MIDI event converter  |
| `SynthEngine`        | FluidSynth audio synthesis engine |
| `MidiEvent`          | Single MIDI event data model      |
| `MetronomeConfig`    | Metronome configuration (enabled, volume, beats) |
| `MetronomeGenerator` | Metronome MIDI event generator    |

**Usage**:

```python
from ApolloTab.audio import (
    MidiConverter, SynthEngine,
    MetronomeConfig, MetronomeGenerator,
)

converter = MidiConverter()
events = converter.convert(song, track_index=0)

engine = SynthEngine()
engine.initialize()
engine.load_soundfont()
engine.load_events(events, bpm=120)  # main events only (no metronome mixed in)
engine.play()

# [v1.4.0] Metronome events are loaded independently and played on a separate
# thread, no longer mixed into the main event stream. Toggling metronome on/off
# or changing volume no longer requires rebuilding the main MIDI events.
config = MetronomeConfig(enabled=True, volume=0.8, gain=1.5)
ticks_per_beat = 480
metro_events = MetronomeGenerator.generate_for_song(
    song=song, track_index=0, config=config,
    expanded_indices=converter.expand_measure_indices(song.tracks[0].measures),
    ticks_per_beat=ticks_per_beat,
)
engine.load_metronome_events(
    events=metro_events, bpm=120, ticks_per_beat=ticks_per_beat,
)
# To stop: engine.unload_metronome_events()
```

#### Utilities (`ApolloTab.utils`)

| Class / Constant  | Description                                        |
| ----------------- | -------------------------------------------------- |
| `RenderConfig`    | Rendering parameter configuration (all adjustable) |
| `ThemeConfig`     | Rendering theme configuration (color schemes)      |
| `TechniqueType`   | Technique type enum (18 types)                     |
| `StandardTunings` | Standard tuning definitions                        |
| `NoteDuration`    | Duration enum                                      |

## Configuration

### ThemeConfig - Rendering Themes

ApolloTab supports multiple built-in color themes for different use cases:

```python
from ApolloTab import TabRenderer, ThemeConfig

# Get preset themes
light_theme = ThemeConfig.get_theme("light")   # Black & white (print-friendly)
dark_theme = ThemeConfig.get_theme("dark")    # Dark mode (eye-care)

# List all available themes
available = ThemeConfig.list_themes()  # ["light", "dark"]

# Use theme with renderer
renderer = TabRenderer()
renderer.set_theme("light")           # Switch by name
renderer.set_theme(dark_theme)        # Switch by instance

# Or specify at initialization
config = RenderConfig(theme=ThemeConfig.get_theme("light"))
renderer = TabRenderer(config)

# Custom theme (extendable)
my_theme = ThemeConfig(
    colors={
        "COLOR_BG": "#FFFDE7",
        "COLOR_TEXT": "#212121",
        # ... other colors (optional, missing ones use dark theme defaults)
    },
    theme_name="sepia"
)
renderer.set_theme(my_theme)
```

**Built-in Themes:**

| Theme Name | Background | Text Color | Use Case |
| ---------- | ---------- | ---------- | -------- |
| `light`    | `#FFFFFF` (white) | `#000000` (black) | Printing, daytime |
| `dark`     | `#1E1E2E` (dark blue-gray) | `#E2E8F0` (light gray) | Night mode, eye-care |

### RenderConfig Parameters

```python
config = RenderConfig(
    # Page size
    page_width=2480,       # Page width(px), effect: larger = sharper but more memory
    page_height=3508,      # Page height(px), A4@300dpi standard size

    # Margins
    margin_top=80,         # Top margin(px), effect: larger = content shifts down
    margin_bottom=60,      # Bottom margin(px)
    margin_left=60,        # Left margin(px)
    margin_right=60,       # Right margin(px)

    # Tablature style
    string_spacing=12,     # String line spacing(px), effect: larger = wider, more readable
    line_width=1,          # Line thickness(px), effect: 0.5=thin, 2=thick
    line_color="#333333",  # Line color(hex), effect: changes overall tone

    # Font settings
    font_family="Arial",   # Font family, effect: use system-supported font name
    font_size_fret=10,     # Fret number font size(px), effect: larger = clearer numbers
    font_size_technique=9, # Technique label font size(px),

    # System spacing
    system_spacing=40,     # System(row) spacing(px), effect: larger = more whitespace between rows
)
```

### SynthEngine Audio Parameters

```python
engine = SynthEngine(
    sample_rate=44100,     # Sample rate(Hz), effect: 48000 = clearer but higher CPU usage
    buffer_size=512,       # Buffer size, effect: 256 = lower latency but may cause audio glitches
    gain=0.8,              # Master volume(0.0-1.0), effect: 1.0 = max volume
)
```

## Supported Techniques

| Technique                  | Abbreviation       | Symbol Type          |
| -------------------------- | ------------------ | -------------------- |
| Hammer-On                  | H                  | Text label           |
| Pull-Off                   | P                  | Text label           |
| Slide Up                   | s/S                | Line + arrow         |
| Slide Down                 | S                  | Line + arrow         |
| Bend                       | B                  | Arc + arrow + degree |
| Vibrato                    | \~                 | Wavy line            |
| Palm Mute                  | P.M.               | Dashed extension     |
| Staccato                   | .                  | Dot mark             |
| Let Ring                   | Dashed extension   | <br />               |
| Natural Harmonic N.H.      | Diamond mark       | <br />               |
| Artificial Harmonic A.H.   | Diamond + text     | <br />               |
| Tremolo Picking Trem.Pick. | Diagonal underline | <br />               |
| Trill                      | "tr" text          | <br />               |
| Grace Note                 | Small note         | <br />               |
| Accentuated                | >                  | Symbol               |
| Ghost Note                 | Parentheses        | <br />               |

## Development Guide

### Project Structure

```
ApolloTab/
├── __init__.py               # Package entry, exports core API
├── py.typed                  # PEP 561 type hint marker
├── player.py                 # Playback controller (timeline + audio sync)
├── parser/
│   ├── __init__.py           # Smart dispatch parse_score()
│   ├── gtp_parser.py         # PyGuitarPro -> GTPSong (GP3/GP4/GP5/GPX)
│   ├── gp7_parser.py         # GP7/GP8 ZIP unpack + file stream reader
│   ├── gpif_parser.py        # GP7/GP8 GPIF XML content parser
│   ├── binary_stylesheet.py  # GP7/GP8 binary stylesheet parser
│   └── part_configuration.py # GP7/GP8 part configuration parser
├── models/
│   ├── __init__.py
│   ├── song.py               # Song model
│   ├── track.py              # Track model
│   ├── measure.py            # Measure model
│   ├── beat.py               # Beat model
│   └── note.py               # Note model
├── renderer/
│   ├── __init__.py
│   ├── tab_renderer.py       # Tablature drawing engine
│   └── layout_engine.py      # Coordinate layout calculation
├── audio/
│   ├── __init__.py
│   ├── midi_converter.py     # GTP -> MIDI event conversion (incl. repeat expansion)
│   └── synth_engine.py       # FluidSynth synthesis engine (CC/Program Change)
└── utils/
    ├── __init__.py
    └── constants.py          # Global constant definitions (RenderMode, etc.)
```

### Local Development

```bash
git clone https://github.com/your-repo/ApolloTab.git
cd ApolloTab

python -m venv venv
source venv/bin/activate  # Linux/Mac
# or .\venv\Scripts\activate  # Windows

pip install -e ".[dev]"

pytest tests/ -v

black ApolloTab/
isort ApolloTab/

mypy ApolloTab/
```

## Dependencies

### Required Dependencies

| Package                                                    | Version  | Purpose                   | License  |
| ---------------------------------------------------------- | -------- | ------------------------- | -------- |
| [pyguitarpro](https://github.com/Perlence/PyGuitarPro)      | >=0.10.1 | Guitar Pro file parsing   | LGPL-3.0 |
| [pyfluidsynth](https://github.com/nwhitehead/pyfluidsynth) | >=1.4.0  | FluidSynth Python binding | LGPL-2.1 |

> **Note**: PyQt5 is no longer a pip dependency. On ARM architectures, install it via `apt` and create symlinks manually. The rendering engine will use it if available at runtime.

### Optional Dependencies

| Group | Package                    | Purpose                       |
| ----- | -------------------------- | ----------------------------- |
| dev   | pytest, black, isort, mypy | Development and testing tools |

## License

This project is licensed under the [LGPL 2.1](LICENSE).

## Contributing

Issues and Pull Requests are welcome!

1. Fork this repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Submit a Pull Request

## Related Projects

- **[TAB Score Viewer](https://github.com/Zhuwenqian/tab-score-viewer)** - Complete guitar tab viewer app built on ApolloTab
- **[pyguitarpro](https://github.com/Perlence/PyGuitarPro)** - Underlying library for Guitar Pro file parsing
- **[FluidSynth](https://github.com/FluidSynth/fluidsynth)** - Real-time MIDI synthesis engine

***

**Version**: v1.3.0
**Last Updated**: 2026-06-30
**Compatibility**: Windows / Linux / macOS (Python 3.11+)

***

***

# ApolloTab（中文）

[![Python 3.11+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: LGPL 2.1](https://img.shields.io/badge/License-LGPL_2.1-orange.svg)](https://opensource.org/licenses/LGPL-2.1/)
[![PyPI version](https://badge.fury.io/py/ApolloTab.svg)](https://pypi.org/project/ApolloTab/)

**Guitar Pro 文件解析、渲染与音频播放引擎库**

`ApolloTab` 是一个功能完整的 Python 库，用于解析、渲染和播放 Guitar Pro (.gp3/.gp4/.gp5/.gpx/.gp) 格式的吉他谱文件。

## 核心功能

- **文件解析**: 完整支持 GP3/GP4/GP5/GPX/GP(GP7/GP8) 格式，提取歌曲信息、音轨、小节、音符、技巧标记
- **智能调度**: `parse_score()` 根据文件扩展名自动选择对应解析器
- **六线谱渲染**: 使用 QPainter 将乐谱数据渲染为高质量六线谱图像（QPixmap），支持多页输出
- **音频播放**: 基于 FluidSynth 的 MIDI 合成引擎，支持 SoundFont 音色库实时播放，含 Bank Select + Program Change
- **反复记号**: 完整支持反复记号（`||:` / `:||`）展开与播放光标同步
- **节拍器**: 内置节拍器功能，独立 MIDI 通道(15)，可配置音量和拍号
- **动态主题注册**: 运行时主题注册 `ThemeConfig.register_theme()`，支持自定义配色方案
- **技巧支持**: 18种演奏技巧（击弦、勾弦、推弦、滑音、泛音、颤音等）
- **高度可配置**: 渲染参数完全可调（线宽、间距、颜色、字体等）
- **主题支持**: 内置黑白/深色配色方案，支持自定义主题扩展

## 安装

### 从 PyPI 安装（推荐）

```bash
pip install ApolloTab
```

### 从源码安装

```bash
# 克隆仓库
git clone https://github.com/your-repo/ApolloTab.git
cd ApolloTab

# 安装依赖
pip install -e .

# 或安装开发环境（含测试工具）
pip install -e ".[dev]"
```

## 快速开始

### 1. 解析 GTP 文件

```python
from ApolloTab import parse_gtp

# 解析 Guitar Pro 文件
song = parse_gtp("my_song.gp5")

# 查看基本信息
print(f"标题: {song.title}")
print(f"艺术家: {song.artist}")
print(f"BPM: {song.tempo}")
print(f"音轨数: {song.track_count}")

# 遍历音轨
for track in song.tracks:
    print(f"\n音轨: {track.name}")
    print(f"调弦: {track.strings}")
    print(f"小节数: {len(track.measures)}")
```

### 2. 渲染六线谱图像

```python
from ApolloTab import render_gtp

# 一键渲染（返回多页 QPixmap 列表）
pages = render_gtp("my_song.gp5", track_index=0)

# pages 是 List[QPixmap]，每页一张图片
for i, page in enumerate(pages):
    page.save(f"output_page_{i + 1}.png")
    print(f"已保存第 {i + 1} 页")

# 或者使用 TabRenderer 类进行更精细的控制
from ApolloTab import TabRenderer, RenderConfig

# 自定义渲染配置
config = RenderConfig(
    page_width=2480,      # 页面宽度(px)，调整效果: 越大越清晰但内存占用更多
    page_height=3508,     # 页面高度(px)，A4@300dpi标准尺寸
    line_color="#000000", # 弦线颜色
)

renderer = TabRenderer(config=config)
pages = renderer.render(song, track_index=0)

# 访问布局数据（用于播放光标等功能）
layouts = renderer.last_layouts  # List[PageLayout]
```

### 3. 音频播放

```python
from ApolloTab import (
    parse_gtp,
    MidiConverter,
    SynthEngine,
)

# 解析文件
song = parse_gtp("my_song.gp5")

# 转换为 MIDI 事件序列
converter = MidiConverter()
events = converter.convert(song, track_index=0)

# 初始化音频引擎
engine = SynthEngine()
engine.initialize()                    # 初始化 FluidSynth 合成器
engine.load_soundfont()               # 自动搜索并加载 SoundFont
engine.set_instrument(0, 27)          # 设置通道0为电吉他(MIDI程序号27)

# 加载事件并播放
engine.load_events(events, bpm=song.tempo)
engine.play()

# 控制播放
import time
time.sleep(5)  # 播放5秒
engine.pause()
time.sleep(2)  # 暂停2秒
engine.resume()
time.sleep(5)  # 继续播放5秒

# 停止并清理
engine.stop()
```

### 4. 完整示例：解析 → 渲染 → 播放

```python
from ApolloTab import (
    parse_gtp,
    TabRenderer,
    MidiConverter,
    SynthEngine,
    RenderConfig,
)

def process_gtp_file(file_path: str, track_index: int = 0):
    """
    完整处理流程：解析 → 渲染 → 播放
    
    参数:
        file_path:   .gp3/.gp4/.gp5/.gpx 文件路径
        track_index: 要处理的音轨索引（默认第1条）
    """
    # ===== 步骤1: 解析 =====
    print(f"[1/3] 正在解析: {file_path}")
    song = parse_gtp(file_path)
    print(f"  ✓ 标题: {song.title}, BPM: {song.tempo}")
    
    # ===== 步骤2: 渲染 =====
    print("[2/3] 正在渲染六线谱...")
    renderer = TabRenderer(RenderConfig())
    pages = renderer.render(song, track_index=track_index)
    
    for i, page in enumerate(pages):
        output_file = f"{song.title}_track{track_index}_p{i + 1}.png"
        page.save(output_file)
        print(f"  ✓ 已保存: {output_file} ({page.width()}x{page.height()}px)")
    
    # ===== 步骤3: 播放 =====
    print("[3/3] 正在初始化音频...")
    converter = MidiConverter()
    events = converter.convert(song, track_index=track_index)
    
    engine = SynthEngine()
    engine.initialize()
    engine.load_soundfont()
    engine.set_instrument(0, 27)
    engine.load_events(events, bpm=song.tempo)
    
    print("  ▶ 开始播放 (按 Ctrl+C 停止)...")
    engine.play()
    
    try:
        while engine.is_playing:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n  ⏹ 停止播放")
        engine.stop()

# 使用示例
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python example.py <文件路径.gp5> [音轨索引]")
        sys.exit(1)
    
    file_path = sys.argv[1]
    track_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    
    process_gtp_file(file_path, track_index)
```

## API 参考

### 核心函数

| 函数                              | 说明        | 返回值             |
| ------------------------------- | --------- | --------------- |
| `parse_gtp(path)`               | 解析GTP文件   | `GTPSong`       |
| `parse_score(path)`             | 智能调度（自动识别格式）| `GTPSong` |
| `render_gtp(path, track_index)` | 一键渲染GTP文件 | `List[QPixmap]` |

### 主要类

#### 数据模型 (`ApolloTab.models`)

| 类名           | 说明                    |
| ------------ | --------------------- |
| `GTPSong`    | 歌曲对象（标题/艺术家/BPM/音轨列表） |
| `GTPTrack`   | 音轨对象（名称/调弦/小节列表）      |
| `GTPMeasure` | 小节对象（拍号/重复记号/拍列表）     |
| `GTPBeat`    | 拍对象（时值/附点/音符列表）       |
| `GTPNote`    | 音符对象（品格/弦/MIDI音高/技巧）  |

#### 解析器 (`ApolloTab.parser`)

| 类                   | 说明                              |
| -------------------- | --------------------------------- |
| `GTPParser`          | GTP文件解析器类 (GP3/GP4/GP5/GPX) |
| `GP7Parser`          | GP7/GP8 (.gp) ZIP解包解析器       |
| `GpifParser`         | GP7/GP8 GPIF XML内容解析器        |
| `BinaryStylesheet`   | GP7/GP8 二进制样式表解析器        |
| `PartConfiguration`  | GP7/GP8 声部配置解析器            |
| `parse_score(path)`  | 智能调度（根据扩展名自动选择）    |

**用法**:

```python
from ApolloTab import parse_score

# 根据扩展名自动识别格式
song = parse_score("song.gp")      # GP7/GP8
song = parse_score("song.gp5")     # GP5
song = parse_score("song.gpx")     # GPX

# 或直接指定解析器
from ApolloTab.parser import GTPParser, GP7Parser

parser = GTPParser()
song = parser.parse("song.gp5")

gp7 = GP7Parser()
song = gp7.parse("song.gp")
```

#### 渲染器 (`ApolloTab.renderer`)

| 类名                | 说明      |
| ----------------- | ------- |
| `TabRenderer`     | 六线谱渲染引擎 |
| `TabLayoutEngine` | 布局计算引擎  |

**用法**:

```python
from ApolloTab.renderer import TabRenderer, RenderConfig

renderer = TabRenderer(RenderConfig())
pages = renderer.render(song, track_index=0)
```

#### 音频引擎 (`ApolloTab.audio`)

| 类名                 | 说明               |
| ------------------ | ---------------- |
| `MidiConverter`     | GTP数据→MIDI事件转换器  |
| `SynthEngine`       | FluidSynth音频合成引擎 |
| `MidiEvent`         | 单个MIDI事件数据模型     |
| `MetronomeConfig`   | 节拍器配置（启用、音量、拍号） |
| `MetronomeGenerator` | 节拍器MIDI事件生成器    |

**用法**:

```python
from ApolloTab.audio import (
    MidiConverter, SynthEngine,
    MetronomeConfig, MetronomeGenerator,
)

converter = MidiConverter()
events = converter.convert(song, track_index=0)

engine = SynthEngine()
engine.initialize()
engine.load_soundfont()
engine.load_events(events, bpm=song.tempo)  # 主事件流（不再混入节拍器）
engine.play()

# [v1.4.0] 节拍器事件独立加载：通过独立线程播放，不与主事件流混合
# 切换节拍器开关/音量时无需重建主事件
config = MetronomeConfig(enabled=True, volume=0.8, gain=1.5)
ticks_per_beat = 480
metro_events = MetronomeGenerator.generate_for_song(
    song=song, track_index=0, config=config,
    expanded_indices=converter.expand_measure_indices(song.tracks[0].measures),
    ticks_per_beat=ticks_per_beat,
)
engine.load_metronome_events(
    events=metro_events, bpm=song.tempo, ticks_per_beat=ticks_per_beat,
)
# 关闭：engine.unload_metronome_events()
```

#### 工具 (`ApolloTab.utils`)

| 类/常量              | 说明                    |
| ----------------- | -------------------- |
| `RenderConfig`    | 渲染参数配置（全部可调）   |
| `ThemeConfig`     | 渲染主题配置（配色方案）     |
| `TechniqueType`   | 技巧类型枚举（18种）    |
| `StandardTunings` | 标准调弦定义            |
| `NoteDuration`    | 时值枚举              |

## 配置说明

### ThemeConfig - 渲染主题

ApolloTab 支持多套内置配色方案，适用于不同使用场景：

```python
from ApolloTab import TabRenderer, ThemeConfig

# 获取预设主题
light_theme = ThemeConfig.get_theme("light")   # 黑白配色（适合打印）
dark_theme = ThemeConfig.get_theme("dark")    # 深色配色（护眼模式）

# 列出所有可用主题
available = ThemeConfig.list_themes()  # ["light", "dark"]

# 使用主题
renderer = TabRenderer()
renderer.set_theme("light")           # 通过名称切换
renderer.set_theme(dark_theme)        # 通过实例切换

# 或在初始化时指定
config = RenderConfig(theme=ThemeConfig.get_theme("light"))
renderer = TabRenderer(config)

# 自定义主题（可扩展）
my_theme = ThemeConfig(
    colors={
        "COLOR_BG": "#FFFDE7",       # 米黄色背景
        "COLOR_TEXT": "#212121",     # 近黑色文字
        # ... 其他颜色参数（可选，缺失的使用深色主题默认值）
    },
    theme_name="sepia"               # 自定义名称
)
renderer.set_theme(my_theme)
```

**内置主题:**

| 主题名称 | 背景色 | 文字色 | 适用场景 |
| ------ | ------ | ------ | ------ |
| `light` | `#FFFFFF` (纯白) | `#000000` (黑色) | 打印输出、白天使用 |
| `dark` | `#1E1E2E` (深蓝灰) | `#E2E8F0` (亮白灰) | 夜间模式、护眼 |

### RenderConfig 渲染参数

```python
config = RenderConfig(
    # 页面尺寸
    page_width=2480,       # 页面宽度(px), 调整效果: A4@300dpi=2480, 屏幕显示可用1200
    page_height=3508,      # 页面高度(px), 调整效果: A4@3508, 可根据需要调整
    
    # 边距
    margin_top=80,         # 上边距(px), 调整效果: 增大则内容下移
    margin_bottom=60,      # 下边距(px)
    margin_left=60,        # 左边距(px)
    margin_right=60,       # 右边距(px)
    
    # 六线谱样式
    string_spacing=12,     # 弦线间距(px), 调整效果: 增大则谱子更宽更易读
    line_width=1,          # 弦线粗细(px), 调整效果: 0.5=细线, 2=粗线
    line_color="#333333",  # 弦线颜色(十六进制), 调整效果: 改变整体色调
    
    # 字体设置
    font_family="Arial",   # 字体族, 调整效果: 使用系统支持的字体的名称
    font_size_fret=10,     # 品格数字大小(px), 调整效果: 增大则数字更清晰
    font_size_technique=9, # 技巧标记大小(px),
    
    # 系统间距
    system_spacing=40,     # 系统(行)间距(px), 调整效果: 增大则行间空白更多
)
```

### SynthEngine 音频参数

```python
engine = SynthEngine(
    sample_rate=44100,     # 采样率(Hz), 调整效果: 48000更清晰但CPU占用更高
    buffer_size=512,       # 缓冲区大小, 调整效果: 256延迟更低但可能爆音
    gain=0.8,              # 主音量(0.0-1.0), 调整效果: 1.0=最大音量
)
```

## 支持的演奏技巧

| 技巧              | 缩写     | 符号类型     |
| --------------- | ------ | -------- |
| 击弦 Hammer-On    | H      | 文字标签     |
| 勾弦 Pull-Off     | P      | 文字标签     |
| 上滑音 Slide Up    | s/S    | 连线+箭头    |
| 下滑音 Slide Down  | S      | 连线+箭头    |
| 推弦 Bend         | B      | 弧线+箭头+度数 |
| 颤音 Vibrato      | \~     | 波浪线      |
| 闷音 Palm Mute    | P.M.   | 虚线延长线    |
| 断奏 Staccato     | .      | 点标记      |
| 延音 Let Ring     | 虚线延长线  | <br />   |
| 自然泛音 N.H.       | 菱形标记   | <br />   |
| 人工泛音 A.H.       | 菱形+文字  | <br />   |
| 震音拨弦 Trem.Pick. | 斜线下划线  | <br />   |
| 颤音 Trill        | "tr"文字 | <br />   |
| 装饰音 Grace Note  | 小音符    | <br />   |
| 重音 Accentuated  | >      | 符号       |
| 幽灵音 Ghost Note  | 括号包裹   | <br />   |

## 开发指南

### 项目结构

```
ApolloTab/
├── __init__.py               # 包入口，导出核心API
├── py.typed                  # PEP 561 类型提示标记
├── player.py                 # 播放控制器（时间线 + 音频同步）
├── parser/
│   ├── __init__.py
│   ├── gtp_parser.py         # PyGuitarPro → GTPSong (GP3/GP4/GP5/GPX)
│   ├── gp7_parser.py         # GP7/GP8 ZIP解包与文件流读取
│   ├── gpif_parser.py        # GP7/GP8 GPIF XML完整解析器
│   ├── binary_stylesheet.py  # GP7/GP8 二进制样式表解析
│   └── part_configuration.py # GP7/GP8 声部配置解析
├── models/
│   ├── __init__.py
│   ├── song.py           # 歌曲模型
│   ├── track.py          # 音轨模型
│   ├── measure.py        # 小节模型
│   ├── beat.py           # 拍模型
│   └── note.py           # 音符模型
├── renderer/
│   ├── __init__.py
│   ├── tab_renderer.py   # 六线谱绘制引擎
│   └── layout_engine.py  # 坐标布局计算
├── audio/
│   ├── __init__.py
│   ├── midi_converter.py # GTP → MIDI事件转换
│   └── synth_engine.py   # FluidSynth合成引擎
└── utils/
    ├── __init__.py
    └── constants.py      # 全局常量定义
```

### 本地开发

```bash
# 克隆仓库
git clone https://github.com/your-repo/ApolloTab.git
cd ApolloTab

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 .\venv\Scripts\activate  # Windows

# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 代码格式化
black ApolloTab/
isort ApolloTab/

# 类型检查
mypy ApolloTab/
```

## 依赖项

### 必需依赖

| 包名                                                         | 版本      | 用途                  | 许可证      |
| ---------------------------------------------------------- | ------- | ------------------- | -------- |
| [pyguitarpro](https://github.com/Perlence/PyGuitarPro)          | >=0.10.1 | Guitar Pro文件解析      | LGPL-3.0 |
| [pyfluidsynth](https://github.com/nwhitehead/pyfluidsynth) | >=1.4.0  | FluidSynth Python绑定   | LGPL-2.1 |

> **注意**: PyQt5 已不再是 pip 依赖。在 ARM 架构上需通过 `apt` 安装后手动创建软链接，pip 无法直接安装。渲染引擎会在运行时检测并使用已安装的 PyQt5。

### 可选依赖

| 组名  | 包名                         | 用途      |
| --- | -------------------------- | ------- |
| dev | pytest, black, isort, mypy | 开发和测试工具 |

## 许可证

本项目采用 [LGPL 2.1](LICENSE) 开源协议。

## 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

## 相关项目

- **[TAB Score Viewer](https://github.com/Zhuwenqian/tab-score-viewer)** - 基于 ApolloTab 的完整吉他谱查看器应用
- **[pyguitarpro](https://github.com/Perlence/PyGuitarPro)** - Guitar Pro 文件解析底层库
- **[FluidSynth](https://github.com/FluidSynth/fluidsynth)** - 实时 MIDI 合成引擎

***

**版本**: v1.3.0
**最后更新**: 2026-06-30
**兼容性**: Windows / Linux / macOS (Python 3.11+)
