# -*- coding: utf-8 -*-
"""
============================================================
示例2: 渲染六线谱 - 将GTP文件渲染为PNG/PDF图像
============================================================

功能:
  演示如何使用 ApolloTab 的渲染功能，
  将 Guitar Pro 文件转换为可视化的六线谱图像。

适用场景:
  - 生成乐谱图片用于打印/分享
  - 批量转换GTP文件为PNG
  - 集成到Web应用或文档系统
  - 创建乐谱预览功能

依赖:
  pip install ApolloTab

运行:
  python render_tab.py <文件路径.gp5> [音轨索引] [输出目录]

示例输出:
  $ python render_tab.py my_song.gp5 0 ./output
  [✓] 渲染完成!
  
  输出文件:
    my_song_track0_p1.png (2480x3508px, A4@300dpi)
    my_song_track0_p2.png (2480x1800px)
    ...

创建日期: 2026-06-12
============================================================
"""

import sys
import os


def main():
    """主函数 - 渲染GTP文件为图像"""
    
    # ===== 参数检查 =====
    if len(sys.argv) < 2:
        print("用法: python render_tab.py <文件路径.gp5> [音轨索引] [输出目录]")
        print("示例: python render_tab.py my_song.gp5 0 ./output")
        print("\n参数说明:")
        print("  文件路径   - .gp3/.gp4/.gp5/.gpx 文件（必填）")
        print("  音轨索引   - 要渲染的音轨号，默认0（可选）")
        print("  输出目录   - 图片保存位置，默认当前目录（可选）")
        sys.exit(1)
    
    file_path = sys.argv[1]
    track_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    output_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        print(f"[错误] 文件不存在: {file_path}")
        sys.exit(1)
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # ===== 导入库 =====
    try:
        from ApolloTab import (
            parse_gtp,
            TabRenderer,
            RenderConfig,
            render_gtp,
        )
    except ImportError as e:
        print(f"[错误] 无法导入 ApolloTab 库")
        print(f"请先安装: pip install ApolloTab")
        sys.exit(1)
    
    # ===== 步骤1: 解析文件 =====
    print(f"\n[1/3] 正在解析: {file_path}")
    
    try:
        song = parse_gtp(file_path)
    except Exception as e:
        print(f"[错误] 解析失败: {e}")
        sys.exit(1)
    
    print(f"  ✓ 标题: {song.title}, 音轨数: {song.track_count}")
    
    # 验证音轨索引
    if track_index >= song.track_count:
        print(f"[警告] 音轨索引{track_index}超出范围(0-{song.track_count - 1})，使用第0轨")
        track_index = 0
    
    target_track = song.tracks[track_index]
    print(f"  ✓ 目标音轨: [{track_index}] {target_track.name}")
    
    # ===== 步骤2: 配置渲染参数 =====
    print("\n[2/3] 配置渲染参数...")
    
    # 方式1: 使用默认配置（最简单）
    # pages = render_gtp(file_path, track_index=track_index)
    
    # 方式2: 自定义配置（推荐用于生产环境）
    config = RenderConfig(
        # === 页面尺寸 ===
        page_width=2480,       # 页面宽度(px), 调整效果: A4@300dpi=2480, 屏幕显示可用1200
        page_height=3508,      # 页面高度(px), A4@300dpi标准尺寸
        
        # === 边距设置 ===
        margin_top=80,         # 上边距(px), 调整效果: 增大则内容下移
        margin_bottom=60,      # 下边距(px)
        margin_left=60,        # 左边距(px)
        margin_right=60,       # 右边距(px)
        
        # === 六线谱样式 ===
        string_spacing=12,     # 弦线间距(px), 调整效果: 增大则谱子更宽更易读
        line_width=1.0,        # 弦线粗细(px), 调整效果: 0.5=细线, 2=粗线
        line_color="#1a1a1a",  # 弦线颜色(十六进制), 深色背景用浅色如"#CCCCCC"
        
        # === 字体设置 ===
        font_family="Arial",   # 字体族, 调整效果: 使用系统支持的字体的名称
        font_size_fret=10,     # 品格数字大小(px), 调整效果: 增大则数字更清晰
        font_size_technique=9, # 技巧标记大小(px),
        
        # === 系统间距 ===
        system_spacing=40,     # 系统(行)间距(px), 调整效果: 增大则行间空白更多
    )
    
    print("  ✓ 渲染配置:")
    print(f"      页面尺寸: {config.page_width}x{config.page_height}px (A4@300dpi)")
    print(f"      弦线间距: {config.string_spacing}px")
    print(f"      字体: {config.font_family} {config.font_size_fret}px")
    
    # ===== 步骤3: 执行渲染 =====
    print(f"\n[3/3] 正在渲染音轨{track_index}...")
    
    renderer = TabRenderer(config=config)
    
    try:
        pages = renderer.render(song, track_index=track_index)
    except Exception as e:
        print(f"[错误] 渲染失败: {e}")
        print("\n可能的原因:")
        print("  1. PyQt5 未正确安装 (pip install PyQt5)")
        print("  2. 文件数据异常")
        sys.exit(1)
    
    # ===== 步骤4: 保存图像 =====
    print(f"\n正在保存{len(pages)}页图像...")
    
    # 生成基础文件名（去除扩展名）
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    saved_files = []
    for i, page in enumerate(pages):
        # 构建输出文件名
        output_file = os.path.join(
            output_dir,
            f"{base_name}_t{track_index}_p{i + 1}.png"
        )
        
        # 保存为PNG（高质量无损格式）
        page.save(output_file, "PNG")
        
        saved_files.append(output_file)
        print(f"  ✓ 第{i + 1}/{len(pages)}页: {output_file}")
        print(f"      尺寸: {page.width()}x{page.height()}px")
    
    # ===== 输出总结 =====
    total_size = sum(os.path.GetSize(f) for f in saved_files) if saved_files else 0
    
    print(f"\n{'=' * 60}")
    print("✅ 渲染完成!")
    print("=" * 60)
    print(f"\n📊 统计信息:")
    print(f"   总页数: {len(pages)}")
    print(f"   输出目录: {os.path.abspath(output_dir)}")
    if total_size > 0:
        print(f"   总大小: {total_size / 1024 / 1024:.2f} MB")
    
    print(f"\n📄 输出文件列表:")
    for i, f in enumerate(saved_files, 1):
        size_kb = os.path.GetSize(f) / 1024
        print(f"   {i}. {os.path.basename(f)} ({size_kb:.1f} KB)")
    
    # ===== 高级功能演示：访问布局数据 =====
    if renderer.last_layouts:
        print(f"\n🔍 布局数据信息:")
        total_systems = sum(len(page.systems) for page in renderer.last_layouts)
        total_measures = sum(
            len(sys.layouts) 
            for page in renderer.last_layouts 
            for sys in page.systems
        )
        print(f"   总系统数(行): {total_systems}")
        print(f"   总小节布局数: {total_measures}")
        print(f"\n💡 提示: 可使用 renderer.last_layouts 实现播放光标等功能")
    
    print(f"\n{'=' * 60}")


def batch_render(input_dir: str, output_dir: str = "./rendered"):
    """
    批量渲染目录下的所有GTP文件
    
    参数:
        input_dir:   包含.gp文件的输入目录
        output_dir:  图像输出目录
        
    示例:
        >>> batch_render("./my_songs", "./output")
        正在处理: song1.gp5...
          ✓ 已保存: ./output/song1_t0_p1.png
        ...
    """
    from ApolloTab import render_gtp
    
    # 支持的文件扩展名
    supported_ext = ('.gp3', '.gp4', '.gp5', '.gpx')
    
    # 扫描目录
    files = [
        f for f in os.listdir(input_dir)
        if os.path.splitext(f)[1].lower() in supported_ext
    ]
    
    if not files:
        print(f"[警告] 目录中未找到GTP文件: {input_dir}")
        return
    
    print(f"找到{len(files)}个GTP文件\n")
    
    os.makedirs(output_dir, exist_ok=True)
    
    success_count = 0
    for file_name in files:
        file_path = os.path.join(input_dir, file_name)
        print(f"正在处理: {file_name}...")
        
        try:
            # 使用便捷函数一键渲染
            pages = render_gtp(file_path, track_index=0)
            
            # 保存所有页面
            base_name = os.path.splitext(file_name)[0]
            for i, page in enumerate(pages):
                output_file = os.path.join(
                    output_dir,
                    f"{base_name}_p{i + 1}.png"
                )
                page.save(output_file, "PNG")
            
            print(f"  ✓ 已保存{len(pages)}页\n")
            success_count += 1
            
        except Exception as e:
            print(f"  ✗ 失败: {e}\n")
    
    print(f"批量渲染完成! 成功: {success_count}/{len(files)}")


if __name__ == "__main__":
    # 支持两种模式：
    # 1. 单文件模式: python render_tab.py file.gp5
    # 2. 批量模式: python render_tab.py --batch ./input_dir ./output
    
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        # 批量模式
        if len(sys.argv) < 3:
            print("用法: python render_tab.py --batch <输入目录> [输出目录]")
            sys.exit(1)
        
        input_dir = sys.argv[2]
        output_dir = sys.argv[3] if len(sys.argv) > 3 else "./rendered"
        batch_render(input_dir, output_dir)
    else:
        # 单文件模式
        main()
