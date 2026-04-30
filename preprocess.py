#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preprocess.py  —  WhiteRectFitter v3 预处理器
仿照 bad_apple_virus/bad apple.py

将视频离线分解为 boxes.bin，播放时零计算。

用法：
    python preprocess.py input.mp4
    python preprocess.py input.mp4 --out boxes.bin --width 128 --max-rects 100 --thresh 127

二进制格式 boxes.bin
─────────────────────────────────────────────────
Header (16 bytes):
  char[4]    magic = "WRF2"
  uint16     base_w          分析宽度（坐标空间）
  uint16     base_h          分析高度（等比）
  float32    fps
  uint32     total_frames

Body (逐帧):
  [x:u16  y:u16  w:u16  h:u16] × N   ← N 个矩形
  [0      0      0      0     ]        ← 帧分隔符 (w=h=0)
─────────────────────────────────────────────────
坐标系：[0, base_w) × [0, base_h)，播放时按比例缩放至屏幕。
"""

from __future__ import annotations
import argparse
import struct
import sys
import time
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

# ─────────────────────────────────────────────────────────────────
# 二进制格式常量
# ─────────────────────────────────────────────────────────────────
MAGIC         = b'WRF2'
HEADER_FMT    = '<4sHHfI'   # magic base_w base_h fps total_frames
HEADER_SIZE   = struct.calcsize(HEADER_FMT)   # 16 bytes
COORD_FMT     = '<HHHH'
COORD_SIZE    = struct.calcsize(COORD_FMT)    # 8 bytes
FRAME_SEP     = struct.pack(COORD_FMT, 0, 0, 0, 0)

assert HEADER_SIZE == 16
assert COORD_SIZE  == 8


def write_header(f, base_w: int, base_h: int, fps: float, total_frames: int):
    f.write(struct.pack(HEADER_FMT, MAGIC, base_w, base_h, fps, total_frames))


def write_frame(f, rects: List[Tuple[int, int, int, int]]):
    for x, y, w, h in rects:
        f.write(struct.pack(COORD_FMT, x, y, w, h))
    f.write(FRAME_SEP)


# ─────────────────────────────────────────────────────────────────
# 算法：贪心最大矩形（仿 bad_apple_virus bad apple.py）
#
# 每次迭代：
#   1. 对当前工作掩码建立列高直方图（numpy，O(W*H)）
#   2. 单行求最大直方图矩形（Python 栈，O(W*H)）
#   3. 记录矩形，将该区域涂黑
#   4. 循环直到 max_rects 或面积 < 4
#
# 速度：在 64×48 分辨率下约 20-50ms/帧（纯 Python）。
# 离线运行，不影响播放性能。
# ─────────────────────────────────────────────────────────────────

def _maxrect_in_hist(hist: np.ndarray, y_bottom: int
                     ) -> Tuple[int, int, int, int]:
    """
    经典单调栈，O(W)。
    返回 (x, y_top, w, h)，坐标系：y=0 在顶部。
    """
    W = len(hist)
    best_area = 0
    best = (0, 0, 0, 0)
    stack: List[int] = []

    for i in range(W + 1):
        v = int(hist[i]) if i < W else 0
        while stack and int(hist[stack[-1]]) > v:
            idx = stack.pop()
            ht = int(hist[idx])
            left = stack[-1] if stack else -1
            wt = i - left - 1
            area = ht * wt
            if area > best_area:
                best_area = area
                best = (left + 1, y_bottom - ht + 1, wt, ht)
        stack.append(i)

    return best


def decompose_frame(gray_small: np.ndarray,
                    thresh: int,
                    max_rects: int) -> List[Tuple[int, int, int, int]]:
    """
    返回 (x, y, w, h) 列表，坐标系为 gray_small 的像素坐标。
    保证：每个矩形内所有像素均为白色（零越界）。
    """
    H, W = gray_small.shape
    work = (gray_small >= thresh).astype(np.uint8)
    rects: List[Tuple[int, int, int, int]] = []
    hist = np.zeros(W, dtype=np.int32)

    for _ in range(max_rects):
        # 重建列高直方图
        hist[:] = 0
        best_area = 0
        best = (0, 0, 0, 0)

        for y in range(H):
            # numpy 向量化更新直方图列高
            hist = np.where(work[y] > 0, hist + 1, np.int32(0))
            r = _maxrect_in_hist(hist, y)
            area = r[2] * r[3]
            if area > best_area:
                best_area = area
                best = r

        if best_area < 4:
            break

        x, y, w, h = best
        rects.append(best)
        work[y:y + h, x:x + w] = 0

    return rects


# ─────────────────────────────────────────────────────────────────
# 主处理流程
# ─────────────────────────────────────────────────────────────────

def preprocess(input_path: str, output_path: str,
               base_w: int, max_rects: int, thresh: int):

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        print(f"[错误] 无法打开视频文件: {input_path}", file=sys.stderr)
        sys.exit(1)

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vid_w        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h        = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    base_h       = max(1, round(base_w * vid_h / vid_w))

    print(f"  输入分辨率 : {vid_w} × {vid_h}")
    print(f"  分析分辨率 : {base_w} × {base_h}")
    print(f"  帧率       : {fps:.2f} fps")
    print(f"  总帧数     : {total_frames}")
    print(f"  最大矩形数 : {max_rects}")
    print(f"  白色阈值   : {thresh}")
    print(f"  输出文件   : {output_path}")
    print()

    # 统计
    max_rects_seen = 0
    total_rect_count = 0
    t_start = time.perf_counter()

    with open(output_path, 'wb') as f:
        # 先写占位 header，处理完成后回填 total_frames
        write_header(f, base_w, base_h, fps, total_frames)
        actual_frames = 0

        frame_idx = 0
        while True:
            ret, bgr = cap.read()
            if not ret:
                break

            # 灰度 + 缩放至分析分辨率
            gray  = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (base_w, base_h),
                               interpolation=cv2.INTER_AREA)

            rects = decompose_frame(small, thresh, max_rects)
            write_frame(f, rects)

            actual_frames += 1
            frame_idx    += 1
            total_rect_count += len(rects)
            if len(rects) > max_rects_seen:
                max_rects_seen = len(rects)

            # 进度
            if frame_idx % 30 == 0 or frame_idx == total_frames:
                pct     = frame_idx / max(total_frames, 1) * 100
                elapsed = time.perf_counter() - t_start
                fps_proc = frame_idx / max(elapsed, 1e-6)
                eta      = (total_frames - frame_idx) / max(fps_proc, 1e-6)
                bar_w    = 30
                done     = int(bar_w * frame_idx / max(total_frames, 1))
                bar      = '█' * done + '░' * (bar_w - done)
                print(f"\r  [{bar}] {pct:5.1f}%  "
                      f"{frame_idx}/{total_frames}帧  "
                      f"{fps_proc:.1f}fps  ETA {eta:.0f}s  "
                      f"矩形/帧={len(rects)}",
                      end='', flush=True)

        print()

        # 回填真实帧数
        f.seek(0)
        write_header(f, base_w, base_h, fps, actual_frames)

    cap.release()

    elapsed = time.perf_counter() - t_start
    out_size = Path(output_path).stat().st_size / 1024
    print(f"\n✓ 完成！")
    print(f"  实际帧数     : {actual_frames}")
    print(f"  最多矩形/帧  : {max_rects_seen}")
    print(f"  平均矩形/帧  : {total_rect_count / max(actual_frames, 1):.1f}")
    print(f"  输出大小     : {out_size:.1f} KB")
    print(f"  处理时间     : {elapsed:.1f} s  ({actual_frames/elapsed:.1f} fps)")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='WhiteRectFitter v3 预处理器：视频 → boxes.bin',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('input',         help='输入视频文件（mp4/avi/mov/mkv…）')
    ap.add_argument('--out', '-o',   default='boxes.bin',
                    help='输出文件路径（默认 boxes.bin）')
    ap.add_argument('--width', '-W', type=int, default=256,
                    help='分析宽度（默认 64，越大越精确但越慢）')
    ap.add_argument('--max-rects',   type=int, default=2048,
                    help='每帧最大矩形数（默认 150）')
    ap.add_argument('--thresh', '-t', type=int, default=200,
                    help='白色亮度阈值 0-255（默认 200）')
    args = ap.parse_args()

    print("WhiteRectFitter v3 预处理器")
    print("=" * 48)
    preprocess(args.input, args.out, args.width, args.max_rects, args.thresh)


if __name__ == '__main__':
    main()
