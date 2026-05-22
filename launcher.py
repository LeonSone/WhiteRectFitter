#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
launcher.py  —  WhiteRectFitter 启动器 GUI
配置参数，直接调用播放器和预处理器模块。
"""

from __future__ import annotations

import io
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

# ─── 颜色主题（复用 player.py）───────────────────────────────────
BG    = '#0d1117';  PANEL = '#161b22';  CARD  = '#1c2128'
LINE  = '#30363d';  TEXT  = '#e6edf3';  MUTED = '#8b949e'
GREEN = '#238636';  BLUE  = '#1f6feb';  RED   = '#da3633'
GOLD  = '#d29922'

def _get_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _get_data_dir() -> Path:
    """打包后从 exe 内置资源目录读取，开发时从项目目录读取。"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

SCRIPT_DIR = _get_base_dir()
DATA_DIR = _get_data_dir()


# ═══════════════════════════════════════════════════════════════════
# § 应用
# ═══════════════════════════════════════════════════════════════════

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WRF Launcher")
        self.root.geometry("520x520")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        # 检测工作区
        self._work_area = self._detect_work_area()

        # 状态变量
        self._mode = tk.StringVar(value='play_noaudio')
        self._boxes_path = tk.StringVar(value='')
        self._audio_path = tk.StringVar(value='')
        self._iv_sx = tk.StringVar(value='0')
        self._iv_sy = tk.StringVar(value='0')
        self._iv_sw = tk.StringVar(value='1440')
        self._iv_sh = tk.StringVar(value='1080')

        self._input_path  = tk.StringVar(value='')
        self._output_path = tk.StringVar(value='boxes.bin')
        self._iv_width    = tk.StringVar(value='64')
        self._iv_maxrects = tk.StringVar(value='150')
        self._iv_thresh   = tk.StringVar(value='200')

        # 文件名显示标签引用
        self._lbl_boxes = None
        self._lbl_audio = None
        self._lbl_input = None
        self._lbl_output = None

        self._build()

        # 默认文件（优先从内置资源读取）
        default_boxes = DATA_DIR / 'data' / 'boxes512.bin'
        if default_boxes.is_file():
            self._boxes_path.set(str(default_boxes))
        default_video = DATA_DIR / 'bad_apple.mp4'
        if default_video.is_file():
            self._input_path.set(str(default_video))
        default_audio = DATA_DIR / 'bad_apple.mp3'
        if default_audio.is_file():
            self._audio_path.set(str(default_audio))

        # 模式切换回调
        self._mode.trace_add('write', self._on_mode_change)
        self._on_mode_change()

        # 更新文件显示
        self._update_boxes_label()
        self._update_audio_label()
        self._update_input_label()
        self._update_output_label()

        # 参数变化时更新预览
        for var in (self._boxes_path, self._audio_path,
                    self._iv_sx, self._iv_sy, self._iv_sw, self._iv_sh,
                    self._input_path, self._output_path,
                    self._iv_width, self._iv_maxrects, self._iv_thresh):
            var.trace_add('write', lambda *_: self._update_preview())

        self._update_preview()

    # ── 检测工作区 ────────────────────────────────────────────────

    @staticmethod
    def _detect_work_area() -> tuple[int, int, int, int]:
        try:
            import ctypes
            import ctypes.wintypes as wt
            rect = wt.RECT()
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
            x, y = rect.left, rect.top
            w, h = rect.right - rect.left, rect.bottom - rect.top
            return x, y, w, h
        except Exception:
            return 0, 0, 1920, 1080

    # ── 构建 UI ───────────────────────────────────────────────────

    def _build(self):
        # 顶栏
        top = tk.Frame(self.root, bg=PANEL, height=38)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Label(top, text="WRF Launcher",
                 bg=PANEL, fg=TEXT,
                 font=('Microsoft YaHei UI', 10, 'bold')).pack(
            side=tk.LEFT, padx=12, pady=8)
        tk.Frame(self.root, bg=LINE, height=1).pack(fill=tk.X)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        # ── 模式选择（水平排列）──
        mode_frame = tk.Frame(body, bg=BG)
        mode_frame.pack(fill=tk.X, pady=(0, 4))
        for val, txt in [('play_noaudio', '无音频'),
                          ('play_audio',   '有音频'),
                          ('preprocess',   '预编译')]:
            tk.Radiobutton(mode_frame, text=txt, variable=self._mode, value=val,
                           bg=BG, fg=TEXT, selectcolor=BG,
                           activebackground=BG, activeforeground=TEXT,
                           font=('Microsoft YaHei UI', 9),
                           cursor='hand2').pack(side=tk.LEFT, padx=(0, 12))

        tk.Frame(body, bg=LINE, height=1).pack(fill=tk.X, pady=2)

        # ── 播放面板 ──
        self._play_panel = tk.Frame(body, bg=BG)

        # boxes.bin + 音频（同一行两列）
        row1 = tk.Frame(self._play_panel, bg=BG)
        row1.pack(fill=tk.X, pady=2)
        # boxes
        tk.Label(row1, text="boxes", bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT)
        self._lbl_boxes = tk.Label(row1, text="尚未选择", bg=CARD, fg=MUTED,
                                    font=('Consolas', 8), anchor=tk.W,
                                    relief=tk.SOLID, bd=1, highlightthickness=1,
                                    highlightbackground=LINE, padx=6, pady=2)
        self._lbl_boxes.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 2))
        tk.Button(row1, text="...", command=self._browse_boxes,
                  bg=BLUE, fg=TEXT, activebackground='#388bfd',
                  relief=tk.FLAT, cursor='hand2', padx=6, pady=1, bd=0
                  ).pack(side=tk.LEFT, padx=(0, 10))
        # 音频
        self._audio_widgets = []
        self._lbl_audio_prefix = tk.Label(row1, text="音频", bg=BG, fg=MUTED,
                                           font=('Microsoft YaHei UI', 8))
        self._audio_widgets.append(self._lbl_audio_prefix)
        self._lbl_audio = tk.Label(row1, text="无", bg=CARD, fg=MUTED,
                                    font=('Consolas', 8), anchor=tk.W,
                                    relief=tk.SOLID, bd=1, highlightthickness=1,
                                    highlightbackground=LINE, padx=6, pady=2)
        self._audio_widgets.append(self._lbl_audio)
        btn_audio = tk.Button(row1, text="...", command=self._browse_audio,
                              bg=CARD, fg=TEXT, activebackground=LINE,
                              relief=tk.FLAT, cursor='hand2', padx=6, pady=1, bd=0,
                              highlightthickness=1, highlightbackground=LINE)
        self._audio_widgets.append(btn_audio)

        # 映射范围
        map_row = tk.Frame(self._play_panel, bg=BG)
        map_row.pack(fill=tk.X, pady=2)
        tk.Label(map_row, text="映射", bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT)
        for lbl, var in [('X', self._iv_sx), ('Y', self._iv_sy),
                          ('W', self._iv_sw), ('H', self._iv_sh)]:
            tk.Label(map_row, text=lbl, bg=BG, fg=MUTED,
                     font=('Consolas', 8)).pack(side=tk.LEFT, padx=(6, 1))
            tk.Entry(map_row, textvariable=var, width=5,
                     bg=CARD, fg=TEXT, insertbackground=TEXT,
                     font=('Consolas', 8), relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=LINE,
                     highlightcolor=BLUE).pack(side=tk.LEFT)
        tk.Button(map_row, text="全屏", command=self._fullscreen,
                  bg=PANEL, fg=BLUE, activebackground=CARD,
                  font=('Consolas', 8), relief=tk.FLAT,
                  cursor='hand2', padx=4, pady=1, bd=0
                  ).pack(side=tk.LEFT, padx=(8, 0))

        # ── 预编译面板 ──
        self._preprocess_panel = tk.Frame(body, bg=BG)

        # 输入 + 输出
        row_p1 = tk.Frame(self._preprocess_panel, bg=BG)
        row_p1.pack(fill=tk.X, pady=2)
        tk.Label(row_p1, text="输入", bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT)
        self._lbl_input = tk.Label(row_p1, text="尚未选择", bg=CARD, fg=MUTED,
                                    font=('Consolas', 8), anchor=tk.W,
                                    relief=tk.SOLID, bd=1, highlightthickness=1,
                                    highlightbackground=LINE, padx=6, pady=2)
        self._lbl_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 2))
        tk.Button(row_p1, text="...", command=self._browse_input,
                  bg=BLUE, fg=TEXT, activebackground='#388bfd',
                  relief=tk.FLAT, cursor='hand2', padx=6, pady=1, bd=0
                  ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(row_p1, text="输出", bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT)
        self._lbl_output = tk.Label(row_p1, text="boxes.bin", bg=CARD, fg=MUTED,
                                     font=('Consolas', 8), anchor=tk.W,
                                     relief=tk.SOLID, bd=1, highlightthickness=1,
                                     highlightbackground=LINE, padx=6, pady=2)
        self._lbl_output.pack(side=tk.LEFT, padx=(4, 2))
        tk.Button(row_p1, text="...", command=self._browse_output,
                  bg=CARD, fg=TEXT, activebackground=LINE,
                  relief=tk.FLAT, cursor='hand2', padx=6, pady=1, bd=0,
                  highlightthickness=1, highlightbackground=LINE
                  ).pack(side=tk.LEFT)

        # 参数行
        row_p2 = tk.Frame(self._preprocess_panel, bg=BG)
        row_p2.pack(fill=tk.X, pady=2)
        for lbl, var in [('宽度', self._iv_width),
                          ('最大矩形', self._iv_maxrects),
                          ('阈值', self._iv_thresh)]:
            tk.Label(row_p2, text=lbl, bg=BG, fg=MUTED,
                     font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT, padx=(0, 1))
            tk.Entry(row_p2, textvariable=var, width=5,
                     bg=CARD, fg=TEXT, insertbackground=TEXT,
                     font=('Consolas', 8), relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=LINE,
                     highlightcolor=BLUE).pack(side=tk.LEFT, padx=(0, 10))

        # ── 启动按钮 ──
        self._btn_launch = tk.Button(body, text="启动",
                                      bg=GREEN, fg=TEXT,
                                      activebackground='#2ea043',
                                      font=('Microsoft YaHei UI', 10, 'bold'),
                                      relief=tk.FLAT, cursor='hand2',
                                      padx=16, pady=6, bd=0,
                                      command=self._launch)
        self._btn_launch.pack(pady=4)

        # ── 日志 ──
        tk.Label(body, text="日志", bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(anchor=tk.W, pady=(2, 0))
        self._log_text = tk.Text(body, height=4, bg=CARD, fg=TEXT,
                                  font=('Consolas', 8), relief=tk.FLAT, wrap=tk.WORD,
                                  state=tk.DISABLED, bd=0, padx=6, pady=4,
                                  highlightthickness=1, highlightbackground=LINE)
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # ── 状态栏 ──
        tk.Frame(self.root, bg=LINE, height=1).pack(fill=tk.X, side=tk.BOTTOM)
        bar = tk.Frame(self.root, bg=PANEL, height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self._iv_status = tk.StringVar(value="就绪")
        tk.Label(bar, textvariable=self._iv_status, bg=PANEL, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side=tk.LEFT, padx=8, pady=3)
        tk.Label(bar, text="WhiteRectFitter", bg=PANEL, fg=MUTED,
                 font=('Consolas', 8)).pack(side=tk.RIGHT, padx=8)

    # ── 文件选择 ──────────────────────────────────────────────────

    def _browse_boxes(self):
        p = filedialog.askopenfilename(
            title="选择 boxes.bin",
            filetypes=[("WRF boxes", "*.bin"), ("所有文件", "*.*")])
        if p:
            self._boxes_path.set(p)

    def _browse_audio(self):
        p = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("音频", "*.mp3 *.ogg *.wav *.flac"), ("所有文件", "*.*")])
        if p:
            self._audio_path.set(p)

    def _browse_input(self):
        p = filedialog.askopenfilename(
            title="选择输入视频",
            filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")])
        if p:
            self._input_path.set(p)

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
            title="输出 boxes.bin 路径",
            defaultextension=".bin",
            filetypes=[("WRF boxes", "*.bin"), ("所有文件", "*.*")])
        if p:
            self._output_path.set(p)

    # ── 文件名标签更新 ────────────────────────────────────────────

    def _update_boxes_label(self, *_):
        p = self._boxes_path.get()
        self._lbl_boxes.config(text=Path(p).name if p else "尚未选择")

    def _update_audio_label(self, *_):
        p = self._audio_path.get()
        self._lbl_audio.config(text=Path(p).name if p else "无")

    def _update_input_label(self, *_):
        p = self._input_path.get()
        self._lbl_input.config(text=Path(p).name if p else "尚未选择")

    def _update_output_label(self, *_):
        p = self._output_path.get()
        self._lbl_output.config(text=Path(p).name if p else "boxes.bin")

    # ── 模式切换 ──────────────────────────────────────────────────

    def _on_mode_change(self, *_):
        mode = self._mode.get()
        self._play_panel.pack_forget()
        self._preprocess_panel.pack_forget()

        # 隐藏音频控件
        for w in self._audio_widgets:
            w.pack_forget()

        if mode in ('play_noaudio', 'play_audio'):
            self._play_panel.pack(fill=tk.X, pady=(0, 4))
            if mode == 'play_audio':
                # 在 row1 中显示音频控件
                self._lbl_audio_prefix.pack(side=tk.LEFT, padx=(10, 0))
                self._lbl_audio.pack(side=tk.LEFT, padx=(4, 2))
                self._audio_widgets[2].pack(side=tk.LEFT)  # browse button
        elif mode == 'preprocess':
            self._preprocess_panel.pack(fill=tk.X, pady=(0, 4))

        self._update_preview()

    # ── 全屏按钮 ──────────────────────────────────────────────────

    def _fullscreen(self):
        self._iv_sx.set('0')
        self._iv_sy.set('0')
        self._iv_sw.set(str(self._work_area[2]))
        self._iv_sh.set(str(self._work_area[3]))

    # ── 命令预览（保留供 UI 更新用）──────────────────────────────

    def _update_preview(self, *_):
        pass

    # ── 验证 ──────────────────────────────────────────────────────

    def _validate(self) -> list[str]:
        errors = []
        mode = self._mode.get()

        if mode in ('play_noaudio', 'play_audio'):
            p = self._boxes_path.get()
            if not p:
                errors.append("请选择 boxes.bin 文件")
            elif not Path(p).is_file():
                errors.append(f"boxes.bin 文件不存在: {p}")
            if mode == 'play_audio':
                a = self._audio_path.get()
                if a and not Path(a).is_file():
                    errors.append(f"音频文件不存在: {a}")
            for name, var in [('sx', self._iv_sx), ('sy', self._iv_sy),
                              ('sw', self._iv_sw), ('sh', self._iv_sh)]:
                try:
                    v = int(var.get())
                    if name in ('sw', 'sh') and v <= 0:
                        errors.append(f"{name} 必须为正整数")
                except ValueError:
                    errors.append(f"{name} 必须为整数: {var.get()}")

        elif mode == 'preprocess':
            p = self._input_path.get()
            if not p:
                errors.append("请选择输入视频文件")
            elif not Path(p).is_file():
                errors.append(f"视频文件不存在: {p}")
            for name, var in [('width', self._iv_width),
                              ('max-rects', self._iv_maxrects),
                              ('thresh', self._iv_thresh)]:
                try:
                    int(var.get())
                except ValueError:
                    errors.append(f"{name} 必须为整数: {var.get()}")

        return errors

    # ── 启动 ──────────────────────────────────────────────────────

    def _launch(self):
        errors = self._validate()
        if errors:
            messagebox.showerror("参数错误", "\n".join(errors))
            return

        # 清空日志
        self._log_text.config(state=tk.NORMAL)
        self._log_text.delete('1.0', tk.END)
        self._log_text.config(state=tk.DISABLED)

        self._btn_launch.config(state=tk.DISABLED, text='运行中...')
        mode = self._mode.get()

        if mode in ('play_noaudio', 'play_audio'):
            self._run_player(mode)
        elif mode == 'preprocess':
            self._run_preprocess()

    # ── 播放（直接调用 Python 模块）────────────────────────────────

    def _run_player(self, mode: str):
        try:
            from player import Player
            from wrf.boxes import BoxesFile
            from wrf.win32 import WindowPool
        except ImportError as e:
            messagebox.showerror("导入错误", f"无法加载播放模块:\n{e}")
            self._btn_launch.config(state=tk.NORMAL, text='启动')
            return

        boxes = BoxesFile()
        if not boxes.load(self._boxes_path.get()):
            messagebox.showerror("错误", "无法解析 boxes.bin 文件")
            self._btn_launch.config(state=tk.NORMAL, text='启动')
            return

        self._player_root = tk.Toplevel(self.root)
        self._player_root.withdraw()

        pool = WindowPool(self._player_root)
        pool.preallocate(boxes.max_rects)

        player = Player(self._player_root, pool, boxes)

        if mode == 'play_audio' and self._audio_path.get():
            player.set_audio(self._audio_path.get())

        try:
            sx = int(self._iv_sx.get()); sy = int(self._iv_sy.get())
            sw = int(self._iv_sw.get()); sh = int(self._iv_sh.get())
            if sw > 0 and sh > 0:
                player.set_mapping(sx, sy, sw, sh)
        except ValueError:
            pass

        player.on_status = self._on_play_status
        self._player = player
        self._pool = pool
        self._iv_status.set("播放中...")
        player.play()

    def _on_play_status(self, frame_idx: int, win_count: int):
        if win_count == 0 and hasattr(self, '_player') and self._player:
            boxes = self._player._boxes
            if frame_idx >= boxes.total - 1:
                self._btn_launch.config(state=tk.NORMAL, text='启动')
                self._iv_status.set("播放完毕")
                if hasattr(self, '_player_root') and self._player_root:
                    self._player_root.destroy()
                    self._player_root = None
                return
        self._iv_status.set(f"帧 {frame_idx}  窗口 {win_count}")

    # ── 预处理（直接调用 Python 模块）──────────────────────────────

    def _run_preprocess(self):
        self._iv_status.set("预处理中...")

        def worker():
            old_stdout = sys.stdout
            sys.stdout = _LogRedirect(self.root, self._log_text)
            try:
                from preprocess import preprocess
                preprocess(
                    self._input_path.get(),
                    self._output_path.get() or 'boxes.bin',
                    int(self._iv_width.get() or '64'),
                    int(self._iv_maxrects.get() or '2048'),
                    int(self._iv_thresh.get() or '200'),
                )
                self.root.after(0, self._on_preprocess_done, True, "")
            except Exception as e:
                self.root.after(0, self._on_preprocess_done, False, str(e))
            finally:
                sys.stdout = old_stdout

        threading.Thread(target=worker, daemon=True).start()

    def _on_preprocess_done(self, ok: bool, err: str):
        self._btn_launch.config(state=tk.NORMAL, text='启动')
        if ok:
            self._iv_status.set("预处理完成")
            self._append_log("\n--- 预处理完成 ---\n")
        else:
            self._iv_status.set("预处理失败")
            self._append_log(f"\n--- 预处理失败: {err} ---\n")

    def _append_log(self, text: str):
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, text)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ── 退出 ──────────────────────────────────────────────────────

    def _quit(self):
        if hasattr(self, '_player') and self._player:
            self._player.stop()
        if hasattr(self, '_pool') and self._pool:
            self._pool.destroy_all()
        if hasattr(self, '_player_root') and self._player_root:
            try:
                self._player_root.destroy()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        # 绑定文件路径变化到标签更新（延迟绑定确保标签已创建）
        self._boxes_path.trace_add('write', self._update_boxes_label)
        self._audio_path.trace_add('write', self._update_audio_label)
        self._input_path.trace_add('write', self._update_input_label)
        self._output_path.trace_add('write', self._update_output_label)

        self.root.mainloop()


# ═══════════════════════════════════════════════════════════════════
# stdout 重定向（用于预处理日志）
# ═══════════════════════════════════════════════════════════════════

class _LogRedirect(io.TextIOBase):
    """将 print 输出重定向到 Tkinter Text 控件"""
    def __init__(self, root, text_widget):
        self._root = root
        self._tw = text_widget

    def write(self, s):
        if not s:
            return 0
        s = s.replace('\r', '\n')
        self._root.after(0, self._append, s)
        return len(s)

    def flush(self):
        pass

    def _append(self, s):
        self._tw.config(state=tk.NORMAL)
        self._tw.insert(tk.END, s)
        self._tw.see(tk.END)
        self._tw.config(state=tk.DISABLED)


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

def main():
    App().run()


if __name__ == '__main__':
    main()
