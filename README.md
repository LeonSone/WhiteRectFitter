# WhiteRectFitter

将黑白视频的白色区域实时映射为 Windows 桌面上的无边框白色窗口。

灵感来自 [bad_apple_virus](https://github.com/mon/bad_apple_virus)（Rust），本项目提供 C++ 和 Python 两套实现。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 预处理（离线，运行一次）
python preprocess.py bad_apple.mp4

# 3. 播放
python player.py              # Python GUI
wrf.exe --play boxes.bin      # C++ 命令行
```

---

## 项目结构

```text
WhiteRectFitter/
├── preprocess.py           ← 入口：预处理（调用 python/preprocess.py）
├── player.py               ← 入口：播放器（调用 python/player.py）
├── launcher.py             ← 入口：GUI 启动器（配置参数启动 wrf.exe）
│
├── python/
│   ├── preprocess.py       预处理器（视频 → boxes.bin）
│   ├── player.py           播放器（Tkinter + Win32 DeferWindowPos）
│   └── wrf/                共享库
│       ├── constants.py    二进制格式常量
│       ├── boxes.py        boxes.bin 读写
│       └── win32.py        Win32 窗口池绑定
│
├── src/
│   └── main.cpp            C++ 实现（预处理 + 播放，单文件）
│
├── CMakeLists.txt          C++ 构建配置
├── requirements.txt        Python 依赖
└── run.bat                 便捷启动脚本
```

**设计原则：预处理与播放完全解耦。** 预处理离线运行一次生成 `boxes.bin`，播放时直接读取，零计算开销。

---

## 使用说明

### 预处理

```bash
# Python 版
python preprocess.py input.mp4 --out boxes.bin --width 256 --max-rects 2048 --thresh 200

# C++ 版（需编译 + OpenCV）
wrf.exe --preprocess input.mp4 --out boxes.bin --width 256
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--width` | 256 | 分析宽度，越大越精确但越慢 |
| `--max-rects` | 2048 | 每帧最大矩形数 |
| `--thresh` | 200 | 白色亮度阈值（0-255） |

### 播放

```bash
# Python 播放器（GUI，可选 pygame 音频）
python player.py

# C++ 播放器
wrf.exe --play boxes.bin
wrf.exe --play boxes.bin --audio "bad apple.mp3"
wrf.exe --play boxes.bin --sx 0 --sy 0 --sw 1920 --sh 1080
```

---

## 编译 C++ 版本

**依赖：** Visual Studio 2022 / MSVC 工具链、CMake 3.20+、（可选）OpenCV 4.x

```bash
# 使用 vcpkg（推荐）
vcpkg install opencv4:x64-windows
cmake -B build
cmake --build build --config Release

# 手动指定 OpenCV
cmake -B build -DOpenCV_DIR="D:/OpenCV/Build"
cmake --build build --config Release

# 不使用 OpenCV（仅播放器）
cmake -B build -DWITH_OPENCV=OFF
cmake --build build --config Release
```

---

## 技术细节

### 算法：贪心最大矩形

1. 对白色区域维护列高直方图 `hist[x]`
2. 单调栈 O(W) 求直方图中最大矩形
3. 记录矩形，将对应区域涂黑
4. 重复直到达到 `max_rects` 或面积过小

每个矩形内所有像素均为白色，用最少矩形覆盖最大白色面积。

### boxes.bin 二进制格式

```
Header (16 bytes, little-endian):
  char[4]    magic        = "WRF2"
  uint16_t   base_w       分析宽度
  uint16_t   base_h       分析高度
  float32    fps
  uint32_t   total_frames

Body（逐帧，变长）:
  [x:u16  y:u16  w:u16  h:u16] × N    ← N 个矩形
  [0:u16  0:u16  0:u16  0:u16]         ← 帧分隔符
```

坐标空间为 `[0, base_w) × [0, base_h)`，播放时按比例缩放至屏幕。

### 性能优化

- 预处理离线完成，播放时零分析开销
- 窗口一次性预分配，非逐帧创建
- WinState 脏标记系统，跳过未变化的窗口
- 直接调用 Win32 `DeferWindowPos` 批量提交，绕过 Tkinter 事件循环
- 使用 `wm_frame()` 获取真实 OS 窗口句柄

### 性能参考

| 指标 | 典型值 |
|------|--------|
| 预处理（Python，64×48） | ~2-5 帧/秒 |
| 预处理（C++，64×48） | ~200-500 帧/秒 |
| 播放延迟（Python） | < 5ms/帧 |
| 播放延迟（C++） | < 1ms/帧 |
| DeferWindowPos 批量提交 150 窗口 | ~2-5ms |

---

## 许可

MIT License
