# WhiteRectFitter v3

> 仿照 [bad_apple_virus](https://github.com/mon/bad_apple_virus) 的架构重写
> 将黑白视频的白色区域实时映射为 Windows 桌面无边框白色窗口

---

## 架构对照

| bad_apple_virus (Rust) | WhiteRectFitter v3 |
| --- | --- |
| `bad apple.py`（Python 预处理） | `python/preprocess.py`（Python 预处理） |
| `src/main.rs`（Rust 播放器） | `src/main.cpp`（C++ 播放器） |
| *(仅 Rust)* | `python/player.py`（Python 播放器） |
| `assets/boxes.bin`（预计算数据） | `data/*.bin`（预计算数据） |

**关键设计原则：分析与播放完全解耦。**
`preprocess.py` / `wrf.exe --preprocess` 离线运行一次；
`player.py` / `wrf.exe --play` 播放时零分析，直接读内存。

---

## 项目结构

```text
WhiteRectFitter v3/
├── preprocess.py           ← thin wrapper，调用 python/preprocess.py
├── player.py               ← thin wrapper，调用 python/player.py
│
├── python/                 Python 代码
│   ├── preprocess.py       离线预处理器（视频 → boxes.bin）
│   ├── player.py           实时播放器（GUI + 后台线程）
│   └── wrf/                共享库
│       ├── constants.py    二进制格式常量
│       ├── boxes.py        BoxesFile 读写
│       └── win32.py        Win32 DeferWindowPos 绑定 + 窗口池
│
├── src/
│   └── main.cpp            C++ 预处理器 + 播放器（单文件）
│
├── data/
│   └── my_boxes256.bin     预计算的矩形数据（gitignored）
│
├── CMakeLists.txt          C++ 构建配置
├── requirements.txt        Python 依赖
└── README.md
```

### Python 包说明

| 模块 | 职责 |
| --- | --- |
| `wrf/constants` | `MAGIC`、`HEADER_FMT`、`COORD_FMT` 等二进制格式常量 |
| `wrf/boxes` | `BoxesFile` 类（读取 boxes.bin）、`write_header`/`write_frame`（写入） |
| `wrf/win32` | Win32 ctypes 绑定、`WinState` 脏标记、`WindowPool` 窗口池 |
| `preprocess` | 贪心最大矩形算法 + CLI 入口 |
| `player` | `Player` 播放控制器 + `App` Tkinter GUI |

---

## 卡顿根因分析（v2.2 → v3 改进）

| 问题 | v2.2 | v3 |
| --- | --- | --- |
| 算法位置 | **每帧实时**运行 `decompose_greedy` | **离线预处理**，播放时只做内存索引 |
| 窗口创建 | 每帧 `apply()` 内反复 `root.update()` | 加载文件后**一次性**调用 |
| 脏标记 | 无，所有窗口全量提交 | **WinState** 位置/尺寸/可见性脏标记 |
| 线程切换 | `root.after(0, ...)` **每帧**调度 | `_w32_batch()` **直接调用 Win32**，每 30 帧才 `after()` 一次 |
| HWND | `winfo_id()`（子组件句柄） | `wm_frame()`（顶层 OS 句柄） |
| 显隐 flag | 统一加 `SWP_HIDEWINDOW`（残影） | 显隐时**去掉** `SWP_NOREDRAW` |

---

## 使用流程

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 可选音频支持
pip install pygame
```

### 2. 预处理（一次性，离线）

```bash
# Python 版本
python preprocess.py bad_apple.mp4

# 自定义参数
python preprocess.py input.mp4 \
    --out boxes.bin \
    --width 256 \          # 分析宽度（越大越精确但越慢）
    --max-rects 2048 \     # 每帧最大矩形数
    --thresh 200           # 白色阈值（0-255）

# C++ 版本（需要编译 + OpenCV）
wrf.exe --preprocess bad_apple.mp4 --out boxes.bin
```

### 3. 播放

```bash
# Python 播放器（GUI）
python player.py
# → 在界面中选择 boxes.bin 和（可选）音频文件

# C++ 播放器（命令行）
wrf.exe --play boxes.bin
wrf.exe --play boxes.bin --audio "bad apple.mp3"
wrf.exe --play boxes.bin --sx 0 --sy 0 --sw 1920 --sh 1080
```

---

## 编译 C++ 版本

### 前提条件

- Visual Studio 2022 或 MSVC 工具链
- CMake 3.20+
- （可选）OpenCV 4.x（用于 `--preprocess` 模式）

### 使用 vcpkg（推荐）

```bash
vcpkg install opencv4:x64-windows
cmake -B build
cmake --build build --config Release
```

### 手动指定 OpenCV

```bash
cmake -B build -DOpenCV_DIR="D:/OpenCV/Build"
cmake --build build --config Release
```

### 不使用 OpenCV（仅播放器）

```bash
cmake -B build -DWITH_OPENCV=OFF
cmake --build build --config Release
```

---

## boxes.bin 二进制格式

```
Header (16 bytes, little-endian):
  char[4]    magic        = "WRF2"
  uint16_t   base_w       分析宽度（坐标空间上界）
  uint16_t   base_h       分析高度
  float32    fps
  uint32_t   total_frames

Body（逐帧，变长）:
  [x:u16  y:u16  w:u16  h:u16] × N    ← N 个矩形（N ≥ 0）
  [0:u16  0:u16  0:u16  0:u16]         ← 帧分隔符（w=h=0）
```

坐标系为 `[0, base_w) × [0, base_h)`，播放时按比例缩放至屏幕。

---

## 贪心最大矩形算法

与 bad_apple_virus 的 Python 脚本同类算法：

1. 对工作掩码（白=1）维护列高直方图 `hist[x]`
2. 单调栈 O(W) 求当前行直方图中最大矩形
3. 记录该矩形，将对应区域置 0（涂黑）
4. 重复至 `max_rects` 次或面积 < 4

**保证**：每个矩形内所有像素均为白色。
**效果**：用最少的矩形覆盖最大的白色面积。

---

## 性能参考

| 指标 | 典型值 |
| --- | --- |
| 预处理速度（Python，64×48） | ~2-5 帧/秒 |
| 预处理速度（C++，64×48） | ~200-500 帧/秒 |
| 播放延迟（Python player） | < 5ms/帧（无分析） |
| 播放延迟（C++ player） | < 1ms/帧 |
| DeferWindowPos 批量提交 150 窗口 | ~2-5ms |

---

## 许可

MIT License
