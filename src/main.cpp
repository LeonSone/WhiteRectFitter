/*
 * main.cpp  —  WhiteRectFitter v3  (C++)
 * 仿照 bad_apple_virus/src/main.rs  重写
 *
 * 编译（需要 OpenCV 4.x + Windows SDK）：
 *   cmake -B build -DCMAKE_BUILD_TYPE=Release
 *   cmake --build build --config Release
 *
 * 用法：
 *   wrf.exe --preprocess input.mp4 [--out boxes.bin] [--width 64] [--max-rects 150]
 *   wrf.exe --play boxes.bin [--audio music.mp3]
 *
 * ─── 与 main.rs 的对应关系 ────────────────────────────────────────
 *
 *  Rust                                  C++
 *  ────────────────────────────────────  ──────────────────────────────────
 *  WinCoords { x:u8, y:u8, w:u8, h:u8 } WinCoords { x,y,w,h : uint16_t }
 *  DeferredWindow                        DeferredWindow
 *  WindowCollection::draw()             WindowCollection::flush()
 *  register_window_class()              register_class()
 *  (0..MAX_WINDOWS).map(DeferredWindow) WindowCollection::preallocate()
 *  frames_iter / SetTimer loop          playback_loop() / SetTimer + PeekMessage
 *  kira audio clock                     MCI audio + QueryPerformanceCounter
 *  include_bytes_zstd!("boxes.bin")     load_boxes_bin() from disk
 *  bad apple.py (Python preprocess)     --preprocess mode (C++ greedy maxrect)
 */

//#define WIN32_LEAN_AND_MEAN
//#define NOMINMAX

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00   // 启用 Windows 10 及更高版本的 API
#endif
#include <windows.h>
#include <shellapi.h>     
#include <mmsystem.h>     // MCI audio
#pragma comment(lib, "winmm.lib")

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include <chrono>
#include <memory>
#include <stack>
#include <string>
#include <vector>
#include <fcntl.h>
#include <io.h>
// OpenCV — 仅 --preprocess 模式需要
#ifdef WITH_OPENCV
#  include <opencv2/opencv.hpp>
#endif

// ═══════════════════════════════════════════════════════════════════
// § 0  二进制格式（与 Python 共享）
// ═══════════════════════════════════════════════════════════════════

#pragma pack(push, 1)
struct BoxHeader {
    char     magic[4];      // "WRF2"
    uint16_t base_w;
    uint16_t base_h;
    float    fps;
    uint32_t total_frames;
};
static_assert(sizeof(BoxHeader) == 16, "BoxHeader must be 16 bytes");

struct WinCoords {
    uint16_t x, y, w, h;
    // w==0 && h==0 → 帧分隔符
    bool is_sep() const { return w == 0 && h == 0; }
};
static_assert(sizeof(WinCoords) == 8, "WinCoords must be 8 bytes");
#pragma pack(pop)

// ═══════════════════════════════════════════════════════════════════
// § 1  boxes.bin I/O
// ═══════════════════════════════════════════════════════════════════

struct Frame {
    std::vector<WinCoords> rects;
};

struct BoxData {
    BoxHeader          hdr;
    std::vector<Frame> frames;
    size_t             max_rects = 0;

    bool load(const char* path) {
        FILE* f = fopen(path, "rb");
        if (!f) { fprintf(stderr, "[错误] 无法打开 %s\n", path); return false; }

        if (fread(&hdr, sizeof(hdr), 1, f) != 1) { fclose(f); return false; }
        if (memcmp(hdr.magic, "WRF2", 4) != 0) {
            fprintf(stderr, "[错误] 文件格式不正确（magic 不匹配）\n");
            fclose(f);
            return false;
        }

        Frame cur;
        WinCoords c;
        while (fread(&c, sizeof(c), 1, f) == 1) {
            if (c.is_sep()) {
                if (cur.rects.size() > max_rects) max_rects = cur.rects.size();
                frames.push_back(std::move(cur));
                cur.rects.clear();
            } else {
                cur.rects.push_back(c);
            }
        }
        if (!cur.rects.empty()) {
            if (cur.rects.size() > max_rects) max_rects = cur.rects.size();
            frames.push_back(std::move(cur));
        }

        fclose(f);
        printf("  base: %u×%u  fps: %.2f  帧数: %zu  max_rects/帧: %zu\n",
               hdr.base_w, hdr.base_h, hdr.fps, frames.size(), max_rects);
        return true;
    }
};

// ═══════════════════════════════════════════════════════════════════
// § 2  DeferredWindow（仿 main.rs DeferredWindow）
// ═══════════════════════════════════════════════════════════════════

// 白色窗口类名
static const wchar_t* WND_CLASS = L"WRF_White";

struct DeferredWindow {
    HWND hwnd  = nullptr;
    int  x = 0, y = 0, w = 1, h = 1;
    bool visible     = false;
    bool pos_stale   = false;
    bool sz_stale    = false;
    bool vis_stale   = false;

    void set_pos(int nx, int ny) {
        pos_stale = pos_stale || (nx != x || ny != y);
        x = nx; y = ny;
    }
    void set_sz(int nw, int nh) {
        sz_stale = sz_stale || (nw != w || nh != h);
        w = nw; h = nh;
    }
    void set_visible(bool v) {
        vis_stale = vis_stale || (v != visible);
        visible   = v;
    }
    bool stale() const { return pos_stale || sz_stale || vis_stale; }

    // 仿 DeferredWindow::draw() in main.rs
    HDWP enqueue(HDWP hdwp) {
        if (!stale() || !hdwp) return hdwp;
        UINT flags = SWP_NOZORDER | SWP_NOACTIVATE;
        bool is_first_show = vis_stale && visible; // 标记是否为首次显示
        if (vis_stale) {
            // 显隐变化：不加 SWP_NOREDRAW（否则内容残影）
            flags |= visible ? SWP_SHOWWINDOW
                            : (SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE);
        } else {
            // 仅移动时加 SWP_NOREDRAW；大小改变时必须重绘，否则新区域空白
            if (!sz_stale) {
                flags |= SWP_NOREDRAW;
            }
            if (!pos_stale) flags |= SWP_NOMOVE;
            if (!sz_stale)  flags |= SWP_NOSIZE;
        }
        hdwp = DeferWindowPos(hdwp, hwnd,
                            HWND_TOPMOST, x, y, w, h, flags);
        pos_stale = sz_stale = vis_stale = false;
        
        // 优化：首次显示时强制重绘整个窗口，确保背景完整填充
        if (is_first_show) {
            InvalidateRect(hwnd, nullptr, TRUE); // nullptr表示重绘整个窗口，TRUE表示擦除背景
        }
        
        return hdwp;
    }
};

// ═══════════════════════════════════════════════════════════════════
// § 3  WindowCollection（仿 main.rs WindowCollection）
// ═══════════════════════════════════════════════════════════════════

static HBRUSH g_white_brush = nullptr;

// 无边框全白窗口的消息处理器
static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg,
                                  WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_ERASEBKGND:
        return 1;   // 阻止默认擦除，我们在 WM_PAINT 中绘制
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC hdc = BeginPaint(hwnd, &ps);
        FillRect(hdc, &ps.rcPaint, g_white_brush);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_CLOSE:
        // 阻止关闭单个白窗口
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static void register_class(HINSTANCE inst)
{
    g_white_brush = CreateSolidBrush(RGB(255, 255, 255));

    WNDCLASSEXW wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = wnd_proc;
    wc.hInstance     = inst;
    wc.lpszClassName = WND_CLASS;
    wc.hbrBackground = g_white_brush;
    wc.style         = CS_HREDRAW | CS_VREDRAW;
    RegisterClassExW(&wc);
}

struct WindowCollection {
    std::vector<DeferredWindow> wins;

    void preallocate(HINSTANCE inst, int n) {
        wins.reserve(n);
        for (int i = 0; i < n; ++i) {
            DeferredWindow dw;
            // WS_POPUP = 无边框；WS_EX_TOOLWINDOW = 不在任务栏；
            // WS_EX_NOACTIVATE = 点击不抢焦点；WS_EX_TOPMOST = 置顶
            dw.hwnd = CreateWindowExW(
                WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                WND_CLASS, L"",
                WS_POPUP,       // ← 无标题栏/边框，无需 FUDGE_X/Y
                -1000, -1000, 1, 1,
                nullptr, nullptr, inst, nullptr
            );
            assert(dw.hwnd != nullptr);
            wins.push_back(dw);
        }
        printf("  已创建 %d 个白色窗口\n", n);
    }

    // 仿 WindowCollection::draw() — 批量原子提交
    void flush() {
        int stale_count = 0;
        for (auto& dw : wins) stale_count += dw.stale() ? 1 : 0;
        if (stale_count == 0) return;

        HDWP hdwp = BeginDeferWindowPos(stale_count);
        if (!hdwp) return;

        for (auto& dw : wins) {
            if (dw.stale()) {
                hdwp = dw.enqueue(hdwp);
                if (!hdwp) return;
            }
        }

        EndDeferWindowPos(hdwp);
    }

    void apply_frame(const Frame& frame,
                    int base_w, int base_h,
                    int sx, int sy, int sw, int sh)
    {
        float rx = (float)sw / base_w;
        float ry = (float)sh / base_h;
        size_t n = frame.rects.size() < wins.size() ? frame.rects.size() : wins.size();
        
        for (size_t i = 0; i < n; ++i) {
            const WinCoords& c = frame.rects[i];
            
            // 修复1：用 (int)(x + 0.5f) 代替 std::round 实现四舍五入
            int new_x = sx + (int)(c.x * rx + 0.5f);
            int new_y = sy + (int)(c.y * ry + 0.5f);
            int new_w = (int)(c.w * rx + 0.5f);
            int new_h = (int)(c.h * ry + 0.5f);
            
            // 修复2：用简单的三元运算符代替 std::max
            new_w = new_w > 1 ? new_w : 1;
            new_h = new_h > 1 ? new_h : 1;
            
            wins[i].set_pos(new_x, new_y);
            wins[i].set_sz(new_w, new_h);
            wins[i].set_visible(true);
        }
        
        for (size_t i = n; i < wins.size(); ++i) {
            wins[i].set_visible(false);
        }
    }

    void hide_all() {
        for (auto& dw : wins) dw.set_visible(false);
        flush();
    }
};

// ═══════════════════════════════════════════════════════════════════
// § 4  播放逻辑（仿 main.rs 主循环）
// ═══════════════════════════════════════════════════════════════════

static volatile size_t g_next_frame = 0;
static volatile bool   g_running    = false;
static BoxData*        g_boxes      = nullptr;
static WindowCollection* g_wc       = nullptr;
static MCIDEVICEID     g_audio_dev  = 0;

// 高精度计时
inline double now_sec() {
    LARGE_INTEGER freq, cnt;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&cnt);
    return (double)cnt.QuadPart / freq.QuadPart;
}

// 获取 MCI 音频当前位置（毫秒）
static DWORD mci_pos_ms() {
    if (!g_audio_dev) return 0;
    MCI_STATUS_PARMS st = {};
    st.dwItem = MCI_STATUS_POSITION;
    if (mciSendCommand(g_audio_dev, MCI_STATUS,
                       MCI_STATUS_ITEM, (DWORD_PTR)&st) == 0)
        return (DWORD)st.dwReturn;
    return 0;
}

static void open_audio(const wchar_t* path) {
    wchar_t cmd[512];
    swprintf_s(cmd, L"open \"%ls\" type mpegvideo alias wrf_audio", path); // ✅ %ls
    DWORD res = mciSendStringW(cmd, nullptr, 0, nullptr);
    if (res != 0) {
        wchar_t err[256] = {};
        mciGetErrorStringW(res, err, 256);
        fprintf(stderr, "[音频] MCI open 失败: %ls\n", err);
        return;
    }
    g_audio_dev = mciGetDeviceIDW(L"wrf_audio");
    printf("  MCI 音频已打开 (id=%u)\n", g_audio_dev);
}

static void play_audio() {
    if (!g_audio_dev) return;
    mciSendStringW(L"play wrf_audio from 0", nullptr, 0, nullptr);
}

static void stop_audio() {
    if (!g_audio_dev) return;
    mciSendStringW(L"stop wrf_audio", nullptr, 0, nullptr);
    mciSendStringW(L"close wrf_audio", nullptr, 0, nullptr);
    g_audio_dev = 0;
}

// WM_TIMER 回调（仿 main.rs `else if msg.message == WM_TIMER`）
static void on_timer(BoxData& boxes, WindowCollection& wc,
                     int sx, int sy, int sw, int sh)
{
    if (!g_running) return;

    size_t idx = g_next_frame;
    if (idx >= boxes.frames.size()) {
        g_running = false;
        wc.hide_all();
        printf("\n[播放完毕]\n");
        return;
    }

    // 音频同步
    if (g_audio_dev) {
        DWORD pos_ms   = mci_pos_ms();
        size_t target  = (size_t)(pos_ms / 1000.0 * boxes.hdr.fps);

        // 跑快了：跳过本次 tick
        if (idx > target + 1) return;

        // 跑慢了：跳帧
        if (target > idx + 1) {
            idx = std::min(target, boxes.frames.size() - 1);
            g_next_frame = idx;
        }
    }

    wc.apply_frame(boxes.frames[idx],
                   boxes.hdr.base_w, boxes.hdr.base_h,
                   sx, sy, sw, sh);
    wc.flush();

    if (idx % 30 == 0) {
        printf("\r  帧 %zu / %zu  窗口 %zu",
               idx, boxes.frames.size(),
               boxes.frames[idx].rects.size());
        fflush(stdout);
    }

    g_next_frame = idx + 1;
}

// ═══════════════════════════════════════════════════════════════════
// § 5  --preprocess 模式（贪心最大矩形）
// ═══════════════════════════════════════════════════════════════════

#ifdef WITH_OPENCV

// 经典单调栈：O(W) 求直方图中最大矩形
struct MaxRect { int x, y, w, h; };

static MaxRect max_in_histogram(const int* hist, int W, int row_bottom)
{
    MaxRect best = {};
    int best_area = 0;
    std::stack<int> stk;

    for (int i = 0; i <= W; ++i) {
        int v = (i < W) ? hist[i] : 0;
        while (!stk.empty() && hist[stk.top()] > v) {
            int idx  = stk.top(); stk.pop();
            int ht   = hist[idx];
            int left = stk.empty() ? -1 : stk.top();
            int wt   = i - left - 1;
            int area = ht * wt;
            if (area > best_area) {
                best_area = area;
                best = { left + 1, row_bottom - ht + 1, wt, ht };
            }
        }
        stk.push(i);
    }
    return best;
}

static std::vector<WinCoords>
decompose_frame(cv::Mat& gray_small, int thresh, int max_rects)
{
    int H = gray_small.rows, W = gray_small.cols;
    cv::Mat work;
    cv::threshold(gray_small, work, thresh, 1, cv::THRESH_BINARY);
    work.convertTo(work, CV_32S);   // 列高直方图需要累加

    std::vector<WinCoords> rects;
    std::vector<int> hist(W, 0);

    for (int iter = 0; iter < max_rects; ++iter) {
        // 重建直方图
        std::fill(hist.begin(), hist.end(), 0);
        MaxRect global_best = {};
        int global_area = 0;

        for (int y = 0; y < H; ++y) {
            const int* row = work.ptr<int>(y);
            for (int x = 0; x < W; ++x)
                hist[x] = row[x] ? hist[x] + 1 : 0;

            MaxRect r = max_in_histogram(hist.data(), W, y);
            int area = r.w * r.h;
            if (area > global_area) { global_area = area; global_best = r; }
        }

        if (global_area < 4) break;

        WinCoords c = {
            (uint16_t)global_best.x, (uint16_t)global_best.y,
            (uint16_t)global_best.w, (uint16_t)global_best.h
        };
        rects.push_back(c);

        // 涂黑
        for (int r = global_best.y; r < global_best.y + global_best.h; ++r)
            memset(work.ptr<int>(r) + global_best.x,
                   0, global_best.w * sizeof(int));
    }
    return rects;
}

static bool do_preprocess(const char* input, const char* output,
                           int base_w, int max_rects, int thresh)
{
    cv::VideoCapture cap(input);
    if (!cap.isOpened()) {
        fprintf(stderr, "[错误] 无法打开视频: %s\n", input);
        return false;
    }

    float  fps   = (float)cap.get(cv::CAP_PROP_FPS);
    int    total = (int)cap.get(cv::CAP_PROP_FRAME_COUNT);
    int    vid_w = (int)cap.get(cv::CAP_PROP_FRAME_WIDTH);
    int    vid_h = (int)cap.get(cv::CAP_PROP_FRAME_HEIGHT);
    int    base_h = std::max(1, (int)std::round((double)base_w * vid_h / vid_w));

    printf("  %d×%d → %d×%d  %.2ffps  %d帧\n",
           vid_w, vid_h, base_w, base_h, fps, total);

    FILE* f = fopen(output, "wb");
    if (!f) { fprintf(stderr, "[错误] 无法写入 %s\n", output); return false; }

    BoxHeader hdr = {};
    memcpy(hdr.magic, "WRF2", 4);
    hdr.base_w       = (uint16_t)base_w;
    hdr.base_h       = (uint16_t)base_h;
    hdr.fps          = fps;
    hdr.total_frames = (uint32_t)total;
    fwrite(&hdr, sizeof(hdr), 1, f);

    WinCoords sep = { 0, 0, 0, 0 };
    int actual = 0;
    int max_r  = 0;

    cv::Mat frame, gray, small;
    auto t0 = std::chrono::steady_clock::now();

    while (cap.read(frame)) {
        cv::cvtColor(frame, gray, cv::COLOR_BGR2GRAY);
        cv::resize(gray, small, {base_w, base_h}, 0, 0, cv::INTER_AREA);

        auto rects = decompose_frame(small, thresh, max_rects);
        for (auto& c : rects) fwrite(&c, sizeof(c), 1, f);
        fwrite(&sep, sizeof(sep), 1, f);

        if ((int)rects.size() > max_r) max_r = (int)rects.size();
        ++actual;

        if (actual % 30 == 0) {
            float pct = actual * 100.0f / std::max(total, 1);
            auto  dt  = std::chrono::steady_clock::now() - t0;
            float sec = std::chrono::duration<float>(dt).count();
            printf("\r  [%.1f%%] %d/%d帧  %.1ffps  矩形=%d",
                   pct, actual, total, actual / std::max(sec, 1e-3f),
                   (int)rects.size());
            fflush(stdout);
        }
    }

    // 回填真实帧数
    fseek(f, 0, SEEK_SET);
    hdr.total_frames = (uint32_t)actual;
    fwrite(&hdr, sizeof(hdr), 1, f);
    fclose(f);

    printf("\n✓ 完成！%d帧  最大矩形/帧=%d\n", actual, max_r);
    return true;
}

#endif  // WITH_OPENCV

// ═══════════════════════════════════════════════════════════════════
// § 6  主播放窗口（Win32 消息循环，仿 main.rs 'outer loop）
// ═══════════════════════════════════════════════════════════════════

// 主窗口（用于接收 WM_TIMER + 用户关闭）
static const wchar_t* MAIN_CLASS = L"WRF_Main";

static LRESULT CALLBACK main_wnd_proc(HWND hwnd, UINT msg,
                                       WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CLOSE:
    case WM_DESTROY:
        g_running = false;
        PostQuitMessage(0);
        return 0;
    case WM_KEYDOWN:
        if (wp == VK_ESCAPE || wp == 'Q') {
            g_running = false;
            PostQuitMessage(0);
        }
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static void register_main_class(HINSTANCE inst) {
    WNDCLASSEXW wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = main_wnd_proc;
    wc.hInstance     = inst;
    wc.lpszClassName = MAIN_CLASS;
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    RegisterClassExW(&wc);
}

struct PlayArgs {
    std::string boxes_path;      // 改用 string 持有
    std::wstring audio_path;     // 改用 wstring 持有
    int sx, sy, sw, sh;
};

static int run_player(PlayArgs& args, HINSTANCE inst)
{
    BoxData boxes;
    if (!boxes.load(args.boxes_path.c_str())) return 1;

    WindowCollection wc;
    printf("  预分配窗口…\n");
    wc.preallocate(inst, (int)boxes.max_rects);

    // 打开并播放音频（如果提供了）
    if (!args.audio_path.empty()) {
        open_audio(args.audio_path.c_str());
        play_audio();
    }

    // 创建最小主控窗口（不可见，只用于消息循环）
    register_main_class(inst);
    HWND ctrl = CreateWindowExW(
        0, MAIN_CLASS, L"WRF Controller",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 300, 80,
        nullptr, nullptr, inst, nullptr
    );
    // 小型控制窗口，显示在右下角
    RECT wa; SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    SetWindowPos(ctrl, HWND_TOP, wa.right - 320, wa.bottom - 100,
                 300, 60, SWP_SHOWWINDOW);

    printf("  开始播放  ESC/Q 退出\n");

    g_boxes     = &boxes;
    g_wc        = &wc;
    g_next_frame = 0;
    g_running   = true;

    // 仿 SetTimer(None, 1, 16, None) in main.rs
    SetTimer(ctrl, 1, 16, nullptr);

    MSG msg = {};
    while (g_running) {
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) { g_running = false; break; }
            if (msg.message == WM_TIMER && msg.wParam == 1) {
                on_timer(boxes, wc, args.sx, args.sy, args.sw, args.sh);
            }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (!g_running) break;
        Sleep(1);
    }

    wc.hide_all();
    stop_audio();
    KillTimer(ctrl, 1);
    DestroyWindow(ctrl);
    printf("\n");
    return 0;
}
// ═══════════════════════════════════════════════════════════════════
// § 7  CLI 解析 & WinMain
// ═══════════════════════════════════════════════════════════════════

static std::string wcs_to_mbs(const wchar_t* wcs) {
    if (!wcs) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, wcs, -1, nullptr, 0, nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, wcs, -1, s.data(), n, nullptr, nullptr);
    if (!s.empty() && s.back() == '\0') s.pop_back();
    return s;
}

// 控制台输出（Windows Subsystem: Window 时也能写控制台）
static void attach_console() {
    if (AttachConsole(ATTACH_PARENT_PROCESS) || AllocConsole()) {
        // 重定向标准流
        freopen("CONOUT$", "w", stdout);
        freopen("CONOUT$", "w", stderr);
        freopen("CONIN$", "r", stdin);

        // 设置控制台输出代码页为 UTF-8
        SetConsoleOutputCP(CP_UTF8);
        // 可选：同时设置输入代码页，以便正确读取中文输入
        SetConsoleCP(CP_UTF8);

        // 更新 C 运行时流为无缓冲（避免与 Win32 控制台缓冲冲突）
        setvbuf(stdout, nullptr, _IONBF, 0);
        setvbuf(stderr, nullptr, _IONBF, 0);
    }
    
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, LPWSTR cmdline, int)
{
    attach_console();

    // 高 DPI 感知
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);

    if (argc < 2) {
        printf(
            "WhiteRectFitter v3 (C++)\n"
            "用法:\n"
            "  wrf.exe --preprocess input.mp4 [--out boxes.bin] [--width 64] [--max-rects 150] [--thresh 200]\n"
            "  wrf.exe --play boxes.bin [--audio music.mp3] [--sx 0] [--sy 0] [--sw W] [--sh H]\n"
        );
        LocalFree(argv);
        return 0;
    }

    std::string mode = wcs_to_mbs(argv[1]);

    // ── --play ──────────────────────────────────────────────────
    if (mode == "--play") {
        if (argc < 3) { fprintf(stderr, "用法: wrf.exe --play boxes.bin\n"); return 1; }

        register_class(inst);

        PlayArgs args = {};
        args.boxes_path = wcs_to_mbs(argv[2]);   // ✅ 直接赋值

        // 默认全屏映射
        RECT wa;
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
        args.sx = wa.left;   args.sy = wa.top;
        args.sw = wa.right - wa.left;
        args.sh = wa.bottom - wa.top;

        for (int i = 3; i < argc; ++i) {   // 注意：i < argc，不需要 -1
            std::string key = wcs_to_mbs(argv[i]);
            if (key == "--audio" && i + 1 < argc) {
                args.audio_path = argv[i + 1];
                ++i;
            }
            else if (key == "--sx" && i + 1 < argc) args.sx = _wtoi(argv[++i]);
            else if (key == "--sy" && i + 1 < argc) args.sy = _wtoi(argv[++i]);
            else if (key == "--sw" && i + 1 < argc) args.sw = _wtoi(argv[++i]);
            else if (key == "--sh" && i + 1 < argc) args.sh = _wtoi(argv[++i]);
        }

        printf("WhiteRectFitter v3  [播放模式]\n");
        printf("  boxes.bin : %s\n", args.boxes_path.c_str());
        printf("  映射区域  : (%d,%d) %d×%d\n", args.sx, args.sy, args.sw, args.sh);
        printf("  音频文件  : %ls\n", args.audio_path.empty() ? L"无" : args.audio_path.c_str());

        LocalFree(argv);
        return run_player(args, inst);
    }

    // ── --preprocess ────────────────────────────────────────────
#ifdef WITH_OPENCV
    if (mode == "--preprocess") {
        if (argc < 3) { fprintf(stderr, "用法: wrf.exe --preprocess input.mp4\n"); return 1; }

        std::string input     = wcs_to_mbs(argv[2]);
        std::string output    = "boxes.bin";
        int base_w   = 64;
        int max_rects = 150;
        int thresh   = 200;

        for (int i = 3; i < argc; ++i) {
            std::string key = wcs_to_mbs(argv[i]);
            if      (key == "--out"       ) output    = wcs_to_mbs(argv[++i]);
            else if (key == "--width"     ) base_w    = _wtoi(argv[++i]);
            else if (key == "--max-rects" ) max_rects = _wtoi(argv[++i]);
            else if (key == "--thresh"    ) thresh    = _wtoi(argv[++i]);
        }

        printf("WhiteRectFitter v3  [预处理模式]\n");
        printf("  输入: %s\n  输出: %s\n", input.c_str(), output.c_str());

        LocalFree(argv);
        return do_preprocess(input.c_str(), output.c_str(),
                              base_w, max_rects, thresh) ? 0 : 1;
    }
#else
    if (mode == "--preprocess") {
        fprintf(stderr, "[错误] 此编译版本未包含 OpenCV，不支持 --preprocess 模式。\n"
                        "       请使用 Python 版本: python preprocess.py input.mp4\n");
        LocalFree(argv);
        return 1;
    }
#endif

    fprintf(stderr, "[错误] 未知命令: %s\n", mode.c_str());
    LocalFree(argv);
    return 1;
}
