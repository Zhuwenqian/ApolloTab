# -*- coding: utf-8 -*-
"""
============================================================
示例1: 基础解析 - 解析GTP文件并显示基本信息
============================================================

功能:
  演示如何使用 ApolloTab 解析 Guitar Pro 文件，
  提取歌曲元数据和音轨信息。

适用场景:
  - 学习 ApolloTab 基本用法
  - 批量提取乐谱信息
  - 构建乐谱管理系统

依赖:
  pip install ApolloTab

运行:
  python basic_parse.py <文件路径.gp5>

示例输出:
  $ python basic_parse.py my_song.gp5
  [✓] 文件解析成功!
  
  📄 歌曲信息:
     标题: Sweet Child O' Mine
     艺术家: Guns N' Roses
     BPM: 125
     音轨数: 6
  
  🎸 音轨列表:
     [0] Lead Guitar (标准调弦 EADGBE) - 120小节
     [1] Rhythm Guitar (标准调弦 EADGBE) - 120小节
     [2] Bass (降D调弦) - 120小节
     ...

创建日期: 2026-06-12
============================================================
"""

import sys
import os


def main():
    """主函数 - 解析并显示GTP文件信息"""
    
    # ===== 参数检查 =====
    if len(sys.argv) < 2:
        print("用法: python basic_parse.py <文件路径.gp5>")
        print("示例: python basic_parse.py my_song.gp5")
        print("\n支持的格式: .gp3, .gp4, .gp5, .gpx")
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        print(f"[错误] 文件不存在: {file_path}")
        sys.exit(1)
    
    # ===== 导入库（延迟导入，提供友好的错误提示）=====
    try:
        from ApolloTab import parse_gtp, GTPParser
    except ImportError as e:
        print(f"[错误] 无法导入 ApolloTab 库")
        print(f"请先安装: pip install ApolloTab")
        print(f"详细错误: {e}")
        sys.exit(1)
    
    # ===== 步骤1: 解析文件 =====
    print(f"\n正在解析: {file_path}")
    
    try:
        # 方式1: 使用便捷函数（推荐）
        song = parse_gtp(file_path)
        
        # 方式2: 使用解析器类（更灵活）
        # parser = GTPParser()
        # song = parser.parse(file_path)
        
    except Exception as e:
        print(f"[错误] 解析失败: {e}")
        print("\n可能的原因:")
        print("  1. 文件格式不支持（仅支持 .gp3/.gp4/.gp5/.gpx）")
        print("  2. 文件已损坏")
        print("  3. 缺少 pyguitarpro 依赖 (pip install pyguitarpro)")
        sys.exit(1)
    
    print("[✓] 文件解析成功!\n")
    
    # ===== 步骤2: 显示歌曲信息 =====
    print("=" * 50)
    print("📄 歌曲信息:")
    print("=" * 50)
    print(f"   标题: {song.title or '(未设置)'}")
    print(f"   艺术家: {song.artist or '(未设置)'}")
    print(f"   专辑: {song.album or '(未设置)'}")
    print(f"   BPM: {song.tempo}")
    print(f"   调号: {song.key}")  # 0=C大调, 正值=升号, 负值=降号
    print(f"   音轨数: {song.track_count}")
    
    # ===== 步骤3: 遍历音轨 =====
    print(f"\n{'=' * 50}")
    print("🎸 音轨列表:")
    print("=" * 50)
    
    for i, track in enumerate(song.tracks):
        # 格式化调弦为可读字符串（MIDI音高→音符名）
        tuning_str = format_tuning(track.strings)
        
        # 统计小节数和总拍数
        measure_count = len(track.measures)
        total_beats = sum(len(m.beats) for m in track.measures)
        
        print(f"\n   [{i}] {track.name or f'音轨{i + 1}'}")
        print(f"       调弦: {tuning_str}")
        print(f"       品格数: {track.fret_count}")
        print(f"       MIDI乐器: {track.instrument} (程序号)")
        print(f"       小节数: {measure_count}")
        print(f"       总拍数: {total_beats}")
        
        # 可见性标记
        visibility = []
        if not track.is_visible:
            visibility.append("隐藏")
        if track.is_solo:
            visibility.append("独奏")
        if track.is_mute:
            visibility.append("静音")
        if visibility:
            print(f"       状态: {', '.join(visibility)}")
    
    # ===== 步骤4: 显示第一轨的第一小节详情（可选）=====
    if song.tracks and song.tracks[0].measures:
        first_track = song.tracks[0]
        first_measure = first_track.measures[0]
        
        print(f"\n{'=' * 50}")
        print("📝 第一小节示例 (音轨0):")
        print("=" * 50)
        print(f"   小节号: {first_measure.number}")
        print(f"   拍号: {first_measure.time_signature[0]}/{first_measure.time_signature[1]}")
        
        if first_measure.marker:
            print(f"   段落标记: {first_measure.marker}")
        
        if first_measure.is_repeat_open:
            print("   重复开始: ✓")
        if first_measure.repeat_close > 0:
            print(f"   重复结束: 第{first_measure.repeat_close}次")
        
        # 显示前3个拍的详细信息
        print(f"\n   前{min(3, len(first_measure.beats))}个拍:")
        for j, beat in enumerate(first_measure.beats[:3]):
            print(f"\n     拍{j + 1}:")
            print(f"       时值: {beat.duration.name}")  # QUARTER/EIGHTH等
            if beat.is_dotted:
                print(f"       附点: ✓")
            if beat.is_rest:
                print(f"       休止符: ✓")
            if beat.text:
                print(f"       文本标注: {beat.text}")
            
            # 显示该拍的所有音符
            if beat.notes:
                print(f"       音符数: {len(beat.notes)}")
                for k, note in enumerate(beat.notes[:2]):  # 只显示前2个音符
                    techniques = [t.value for t in note.techniques] if note.techniques else []
                    tech_str = ", ".join(techniques) if techniques else "无"
                    print(f"         [{k}] 弦{note.string + 1}品{note.fret} "
                          f"(MIDI:{note.midi_pitch}) 技巧:[{tech_str}]")
                if len(beat.notes) > 2:
                    print(f"         ... 还有{len(beat.notes) - 2}个音符")
    
    print(f"\n{'=' * 50}")
    print("✅ 解析完成!")
    print("=" * 50)


def format_tuning(tuning: tuple) -> str:
    """
    将MIDI音高元组格式化为可读的调弦字符串
    
    参数:
        tuning: MIDI音高元组，如 (64, 59, 55, 50, 45, 40)
        
    返回:
        可读字符串，如 "E4 B3 G3 D3 A2 E2"
        
    示例:
        >>> format_tuning((64, 59, 55, 50, 45, 40))
        'E4 B3 G3 D3 A2 E2'
    """
    # MIDI音高→音符名映射表（C4=60中央C）
    NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    
    notes = []
    for midi_pitch in tuning:
        note_name = NOTE_NAMES[midi_pitch % 12]
        octave = (midi_pitch // 12) - 1  # MIDI octave公式
        notes.append(f"{note_name}{octave}")
    
    return " ".join(notes)


if __name__ == "__main__":
    main()
