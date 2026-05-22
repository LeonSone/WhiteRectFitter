"""WhiteRectFitter — 二进制格式常量（与 main.cpp 共享）"""

import struct

MAGIC       = b'WRF2'
HEADER_FMT  = '<4sHHfI'   # magic base_w base_h fps total_frames
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 16 bytes
COORD_FMT   = '<HHHH'
COORD_SIZE  = struct.calcsize(COORD_FMT)    # 8 bytes
FRAME_SEP   = struct.pack(COORD_FMT, 0, 0, 0, 0)

assert HEADER_SIZE == 16
assert COORD_SIZE  == 8
