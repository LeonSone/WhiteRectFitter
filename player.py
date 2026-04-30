#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
player.py  —  WhiteRectFitter v3 播放器（Python）
仿照 bad_apple_virus/src/main.rs

读取 preprocess.py 生成的 boxes.bin，实时驱动 Win32 窗口。

核心优化（对比 WhiteRectFitter 卡顿根因）
─────────────────────────────────────────────────────────────────
① 播放时零计算
    boxes.bin 在启动时全量加载进内存，每帧只做内存索引。
    不再在主循环中运行任何图像分析。

② DeferWindowPos + 脏标记  (仿 DeferredWindow in main.rs)
    BeginDeferWindowPos → DeferWindowPos × N → EndDeferWindowPos
    仅提交 pos/size/visibility 发生变化的窗口（stale 标记）。
    移动时加 SWP_NOREDRAW，显隐切换时不加（避免残影）。

③ 窗口只创建一次  (仿 (0..MAX_WINDOWS).map(|_| DeferredWindow::new()))
    preallocate() 在加载文件时一次性调用，播放中不再创建窗口。

④ 播放线程直接调用 Win32
    _w32_batch() 经由 ctypes 直接调用 DeferWindowPos，
    不经过 Tkinter after() 调度，消除 per-frame UI 线程切换开销。
    UI 状态更新（时间、窗口数）每 30 帧才通过 after() 一次。

⑤ HWND = wm_frame()
    overrideredirect 窗口的真实系统句柄需通过 wm_frame() 获取，
    winfo_id() 返回的是 Tk 内部子组件句柄，DeferWindowPos 会失败。
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import struct
import sys
import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import List, Tuple, Optional

IS_WIN = sys.platform == 'win32'

# ─── 可选音频 ────────────────────────────────────────────────────
try:
    import pygame
    _PYGAME = True
except ImportError:
    _PYGAME = False

# ─── 二进制格式常量（与 preprocess.py / main.cpp 共享）────────────
MAGIC       = b'WRF2'
HEADER_FMT  = '<4sHHfI'
HEADER_SIZE = struct.calcsize(HEADER_FMT)
COORD_FMT   = '<HHHH'
COORD_SIZE  = struct.calcsize(COORD_FMT)

# ═══════════════════════════════════════════════════════════════════
# § 0  Win32 绑定（仿 main.rs 的 windows crate 调用）
# ═══════════════════════════════════════════════════════════════════

if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt

    _u32 = ctypes.windll.user32

    # BeginDeferWindowPos(nNumWindows) -> HDWP
    _BDP = _u32.BeginDeferWindowPos
    _BDP.argtypes = [ctypes.c_int]
    _BDP.restype  = ctypes.c_size_t

    # DeferWindowPos(hWinPosInfo, hWnd, hWndInsertAfter, x, y, cx, cy, uFlags) -> HDWP
    _DWP = _u32.DeferWindowPos
    _DWP.argtypes = [ctypes.c_size_t, wt.HWND, wt.HWND,
                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     ctypes.c_uint]
    _DWP.restype  = ctypes.c_size_t

    # EndDeferWindowPos(hWinPosInfo) -> BOOL
    _EDP = _u32.EndDeferWindowPos
    _EDP.argtypes = [ctypes.c_size_t]
    _EDP.restype  = wt.BOOL

    # SetWindowLongPtrW / GetWindowLongPtrW
    _ptr_bits = ctypes.sizeof(ctypes.c_void_p) * 8
    _SWL = (_u32.SetWindowLongPtrW if _ptr_bits == 64
            else _u32.SetWindowLongW)
    _SWL.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SWL.restype  = ctypes.c_ssize_t
    _GWL = (_u32.GetWindowLongPtrW if _ptr_bits == 64
            else _u32.GetWindowLongW)
    _GWL.argtypes = [wt.HWND, ctypes.c_int]
    _GWL.restype  = ctypes.c_ssize_t

    # SWP flags（完全对照 main.rs 的 DeferredWindow::draw()）
    SWP_NOSIZE     = 0x0001
    SWP_NOMOVE     = 0x0002
    SWP_NOZORDER   = 0x0004
    SWP_NOREDRAW   = 0x0008   # 仅移动/缩放时用；显隐时必须去掉，否则残影
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_HIDEWINDOW = 0x0080
    HWND_TOPMOST   = -1
    GWL_EXSTYLE    = -20
    WS_EX_TOOLWINDOW = 0x00000080   # 不在任务栏显示
    WS_EX_NOACTIVATE = 0x08000000   # 点击不抢焦点

    def _set_tool_window(hwnd: int):
        if not hwnd:
            return
        try:
            ex = _GWL(hwnd, GWL_EXSTYLE)
            _SWL(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        except Exception:
            pass

    def _w32_batch(updates: list):
        """
        仿 WindowCollection::draw() in main.rs：
        updates = [(hwnd, x, y, w, h, show_now, was_shown), ...]
        """
        if not updates:
            return
        hdwp = _BDP(len(updates))
        if not hdwp:
            return
        for hwnd, x, y, w, h, show_now, was_shown in updates:
            vis_stale = show_now != was_shown
            if vis_stale:
                # 显隐切换：不加 SWP_NOREDRAW（否则窗口内容不刷新）
                flags = SWP_NOZORDER | SWP_NOACTIVATE
                flags |= SWP_SHOWWINDOW if show_now else (SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE)
            else:
                # 仅移动/缩放：加 SWP_NOREDRAW 减少重绘
                flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOREDRAW
            hdwp = _DWP(hdwp, hwnd, HWND_TOPMOST, x, y, w, h, flags)
            if not hdwp:
                return
        _EDP(hdwp)

else:
    def _set_tool_window(hwnd): pass
    def _w32_batch(updates): pass


# ═══════════════════════════════════════════════════════════════════
# § 1  boxes.bin 读取器
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# § 2  窗口池（仿 WindowCollection in main.rs）
# ═══════════════════════════════════════════════════════════════════

class WinState:
    """单个窗口的脏标记状态（仿 DeferredWindow in main.rs）"""
    __slots__ = ('tk_win', 'hwnd',
                 'x', 'y', 'w', 'h',
                 'visible',
                 'pos_stale', 'sz_stale', 'vis_stale')

    def __init__(self, tk_win: tk.Toplevel, hwnd: int):
        self.tk_win  = tk_win
        self.hwnd    = hwnd
        self.x = self.y = 0
        self.w = self.h = 1
        self.visible  = False
        self.pos_stale = self.sz_stale = self.vis_stale = False

    def set_geom(self, x, y, w, h):
        self.pos_stale = self.pos_stale or (x != self.x or y != self.y)
        self.sz_stale  = self.sz_stale  or (w != self.w or h != self.h)
        self.x, self.y, self.w, self.h = x, y, w, h

    def set_visible(self, v: bool):
        self.vis_stale = self.vis_stale or (v != self.visible)
        self.visible   = v

    def stale(self) -> bool:
        return self.pos_stale or self.sz_stale or self.vis_stale

    def clear(self):
        self.pos_stale = self.sz_stale = self.vis_stale = False


class WindowPool:
    """
    仿 WindowCollection in main.rs：
    - preallocate() 在加载文件后一次性调用（对应 (0..MAX_WINDOWS).map(...)）
    - update() 按脏标记批量提交（对应 WindowCollection::draw()）
    """

    def __init__(self, root: tk.Tk):
        self._root = root
        self._pool: List[WinState] = []

    # ── 预分配（仅调用一次） ──────────────────────────────────────

    def preallocate(self, n: int):
        """
        创建 n 个无边框白色弹出窗口并获取真实 HWND。
        使用 wm_frame() 而非 winfo_id()：
            overrideredirect 窗口的顶层 OS 句柄是 wm_frame() 的返回值，
            winfo_id() 返回 Tk 内部子组件，DeferWindowPos 对其无效。
        """
        # 只扩充，不收缩
        while len(self._pool) < n:
            win = tk.Toplevel(self._root)
            win.overrideredirect(True)
            win.attributes('-topmost', True)
            win.configure(bg='white')
            # 初始放到屏幕外极远处（不用 withdraw，避免 Tk 不渲染背景）
            win.geometry('1x1+-32000+-32000')
            self._pool.append(WinState(win, 0))

        # 确保 OS 已创建窗口框架
        self._root.update()

        for ws in self._pool:
            if ws.hwnd == 0:
                try:
                    ws.hwnd = int(ws.tk_win.wm_frame(), 16)
                except Exception:
                    ws.hwnd = ws.tk_win.winfo_id()
                _set_tool_window(ws.hwnd)

    # ── 每帧更新（仿 WindowCollection::draw()） ───────────────────

    def update(self,
               rects: List[Tuple[int,int,int,int]],
               base_w: int, base_h: int,
               sx: int, sy: int, sw: int, sh: int):
        """
        将 rects（base_w×base_h 坐标）映射到屏幕矩形 (sx,sy,sw,sh)，
        仅提交 stale 的窗口。
        """
        sx_f = sw / max(base_w, 1)
        sy_f = sh / max(base_h, 1)
        n    = min(len(rects), len(self._pool))

        for i, (rx, ry, rw, rh) in enumerate(rects[:n]):
            ws = self._pool[i]
            wx = sx + round(rx * sx_f)
            wy = sy + round(ry * sy_f)
            ww = max(1, round(rw * sx_f))
            wh = max(1, round(rh * sy_f))
            ws.set_geom(wx, wy, ww, wh)
            ws.set_visible(True)

        for i in range(n, len(self._pool)):
            self._pool[i].set_visible(False)

        # 收集 stale 窗口
        updates = []
        for ws in self._pool:
            if not ws.stale():
                continue
            was_shown = ws.visible ^ ws.vis_stale  # 之前的可见状态
            updates.append((ws.hwnd, ws.x, ws.y, ws.w, ws.h,
                             ws.visible, was_shown))
            ws.clear()

        if IS_WIN:
            _w32_batch(updates)
        else:
            # 非 Windows 回退
            for _, x, y, w, h, show, _ in updates:
                # 找到对应窗口
                for ws in self._pool:
                    if ws.x == x and ws.y == y:
                        if show:
                            ws.tk_win.geometry(f'{w}x{h}+{x}+{y}')
                            ws.tk_win.deiconify()
                        else:
                            ws.tk_win.geometry('1x1+-32000+-32000')
                        break

    def hide_all(self):
        for ws in self._pool:
            ws.set_visible(False)
        updates = []
        for ws in self._pool:
            if ws.stale():
                updates.append((ws.hwnd, ws.x, ws.y, ws.w, ws.h, False, True))
                ws.clear()
        _w32_batch(updates)
        if not IS_WIN:
            for ws in self._pool:
                ws.tk_win.geometry('1x1+-32000+-32000')

    def destroy_all(self):
        for ws in self._pool:
            try:
                ws.tk_win.destroy()
            except Exception:
                pass
        self._pool.clear()

    @property
    def active_count(self) -> int:
        return sum(1 for ws in self._pool if ws.visible)


# ═══════════════════════════════════════════════════════════════════
# § 3  播放控制器（后台线程）
# ═══════════════════════════════════════════════════════════════════

class Player:
    """
    仿 main.rs 主循环：SetTimer + PeekMessage
    
    Python 版本：后台线程精确定时，直接调用 _w32_batch()（不经 Tkinter）。
    UI 回调每 30 帧通过 root.after() 一次，大幅减少调度开销。
    """

    def __init__(self, root: tk.Tk, pool: WindowPool, boxes: BoxesFile):
        self._root  = root
        self._pool  = pool
        self._boxes = boxes

        self._sx = 0;  self._sy = 0
        self._sw = root.winfo_screenwidth()
        self._sh = root.winfo_screenheight()

        self._thread: Optional[threading.Thread] = None
        self._stop  = threading.Event()
        self._pause = threading.Event()
        self._pause.set()

        self.cur_frame = 0

        # 音频
        self._audio_file: Optional[str] = None
        self._audio_ready = False

        # UI 回调（每 30 帧一次）
        self.on_status: Optional[callable] = None  # fn(frame_idx, win_count)

    def set_mapping(self, sx, sy, sw, sh):
        self._sx, self._sy, self._sw, self._sh = sx, sy, sw, sh

    def set_audio(self, path: Optional[str]):
        self._audio_file  = path
        self._audio_ready = False
        if path and _PYGAME:
            try:
                pygame.mixer.init(frequency=44100)
                pygame.mixer.music.load(path)
                self._audio_ready = True
            except Exception as e:
                print(f"[音频] 加载失败: {e}")

    def play(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._pause.set()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='WRFPlayer'
        )
        self._thread.start()

    def pause(self):
        self._pause.clear()
        if self._audio_ready:
            pygame.mixer.music.pause()

    def resume(self):
        self._pause.set()
        if self._audio_ready:
            pygame.mixer.music.unpause()

    def stop(self):
        self._stop.set()
        self._pause.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._audio_ready:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.cur_frame = 0

    # ── 播放主循环（仿 'outer loop in main.rs）─────────────────────

    def _loop(self):
        boxes = self._boxes
        fps   = boxes.fps
        frame_dur = 1.0 / fps

        # 启动音频（仿 clock.start()）
        if self._audio_ready:
            pygame.mixer.music.play()
            use_audio_sync = True
        else:
            use_audio_sync = False

        t_start = time.perf_counter()
        idx     = 0

        while not self._stop.is_set() and idx < len(boxes.frames):
            # 暂停等待（仿 pause_evt.wait()）
            self._pause.wait(timeout=0.5)
            if self._stop.is_set() or not self._pause.is_set():
                continue

            # 音频同步（仿 clock.time().ticks）
            if use_audio_sync:
                try:
                    pos_ms     = pygame.mixer.music.get_pos()
                    target_idx = int(pos_ms / 1000.0 * fps)
                except Exception:
                    target_idx = idx

                # 跑得太快：等待（仿 if next_tick > current_tick: continue）
                if idx > target_idx:
                    time.sleep(0.002)
                    continue

                # 跑得太慢：跳帧（仿 while current_tick > next_tick）
                if target_idx > idx + 1:
                    idx = min(target_idx, len(boxes.frames) - 1)
            else:
                # 纯时钟模式（仿 SetTimer 16ms）
                expected = t_start + idx * frame_dur
                now      = time.perf_counter()
                wait     = expected - now
                if wait > 0.001:
                    time.sleep(wait)

            if idx >= len(boxes.frames):
                break

            frame_rects = boxes.frames[idx]
            self.cur_frame = idx

            # ── 核心：直接调用 DeferWindowPos（不经 Tkinter）────────
            self._pool.update(
                frame_rects,
                boxes.base_w, boxes.base_h,
                self._sx, self._sy, self._sw, self._sh,
            )

            # UI 状态更新（每 30 帧 → root.after，避免频繁调度）
            if idx % 30 == 0 and self.on_status:
                snap_idx = idx
                snap_wins = self._pool.active_count
                self._root.after(0, self.on_status, snap_idx, snap_wins)

            idx += 1

        # 播放结束
        self._pool.hide_all()
        self._root.after(0, self.on_status, len(boxes.frames), 0)


# ═══════════════════════════════════════════════════════════════════
# § 4  Tkinter 界面
# ═══════════════════════════════════════════════════════════════════

BG    = '#0d1117';  PANEL = '#161b22';  CARD  = '#1c2128'
LINE  = '#30363d';  TEXT  = '#e6edf3';  MUTED = '#8b949e'
GREEN = '#238636';  BLUE  = '#1f6feb';  RED   = '#da3633'
GOLD  = '#d29922'


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WhiteRectFitter v3 — 播放器")
        self.root.geometry("480x620")
        self.root.configure(bg=BG)

        if IS_WIN:
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        self._boxes = BoxesFile()
        self._pool  = WindowPool(self.root)
        self._player: Optional[Player] = None

        self._build()

    # ── UI ───────────────────────────────────────────────────────

    def _build(self):
        # 顶栏
        top = tk.Frame(self.root, bg=PANEL, height=44)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Label(top, text="◈  WhiteRectFitter v3  —  Player",
                 bg=PANEL, fg=TEXT,
                 font=('Microsoft YaHei UI', 11, 'bold')).pack(
            side=tk.LEFT, padx=16, pady=10)
        tk.Frame(self.root, bg=LINE, height=1).pack(fill=tk.X)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        def sec(title):
            f = tk.LabelFrame(body, text=f" {title} ",
                              bg=CARD, fg=MUTED,
                              font=('Microsoft YaHei UI', 8),
                              bd=1, relief=tk.SOLID,
                              highlightthickness=0)
            f.pack(fill=tk.X, pady=(0, 8))
            return f

        # ── 文件选择 ──
        sf = sec("文件")
        self._iv_boxes = tk.StringVar(value="boxes.bin  (尚未加载)")
        tk.Label(sf, textvariable=self._iv_boxes,
                 bg=CARD, fg=MUTED,
                 font=('Consolas', 8), anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=6)
        tk.Button(sf, text="…", command=self._open,
                  bg=BLUE, fg=TEXT, activebackground='#388bfd',
                  relief=tk.FLAT, cursor='hand2', padx=8, pady=4, bd=0
                  ).pack(side=tk.RIGHT, padx=6, pady=4)

        self._lbl_info = tk.Label(body, text="",
                                   bg=BG, fg=MUTED,
                                   font=('Consolas', 8), anchor=tk.W, justify=tk.LEFT)
        self._lbl_info.pack(fill=tk.X, pady=(0, 4))

        # ── 音频（可选）──
        sa = sec("音频（可选）")
        self._iv_audio = tk.StringVar(value="无  (纯时钟模式)")
        tk.Label(sa, textvariable=self._iv_audio,
                 bg=CARD, fg=MUTED,
                 font=('Consolas', 8), anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=6)
        tk.Button(sa, text="…", command=self._open_audio,
                  bg=CARD, fg=TEXT, activebackground=LINE,
                  relief=tk.FLAT, cursor='hand2', padx=8, pady=4, bd=0,
                  highlightthickness=1, highlightbackground=LINE
                  ).pack(side=tk.RIGHT, padx=6, pady=4)

        # ── 屏幕映射 ──
        sm = sec("屏幕映射")
        self._iv_sx = tk.StringVar(value='0')
        self._iv_sy = tk.StringVar(value='0')
        self._iv_sw = tk.StringVar(value=str(self.root.winfo_screenwidth()))
        self._iv_sh = tk.StringVar(value=str(self.root.winfo_screenheight()))
        mrow = tk.Frame(sm, bg=CARD)
        mrow.pack(fill=tk.X, padx=6, pady=4)
        for lbl, var in [('X', self._iv_sx), ('Y', self._iv_sy),
                          ('W', self._iv_sw), ('H', self._iv_sh)]:
            tk.Label(mrow, text=lbl, bg=CARD, fg=MUTED,
                     font=('Consolas', 9)).pack(side=tk.LEFT, padx=(6, 2))
            tk.Entry(mrow, textvariable=var, width=7,
                     bg='#0d1117', fg=TEXT, insertbackground=TEXT,
                     font=('Consolas', 9), relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=LINE,
                     highlightcolor=BLUE).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(mrow, text="全屏", command=self._fullscreen,
                  bg=PANEL, fg=BLUE, activebackground=CARD,
                  font=('Consolas', 8), relief=tk.FLAT,
                  cursor='hand2', padx=6, pady=3, bd=0
                  ).pack(side=tk.LEFT, padx=(6, 0))

        # ── 播放控制 ──
        sc = sec("控制")
        brow = tk.Frame(sc, bg=CARD)
        brow.pack(fill=tk.X, padx=6, pady=6)
        bk = dict(relief=tk.FLAT, cursor='hand2', pady=7, bd=0,
                  font=('Segoe UI Symbol', 14))
        self._btn_play = tk.Button(brow, text="▶",
                                    bg=GREEN, fg=TEXT,
                                    activebackground='#2ea043',
                                    command=self._toggle, **bk)
        self._btn_play.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        tk.Button(brow, text="⏹", bg=CARD, fg=TEXT,
                  activebackground=LINE, command=self._stop_play, **bk
                  ).pack(side=tk.LEFT, expand=True, fill=tk.X)

        self._lbl_time = tk.Label(sc, text="00:00 / 00:00",
                                   bg=CARD, fg=MUTED, font=('Consolas', 9))
        self._lbl_time.pack(pady=(0, 6))

        # ── 统计 ──
        ss = sec("状态")
        row = tk.Frame(ss, bg=CARD)
        row.pack(fill=tk.X, padx=6, pady=6)
        self._stat: dict[str, tk.Label] = {}
        for key, title in [('frame', '帧'), ('wins', '白色窗口'),
                            ('fps',   'FPS'), ('mode', '同步模式')]:
            c = tk.Frame(row, bg='#0d1117',
                         highlightthickness=1, highlightbackground=LINE)
            c.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
            tk.Label(c, text=title, bg='#0d1117', fg=MUTED,
                     font=('Microsoft YaHei UI', 7)).pack(pady=(4, 0))
            lbl = tk.Label(c, text='—', bg='#0d1117', fg=TEXT,
                           font=('Consolas', 11, 'bold'))
            lbl.pack(pady=(0, 4))
            self._stat[key] = lbl

        # 状态栏
        tk.Frame(self.root, bg=LINE, height=1).pack(fill=tk.X, side=tk.BOTTOM)
        bar = tk.Frame(self.root, bg=PANEL, height=26)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self._iv_status = tk.StringVar(value="就绪 — 请先运行 preprocess.py 生成 boxes.bin")
        tk.Label(bar, textvariable=self._iv_status, bg=PANEL, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT, padx=10, pady=4)
        pfg = GREEN if IS_WIN else GOLD
        tag = "✓ Win32 DeferWindowPos" if IS_WIN else "⚠ 非 Windows"
        tk.Label(bar, text=tag, bg=PANEL, fg=pfg,
                 font=('Consolas', 8)).pack(side=tk.RIGHT, padx=10)

    # ── 事件 ─────────────────────────────────────────────────────

    def _open(self):
        p = filedialog.askopenfilename(
            title="选择 boxes.bin",
            filetypes=[("WRF boxes", "*.bin"), ("所有文件", "*.*")]
        )
        if not p:
            return
        if self._player:
            self._player.stop()
        if not self._boxes.load(p):
            messagebox.showerror("错误", f"无法解析文件，请确认由 preprocess.py 生成：\n{p}")
            return
        self._iv_boxes.set(Path(p).name)
        b = self._boxes
        self._lbl_info.config(
            text=(f"分辨率: {b.base_w}×{b.base_h}   帧率: {b.fps:.1f}fps   "
                  f"帧数: {b.total}   最大矩形/帧: {b.max_rects}")
        )
        # 预分配窗口（只做一次）
        self._pool.preallocate(b.max_rects)

        # 重建 Player
        self._player = Player(self.root, self._pool, self._boxes)
        self._player.on_status = self._on_status
        self._apply_mapping()
        self._update_mode()
        self._status(f"已加载  {Path(p).name}  ({b.total} 帧)")
        self._btn_play.config(state=tk.NORMAL)

    def _open_audio(self):
        p = filedialog.askopenfilename(
            title="选择音频文件（可选）",
            filetypes=[("音频", "*.mp3 *.ogg *.wav *.flac"), ("所有文件", "*.*")]
        )
        if not p:
            return
        self._iv_audio.set(Path(p).name)
        if self._player:
            self._player.set_audio(p)
        self._update_mode()

    def _fullscreen(self):
        self._iv_sx.set('0'); self._iv_sy.set('0')
        self._iv_sw.set(str(self.root.winfo_screenwidth()))
        self._iv_sh.set(str(self.root.winfo_screenheight()))
        self._apply_mapping()

    def _apply_mapping(self):
        if not self._player:
            return
        try:
            sx = int(self._iv_sx.get()); sy = int(self._iv_sy.get())
            sw = int(self._iv_sw.get()); sh = int(self._iv_sh.get())
            assert sw > 0 and sh > 0
            self._player.set_mapping(sx, sy, sw, sh)
        except Exception:
            pass

    def _toggle(self):
        if not self._player:
            return
        btn_text = self._btn_play.cget('text')
        if btn_text == '▶':
            self._apply_mapping()
            self._player.play()
            self._btn_play.config(text='⏸', bg='#9e6a03')
            self._status("播放中…")
        elif btn_text == '⏸':
            self._player.pause()
            self._btn_play.config(text='▶', bg=GREEN)
            self._status("已暂停")
        else:
            self._player.resume()
            self._btn_play.config(text='⏸', bg='#9e6a03')
            self._status("继续播放")

    def _stop_play(self):
        if self._player:
            self._player.stop()
        self._pool.hide_all()
        self._btn_play.config(text='▶', bg=GREEN)
        for k in ('frame', 'wins', 'fps'):
            self._stat[k].config(text='0')
        self._status("已停止")

    def _on_status(self, frame_idx: int, win_count: int):
        b = self._boxes
        total_sec = b.total / max(b.fps, 1)
        cur_sec   = frame_idx / max(b.fps, 1)
        self._lbl_time.config(text=f"{self._fmt(cur_sec)} / {self._fmt(total_sec)}")
        self._stat['frame'].config(text=str(frame_idx))
        self._stat['wins' ].config(text=str(win_count))
        if win_count == 0 and frame_idx >= b.total - 1:
            self._btn_play.config(text='▶', bg=GREEN)
            self._status("播放完毕")

    def _update_mode(self):
        if not self._player:
            return
        if self._player._audio_ready:
            self._stat['mode'].config(text='音频同步', fg=GREEN)
        else:
            self._stat['mode'].config(text='时钟', fg=MUTED)

    @staticmethod
    def _fmt(s: float) -> str:
        s = max(0, int(s))
        return f"{s//60:02d}:{s%60:02d}"

    def _status(self, msg: str):
        self._iv_status.set(msg)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.mainloop()

    def _quit(self):
        if self._player:
            self._player.stop()
        self._pool.destroy_all()
        self.root.destroy()


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if not IS_WIN:
        print("[警告] 桌面窗口叠加功能仅完整支持 Windows 10/11")
    App().run()
