"""WhiteRectFitter v3 — boxes.bin 二进制格式读写"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Tuple

from .constants import MAGIC, HEADER_FMT, HEADER_SIZE, COORD_FMT, COORD_SIZE, FRAME_SEP


def write_header(f, base_w: int, base_h: int, fps: float, total_frames: int):
    f.write(struct.pack(HEADER_FMT, MAGIC, base_w, base_h, fps, total_frames))


def write_frame(f, rects: List[Tuple[int, int, int, int]]):
    for x, y, w, h in rects:
        f.write(struct.pack(COORD_FMT, x, y, w, h))
    f.write(FRAME_SEP)


class BoxesFile:
    """将 boxes.bin 全量加载进内存，O(1) 随机帧访问。"""

    def __init__(self):
        self.base_w: int   = 0
        self.base_h: int   = 0
        self.fps:    float = 30.0
        self.total:  int   = 0
        # frames[i] = list of (x, y, w, h) in base_w × base_h space
        self.frames: List[List[Tuple[int,int,int,int]]] = []

    def load(self, path: str) -> bool:
        try:
            data = Path(path).read_bytes()
        except OSError:
            return False

        if len(data) < HEADER_SIZE or data[:4] != MAGIC:
            return False

        magic, bw, bh, fps, total = struct.unpack_from(HEADER_FMT, data, 0)
        self.base_w = bw
        self.base_h = bh
        self.fps    = fps
        self.total  = total

        # 解析 Body
        pos    = HEADER_SIZE
        frames = []
        cur    = []
        while pos + COORD_SIZE <= len(data):
            x, y, w, h = struct.unpack_from(COORD_FMT, data, pos)
            pos += COORD_SIZE
            if w == 0 and h == 0:
                frames.append(cur)
                cur = []
            else:
                cur.append((x, y, w, h))
        if cur:
            frames.append(cur)

        self.frames = frames
        return True

    @property
    def max_rects(self) -> int:
        if not self.frames:
            return 0
        return max(len(f) for f in self.frames)
