# -*- coding: utf-8 -*-
"""
============================================================
示例3: 音频播放 - 使用FluidSynth播放GTP文件
============================================================

功能:
  演示如何使用 ApolloTab 的音频引擎，
  将 Guitar Pro 文件转换为 MIDI 事件并通过 FluidSynth 实时播放。

适用场景:
  - 乐谱试听和预览
  - 构建音乐教育应用
  - 集成到练习工具
  - MIDI音序器开发

依赖:
  pip install ApolloTab
  
  额外需要:
  - libfluidsynth-3.dll (Windows) 或 libfluidsynth.so (Linux)
  - SoundFont 文件 (.sf2) - 可自动下载或手动放置

运行:
  python audio_playback.py <文件路径.gp5> [音轨索引]

控制:
  - 按 Enter 键暂停/继续
  - 输入 'q' + Enter 退出
  - 输入 's' + Enter 停止并重新开始

示例输出:
  $ python audio_playback.py my_song.gp5 0
  [✓] 音频引擎初始化成功!
  
   正在播放: Sweet Child O' Mine (BPM: 125)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━ 45% ████████████████░░░░░░░
   
   > (按Enter暂停, q退出)

注意事项:
  1. 首次运行可能需要下载SoundFont文件（会自动提示）
  2. 确保系统有音频输出设备（扬声器/耳机）
  3. Linux用户需要安装: sudo apt-get install fluidsynth

创建日期: 2026-06-12
============================================================
"""

import sys
import os
import time
import threading


def main():
    """主函数 - 解析并播放GTP文件"""
    
    # ===== 参数检查 =====
    if len(sys.argv) < 2:
        print("用法: python audio_playback.py <文件路径.gp5> [音轨索引]")
        print("示例: python audio_playback.py my_song.gp5 0")
        print("\n控制命令:")
        print("  Enter    - 暂停/继续")
        print("  q        - 退出程序")
        print("  s        - 停止并重新开始")
        sys.exit(1)
    
    file_path = sys.argv[1]
    track_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        print(f"[错误] 文件不存在: {file_path}")
        sys.exit(1)
    
    # ===== 导入库 =====
    try:
        from ApolloTab import (
            parse_gtp,
            MidiConverter,
            SynthEngine,
        )
    except ImportError as e:
        print(f"[错误] 无法导入 ApolloTab 库")
        print(f"请先安装: pip install ApolloTab")
        sys.exit(1)
    
    # ===== 步骤1: 解析文件 =====
    print(f"\n正在解析: {file_path}")
    
    try:
        song = parse_gtp(file_path)
    except Exception as e:
        print(f"[错误] 解析失败: {e}")
        sys.exit(1)
    
    print(f"✓ 标题: {song.title}, BPM: {song.tempo}")
    
    if track_index >= song.track_count:
        print(f"[警告] 音轨索引{track_index}超出范围，使用第0轨")
        track_index = 0
    
    target_track = song.tracks[track_index]
    print(f"✓ 目标音轨: [{track_index}] {target_track.name}")
    
    # ===== 步骤2: 转换为MIDI事件 =====
    print("\n正在转换为MIDI事件...")
    
    converter = MidiConverter()
    
    try:
        events = converter.convert(song, track_index=track_index)
    except Exception as e:
        print(f"[错误] MIDI转换失败: {e}")
        sys.exit(1)
    
    print(f"✓ 生成了{len(events)}个MIDI事件")
    
    # 显示MIDI事件统计
    note_on_count = sum(1 for e in events if e.type == "note_on")
    tempo_events = [e for e in events if e.type == "tempo"]
    pitch_bend_count = sum(1 for e in events if e.type == "pitch_bend")
    
    print(f"   note_on事件: {note_on_count}")
    print(f"   tempo事件: {len(tempo_events)}")
    if pitch_bend_count > 0:
        print(f"   pitch_bend事件: {pitch_bend_count} (推弦/弯音)")
    
    # ===== 步骤3: 初始化音频引擎 =====
    print("\n正在初始化音频引擎...")
    
    engine = SynthEngine(
        sample_rate=44100,     # 标准CD音质采样率(Hz), 调整效果: 48000更清晰但CPU占用更高
        buffer_size=512,       # 音频缓冲区大小(采样点数), 调整效果: 256延迟更低但可能爆音
        gain=0.8,              # 主音量(0.0-1.0), 调整效果: 1.0=最大音量
    )
    
    try:
        engine.initialize()
    except Exception as e:
        print(f"[错误] 音频引擎初始化失败: {e}")
        print("\n可能的原因:")
        print("  1. 未安装 libfluidsynth (Windows需.dll, Linux需.so)")
        print("  2. 未找到 SoundFont 文件 (.sf2)")
        print("  3. 系统无音频输出设备")
        print("\n解决方案:")
        print("  Windows:")
        print("    - 下载 libfluidsynth-3.dll 放到项目目录或系统PATH")
        print("    - 下载 .sf2 SoundFont 文件放到 ./soundfont/ 目录")
        print("  Linux:")
        print("    - sudo apt-get install fluidsynth")
        print("    - sudo apt-get install fluid-soundfont-gm")
        sys.exit(1)
    
    print("✓ 音频引擎初始化成功!")
    
    # 加载SoundFont
    try:
        sf_path = engine.load_soundfont()
        if sf_path:
            print(f"✓ 已加载SoundFont: {sf_path}")
        else:
            print("[警告] 未找到SoundFont，使用默认音色")
    except Exception as e:
        print(f"[警告] SoundFont加载失败: {e}，使用默认音色")
    
    # 设置乐器音色
    instrument = target_track.instrument or 27  # 默认电吉他
    engine.set_instrument(0, instrument)  # 通道0
    print(f"✓ 已设置乐器: 通道0 → MIDI程序号{instrument} (电吉他)")
    
    # ===== 步骤4: 加载事件并准备播放 =====
    print("\n正在加载MIDI事件...")
    engine.load_events(events, bpm=song.tempo)
    print(f"✓ 就绪! 总时长约 {engine.estimated_duration:.1f}秒")
    
    # ===== 步骤5: 交互式播放控制 =====
    print(f"\n{'=' * 60}")
    print(f"▶ 正在播放: {song.title} (BPM: {song.tempo})")
    print("=" * 60)
    print("控制: Enter=暂停/继续 | q=退出 | s=重新开始\n")
    
    # 启动播放
    engine.play()
    
    # 启动进度显示线程
    stop_progress = threading.Event()
    progress_thread = threading.Thread(
        target=show_progress,
        args=(engine, stop_progress),
        daemon=True
    )
    progress_thread.start()
    
    # 主循环：处理用户输入
    try:
        while True:
            cmd = input("> ").strip().lower()
            
            if cmd == "" or cmd == "p":
                # 暂停/切换
                if engine.is_playing:
                    engine.pause()
                    print("⏸ 已暂停 (按Enter继续)")
                else:
                    engine.resume()
                    print("▶ 继续播放")
                    
            elif cmd == "q":
                # 退出
                print("⏹ 停止播放...")
                break
                
            elif cmd == "s":
                # 重新开始
                engine.stop()
                time.sleep(0.1)  # 等待停止完成
                engine.load_events(events, bpm=song.tempo)
                engine.play()
                print("▶ 重新开始播放")
                
            elif cmd == "seek":
                # 跳转到指定位置（秒）
                try:
                    pos = float(input("  跳转到(秒): "))
                    engine.seek(pos)
                    print(f"✓ 已跳转到 {pos:.1f}秒")
                except ValueError:
                    print("  [错误] 请输入有效数字")
                    
            else:
                print(f"  未知命令: {cmd}")
                print("  可用命令: Enter(暂停) | q(退出) | s(重启) | seek(跳转)")
                
    except KeyboardInterrupt:
        print("\n\n⏹ 用户中断，正在停止...")
    except EOFError:
        # Windows下Ctrl+C可能触发EOFError
        print("\n")
    
    finally:
        # 清理资源
        stop_progress.set()  # 停止进度线程
        time.sleep(0.2)
        
        if engine.is_playing or engine.is_paused:
            engine.stop()
        
        print("✓ 音频引擎已关闭")
        print("\n感谢使用 gtp-engine!")


def show_progress(engine: SynthEngine, stop_event: threading.Event):
    """
    在后台线程中显示播放进度条
    
    参数:
        engine:     SynthEngine实例
        stop_event: threading.Event，用于通知线程退出
        
    显示格式:
      ▶ 播放中... 01:23 / 04:56 [████████████░░░░░░░] 28%
    """
    last_time = 0
    
    while not stop_event.is_set():
        if engine.is_playing and not engine.is_paused:
            current_time = engine.current_time_ms / 1000.0  # 转换为秒
            total_time = engine.estimated_duration
            
            # 避免频繁更新（每500ms更新一次）
            if abs(current_time - last_time) >= 0.5 or last_time == 0:
                last_time = current_time
                
                # 计算进度百分比
                if total_time > 0:
                    progress = min(100, (current_time / total_time) * 100)
                else:
                    progress = 0
                
                # 构建进度条（50字符宽）
                bar_width = 50
                filled = int(bar_width * progress / 100)
                bar = "█" * filled + "░" * (bar_width - filled)
                
                # 格式化时间
                current_str = format_time(current_time)
                total_str = format_time(total_time)
                
                # 打印进度（使用\r实现原地更新）
                status = "▶" if engine.is_playing else "⏸"
                line = f"\r  {status} {current_str} / {total_str} [{bar}] {progress:5.1f}%"
                print(line, end="", flush=True)
        
        # 检查是否自然结束
        if not engine.is_playing and not engine.is_paused:
            # 播放结束
            total_time = engine.estimated_duration
            total_str = format_time(total_time)
            print(f"\r  ✓ 完成! 总时长: {total_str}{' ' * 20}")
            break
        
        time.sleep(0.1)


def format_time(seconds: float) -> str:
    """
    将秒数格式化为 MM:SS 格式
    
    参数:
        seconds: 秒数（可以是浮点数）
        
    返回:
        格式化字符串，如 "04:32"
        
    示例:
        >>> format_time(272.5)
        '04:32'
        >>> format_time(3661.0)
        '61:01'
    """
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


if __name__ == "__main__":
    main()
