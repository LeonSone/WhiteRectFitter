# WhiteRectFitter

将黑白视频的白色区域实时映射为 Windows 桌面上的无边框白色窗口。

灵感来自 [bad_apple_virus](https://github.com/mon/bad_apple_virus)（Rust），本项目提供 C++ 和 Python 两套实现。

---

## 快速开始

以下命令均在项目根目录下执行，路径为相对路径。

```bash
# Python 版
pip install -r requirements.txt
python launcher.py              # GUI 启动器

# C++ 版（双击 build\Release\WhiteRectFitter.exe 或命令行）
build\Release\WhiteRectFitter.exe                         # 无参数 → 启动 GUI 启动器
build\Release\WhiteRectFitter.exe --play data\boxes256.bin  # 命令行直接播放
```

---

## 功能特性

- **C++ 原生 GUI 启动器** — 双击 `build\Release\WhiteRectFitter.exe` 即可使用，暗色主题界面，支持播放/预处理两种模式切换、文件浏览、屏幕区域配置、质量预设选择
- **嵌入资源** — 可将 `boxes*.bin`、音频、视频编译进单个 exe，运行时自动提取，退出时清理，实现单文件分发
- **质量预设** — Low（128px）、Medium（256px）、High（512px）三档预设，对应预计算的 boxes 数据
- **音频同步** — C++ 播放器通过 MCI 播放 MP3，使用 `QueryPerformanceCounter` 高精度计时，自动检测音画漂移并跳帧/追赶
- **DPI 感知** — 使用 Per-Monitor V2 DPI 感知，高分辨率屏幕下正确渲染
- **免责声明** — 首次启动弹出资源占用提示，用户确认后方可运行

---

## 项目结构

```text
WhiteRectFitter/
├── CMakeLists.txt              C++ 构建配置
├── requirements.txt            Python 依赖
├── launcher.py                 Python GUI 启动器
│
├── src/                        C++ 源码
│   ├── main.cpp                全部实现（GUI + 预处理 + 播放）
│   ├── resource.h              嵌入资源 ID 定义
│   └── wrf.rc.in               CMake 资源编译模板
│
├── python/                     Python 实现
│   ├── preprocess.py           预处理器（视频 → boxes.bin）
│   ├── player.py               播放器（Tkinter + Win32 DeferWindowPos）
│   └── wrf/                    共享库
│       ├── constants.py        二进制格式常量
│       ├── boxes.py            boxes.bin 读写
│       └── win32.py            Win32 窗口池绑定
│
├── data/                       预计算数据
│   ├── boxes128.bin            低质量（128px）
│   ├── boxes256.bin            中质量（256px）
│   └── boxes512.bin            高质量（512px）
│
├── bad_apple.mp3               音频（gitignored）
└── bad_apple.mp4               源视频（gitignored）
```

**设计原则：预处理与播放完全解耦。** 预处理离线运行一次生成 `boxes.bin`，播放时直接读取，零计算开销。

---

## 使用说明

### Python

```bash
# 预处理
python python/preprocess.py bad_apple.mp4 --out data\boxes.bin --width 256 --max-rects 2048 --thresh 200

# 播放器（GUI）
python python/player.py

# GUI 启动器（配置参数后调用播放器/预处理器）
python launcher.py
```

### C++ — GUI 模式

```bash
build\Release\WhiteRectFitter.exe                       # 无参数，启动 GUI 启动器
```

在 GUI 中可选择：

- **Play 模式**：选择 boxes.bin 文件、音频文件、屏幕区域、质量预设
- **Preprocess 模式**：选择输入视频、输出路径、分析宽度、最大矩形数、阈值

### C++ — 命令行模式

```bash
# 播放
build\Release\WhiteRectFitter.exe --play data\boxes256.bin
build\Release\WhiteRectFitter.exe --play data\boxes512.bin --audio bad_apple.mp3
build\Release\WhiteRectFitter.exe --play data\boxes256.bin --sx 0 --sy 0 --sw 1920 --sh 1080

# 预处理（需 OpenCV）
build\Release\WhiteRectFitter.exe --preprocess bad_apple.mp4 --out data\boxes.bin --width 64 --max-rects 150 --thresh 200
```

### 参数说明

| 参数 | 默认值 | 说明 |
| ---- | ---- | ---- |
| `--width` | 256 | 分析宽度，越大越精确但越慢 |
| `--max-rects` | 2048 | 每帧最大矩形数 |
| `--thresh` | 200 | 白色亮度阈值（0-255） |
| `--audio` | — | 音频文件路径（MP3） |
| `--sx` / `--sy` | 0 | 屏幕映射区域左上角坐标 |
| `--sw` / `--sh` | 屏幕尺寸 | 屏幕映射区域宽高 |

---

## 编译

**依赖：** Visual Studio 2022+ / MSVC 工具链、CMake 3.20+、（可选）OpenCV 4.x

```bash
# 默认构建（GUI 子系统，无控制台窗口）
cmake -B build
cmake --build build --config Release

# 控制台模式（调试用，显示 printf 输出）
cmake -B build -DCONSOLE_BUILD=ON
cmake --build build --config Release

# 使用 vcpkg 的 OpenCV
vcpkg install opencv4:x64-windows
cmake -B build
cmake --build build --config Release

# 手动指定 OpenCV 路径
cmake -B build -DOpenCV_DIR="D:/OpenCV/BuildStatic"
cmake --build build --config Release

# 不使用 OpenCV（仅播放器，无预处理功能）
cmake -B build -DWITH_OPENCV=OFF
cmake --build build --config Release
```

### 构建选项

| 选项 | 默认值 | 说明 |
| ---- | ---- | ---- |
| `CONSOLE_BUILD` | `OFF` | `ON`：控制台子系统（`wmain` 入口，有控制台窗口） |
| | | `OFF`：GUI 子系统（`wWinMain` 入口，无控制台） |
| `WITH_OPENCV` | `ON` | 启用 `--preprocess` 模式，关闭后仅 `--play` 可用 |

### 嵌入资源

构建时，CMake 会将 `data/boxes*.bin`、`bad_apple.mp3`、`bad_apple.mp4` 作为 Win32 RCDATA 资源编译进 exe。运行时自动提取到 exe 所在目录，退出时清理。`boxes512.bin` 按需提取（选择 High 质量时才解压）。

---

## 技术细节

### 算法：贪心最大矩形

1. 对白色区域维护列高直方图 `hist[x]`
2. 单调栈 O(W) 求直方图中最大矩形
3. 记录矩形，将对应区域涂黑
4. 重复直到达到 `max-rects` 或面积过小

每个矩形内所有像素均为白色，用最少矩形覆盖最大白色面积。

### boxes.bin 二进制格式

```text
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

### 播放性能优化

- 窗口一次性预分配，非逐帧创建
- 脏标记系统（`DeferredWindow`），跳过未变化的窗口
- `BeginDeferWindowPos` / `EndDeferWindowPos` 批量提交窗口位置变更
- 播放时零分析开销（预处理已完成）

### 音频同步

C++ 播放器使用 Windows MCI 子系统播放 MP3，每帧通过 `MCI_STATUS` 查询音频播放位置，与视频帧时间戳比对：

- 音频领先超过 1 帧 → 跳帧追赶
- 视频领先超过 1 帧 → 跳过本次渲染
- 保持音画同步，无累积漂移

### 性能参考

| 指标 | 典型值 |
| ---- | ---- |
| 预处理（Python，64×48） | ~2-5 帧/秒 |
| 预处理（C++，64×48） | ~200-500 帧/秒 |
| 播放延迟（Python） | < 5ms/帧 |
| 播放延迟（C++） | < 1ms/帧 |
| DeferWindowPos 批量提交 150 窗口 | ~2-5ms |

---

## 许可

MIT License
