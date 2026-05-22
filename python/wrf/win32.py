"""WhiteRectFitter v3 — Win32 窗口池（仿 WindowCollection in main.rs）"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import List, Tuple

IS_WIN = sys.platform == 'win32'

# ═══════════════════════════════════════════════════════════════════
# Win32 ctypes 绑定（仿 main.rs 的 windows crate 调用）
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
# 窗口状态 + 窗口池
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
