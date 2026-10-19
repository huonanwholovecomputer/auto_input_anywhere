# -*- coding: utf-8 -*-
"""
键盘输入助手（图形界面版）

把一段文本自动「敲」进当前光标所在的输入框。
文本直接在软件窗口里粘贴、编辑，不再依赖桌面上的 input.txt。

【Unicode 直发】
所有字符都通过 SendInput 的 Unicode 模式直接发送，完全绕过输入法：
不需要切换中英文，不依赖输入法选词（没有同音词/多音字问题），也不使用剪贴板。
只依赖 Python 标准库，无需安装任何第三方包。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

# Windows 控制台默认用 GBK，输出 emoji 会直接抛 UnicodeEncodeError。统一切到 UTF-8。
# 以窗口模式打包时 sys.stdout / sys.stderr 可能是 None，所以每一步都要兜住。
if sys.platform == "win32":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ============== 速度档位 ==============
# （名称，每个字符之间的间隔秒，换行后的等待秒）
# 目标程序丢字就换更慢的档位。
SPEED_PRESETS = {
    "fast": {"name": "极速", "key_delay": 0.01, "enter_delay": 0.015},
    "safe": {"name": "保守", "key_delay": 0.04, "enter_delay": 0.05},
}
DEFAULT_SPEED = "fast"
# ==================================

# ============== 程序标识 ==============
APP_NAME = "键盘输入助手"
APP_VERSION = "v0.3"          # 显示在窗口标题里；改版本号时同步改 version_info.txt
# ====================================

user32 = ctypes.WinDLL("user32", use_last_error=True)

_ULONG_PTR = (ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8
              else ctypes.c_ulong)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]


_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002

_VK_RETURN = 0x0D
_VK_ESCAPE = 0x1B
_VK_HOME = 0x24
_VK_DELETE = 0x2E
_VK_LSHIFT = 0xA0

# Home / Delete 必须带 EXTENDEDKEY 标志，否则会被当成小键盘上对应的键
_EXTENDED_KEYS = {_VK_HOME, _VK_DELETE}

user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.GetWindowThreadProcessId.restype = wt.DWORD


def _kb(vk=0, scan=0, up=False, unicode_mode=False):
    flags = 0
    if unicode_mode:
        flags |= _KEYEVENTF_UNICODE
    elif vk in _EXTENDED_KEYS:
        flags |= _KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= _KEYEVENTF_KEYUP
    return _INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, 0)))


def _send(events):
    arr = (_INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    if sent != len(events):
        raise OSError(
            f"SendInput 失败 (错误码 {ctypes.get_last_error()})。"
            "若目标程序以管理员身份运行，本程序也需要用管理员身份运行。"
        )


def send_char(ch):
    """以 Unicode 模式发送一个字符。这条路绕过输入法，不受中英文状态影响"""
    code = ord(ch)
    if code > 0xFFFF:
        # emoji 等 BMP 之外的字符要拆成 UTF-16 代理对
        code -= 0x10000
        units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
    else:
        units = [code]
    for u in units:
        _send([_kb(scan=u, unicode_mode=True),
               _kb(scan=u, up=True, unicode_mode=True)])


def tap(vk):
    """按一下某个键"""
    _send([_kb(vk=vk), _kb(vk=vk, up=True)])


def chord(*vks):
    """按住多个键再逆序松开，例如 chord(VK_LSHIFT, VK_HOME) 就是 Shift+Home"""
    _send([_kb(vk=v) for v in vks] + [_kb(vk=v, up=True) for v in reversed(vks)])


class TypingCancelled(Exception):
    """用户中途点了「停止」"""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TypingCancelled()


# ---------- 核心输入函数 ----------
def newline_to(indent, enter_delay, cancel=None):
    """换行，并把编辑器自动插入的缩进换成源文本里的真实缩进"""
    _check_cancel(cancel)
    # 编辑器的代码提示/参数提示弹窗会把 Enter 吃掉，先按 Esc 关掉它
    tap(_VK_ESCAPE)
    tap(_VK_RETURN)
    # 编辑器会按上一行自动缩进，而且只增不减，逐行累积就越缩越深。
    # 选中它插入的那段缩进，替换成源文本里的真实缩进。
    chord(_VK_LSHIFT, _VK_HOME)
    if indent:
        for ch in indent:
            send_char(ch)
    else:
        tap(_VK_DELETE)
    time.sleep(enter_delay)


def type_text(text, key_delay, enter_delay, cancel=None, on_progress=None):
    """逐字输入 text。on_progress 收到的是「已处理的源文本字符数」"""
    total = len(text)
    pos = 0
    for line_index, line in enumerate(text.split("\n")):
        indent = line[:len(line) - len(line.lstrip())]
        if line_index:
            _check_cancel(cancel)
            newline_to(indent, enter_delay, cancel)
            pos += 1 + len(indent)
            if on_progress is not None:
                on_progress(min(pos, total))
        body = line[len(indent):]
        for ch in body:
            _check_cancel(cancel)
            if ch != "\r":
                send_char(ch)
                time.sleep(key_delay)
            pos += 1
            if on_progress is not None:
                on_progress(min(pos, total))


# ---------- 小工具 ----------
def normalize_text(text):
    """把 Windows 剪贴板里的 CRLF 统一成 LF"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    minutes = int(seconds // 60)
    rest = int(round(seconds % 60))
    if rest == 60:
        minutes += 1
        rest = 0
    if minutes < 60:
        return f"{minutes} 分 {rest} 秒"
    hours = minutes // 60
    minutes %= 60
    return f"{hours} 小时 {minutes} 分"


def format_wait(seconds):
    """倒计时显示：向上取到 0.1 秒，避免还没开始就显示 0.0"""
    if seconds >= 10:
        return f"{math.ceil(seconds)}"
    tenths = math.ceil(seconds * 10) / 10
    if abs(tenths - round(tenths)) < 1e-9:
        return f"{int(round(tenths))}"
    return f"{tenths:.1f}"


def enable_dpi_awareness():
    """让窗口在高分屏上按物理像素渲染，避免发虚

    这里用「系统 DPI 感知」而不是 per-monitor：Tk 8.6 不处理
    WM_DPICHANGED，跨不同缩放比例的显示器拖动时 per-monitor
    反而会让界面尺寸错乱。系统 DPI 感知下，Windows 会把窗口
    整体位图缩放到副屏，尺寸始终正确。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_system_dpi():
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        try:
            return int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:
        return 96


def find_icon(name="icon.ico"):
    """找图标文件。源码运行、PyInstaller 打包、Nuitka 打包三种情况下目录都不同，
    所以按几个候选位置依次找；找不到就返回 None（不影响程序运行）。"""
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)          # PyInstaller onefile
    if meipass:
        roots.append(meipass)
    try:
        roots.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    for candidate in (sys.argv[0] if sys.argv else "",
                      sys.executable or ""):
        if candidate:
            roots.append(os.path.dirname(os.path.abspath(candidate)))
    for root in roots:
        for rel in (os.path.join("assets", name), name):
            path = os.path.join(root, rel)
            if os.path.isfile(path):
                return path
    return None


def set_taskbar_identity():
    """给进程一个独立 AppUserModelID：直接跑 python 时任务栏不会再归到 python.exe 上"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"huonanwholovecomputer.{APP_NAME}.{APP_VERSION}")
    except Exception:
        pass


def _log_exception(exc, val, tb):
    text = "".join(traceback.format_exception(exc, val, tb))
    try:
        if sys.stderr is not None:
            sys.stderr.write(text)
    except Exception:
        pass
    return text


# ---------- 界面 ----------
class KeyboardInputApp:
    """主窗口：输入区 + 速度 + 倒计时 + 进度 + 停止"""

    POLL_MS = 80
    COUNTDOWN_TICK_MS = 100
    DEFAULT_DELAY = 3.0

    BG = "#f6f8fa"
    BORDER = "#c9d1d9"
    TEXT_FG = "#1f2328"
    HINT_FG = "#57606a"
    OK_FG = "#1a7f37"
    WARN_FG = "#9a6700"
    ERROR_FG = "#cf222e"

    def __init__(self, root):
        self.root = root
        self.dpi = get_system_dpi()
        self.scale = max(1.0, self.dpi / 96.0)
        try:
            self.root.tk.call("tk", "scaling", self.dpi / 72.0)
        except tk.TclError:
            pass

        self._phase = "idle"          # idle / countdown / typing
        self._cancel = threading.Event()
        self._results = queue.Queue()
        self._typed = 0
        self._total = 0
        self._text = ""
        self._key_delay = 0.0
        self._enter_delay = 0.0
        self._countdown_job = None
        self._poll_job = None
        self._countdown_deadline = 0.0
        self._last_countdown_text = None
        self._started_at = 0.0
        self._last_shown = -1

        self._build_style()
        self._build_ui()
        self._bind_events()
        self._refresh_metrics()

        root.update_idletasks()
        root.deiconify()
        self.text.focus_set()

    # ---------- 尺寸 / 样式 ----------
    def _s(self, value):
        return max(1, int(round(value * self.scale)))

    def _build_style(self):
        self.style = ttk.Style(self.root)
        if sys.platform == "win32":
            for theme in ("vista", "winnative", "clam"):
                if theme in self.style.theme_names():
                    self.style.theme_use(theme)
                    break

        base_font = ("Microsoft YaHei UI", 10)
        self.style.configure("TLabel", font=base_font)
        self.style.configure("TRadiobutton", font=base_font)
        self.style.configure("TButton", font=base_font)
        self.style.configure("Title.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        self.style.configure("Hint.TLabel",
                             font=("Microsoft YaHei UI", 9),
                             foreground=self.HINT_FG)
        self.style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9))
        self.style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"))

    def _apply_window_icon(self):
        """把标题栏 / 任务栏图标换成 assets/icon.ico（用 default= 一次性覆盖所有窗口）"""
        path = find_icon()
        if not path:
            return
        try:
            self.root.iconbitmap(default=path)
        except tk.TclError:
            pass

    def _build_ui(self):
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self._apply_window_icon()
        width, height = self._s(820), self._s(620)
        self.root.minsize(self._s(560), self._s(430))
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        try:
            self.root.configure(bg=self.BG)
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=(self._s(16), self._s(14)))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # 标题行
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="输入内容", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="Ctrl+V 直接粘贴 · 也可右键粘贴",
                  style="Hint.TLabel").pack(side="right")

        # 文本区（外面套 1px 边框，比 Text 自带凹陷边框干净）
        text_wrap = tk.Frame(outer, bg=self.BORDER, bd=0, highlightthickness=0)
        text_wrap.grid(row=1, column=0, sticky="nsew",
                       pady=(self._s(6), self._s(10)))
        text_wrap.rowconfigure(0, weight=1)
        text_wrap.columnconfigure(0, weight=1)

        self.text = tk.Text(
            text_wrap,
            wrap="word",
            undo=True,
            maxundo=500,
            bd=0,
            highlightthickness=0,
            padx=self._s(10),
            pady=self._s(8),
            font=("Microsoft YaHei UI", 11),
            bg="#ffffff",
            fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG,
            insertwidth=self._s(2),
            selectbackground="#b6d7ff",
            selectforeground=self.TEXT_FG,
            spacing3=self._s(1),
        )
        self.text.grid(row=0, column=0, sticky="nsew", padx=self._s(1),
                       pady=self._s(1))
        self.scroll = ttk.Scrollbar(text_wrap, orient="vertical",
                                    command=self.text.yview)
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=self.scroll.set)

        # 选项行
        options = ttk.Frame(outer)
        options.grid(row=2, column=0, sticky="ew", pady=(0, self._s(8)))
        ttk.Label(options, text="速度").pack(side="left")
        self.speed_var = tk.StringVar(value=DEFAULT_SPEED)
        self.speed_buttons = []
        for key, preset in SPEED_PRESETS.items():
            rb = ttk.Radiobutton(
                options,
                text=f"{preset['name']}（{preset['key_delay']:g} 秒/字）",
                value=key,
                variable=self.speed_var,
                command=self._refresh_metrics,
            )
            rb.pack(side="left", padx=(self._s(10), 0))
            self.speed_buttons.append(rb)

        ttk.Separator(options, orient="vertical").pack(
            side="left", fill="y", padx=(self._s(14), self._s(12)),
            pady=self._s(2),
        )
        ttk.Label(options, text="开始前等待").pack(side="left")
        self.delay_var = tk.StringVar(value=f"{self.DEFAULT_DELAY:g}")
        self.delay_entry = ttk.Entry(options, textvariable=self.delay_var,
                                     width=6, justify="center")
        self.delay_entry.pack(side="left", padx=(self._s(6), 0))
        ttk.Label(options, text="秒（可填小数）").pack(
            side="left", padx=(self._s(4), 0),
        )

        self.metrics_var = tk.StringVar()
        ttk.Label(options, textvariable=self.metrics_var,
                  style="Hint.TLabel").pack(side="right")

        # 状态 + 进度
        self.status_var = tk.StringVar(value="就绪：把内容粘贴到上方，点击「开始输入」。")
        self.status_label = ttk.Label(outer, textvariable=self.status_var,
                                      style="Status.TLabel", anchor="w")
        self.status_label.grid(row=3, column=0, sticky="ew")
        self.status_label.configure(foreground=self.HINT_FG)

        self.progress = ttk.Progressbar(outer, mode="determinate",
                                        maximum=1, value=0)
        self.progress.grid(row=4, column=0, sticky="ew",
                           pady=(self._s(4), self._s(10)))

        # 按钮行
        buttons = ttk.Frame(outer)
        buttons.grid(row=5, column=0, sticky="ew")
        self.paste_btn = ttk.Button(buttons, text="粘贴剪贴板",
                                    command=self.paste_clipboard)
        self.paste_btn.pack(side="left")
        self.clear_btn = ttk.Button(buttons, text="清空", command=self.clear_text)
        self.clear_btn.pack(side="left", padx=(self._s(8), 0))

        self.start_btn = ttk.Button(buttons, text="开始输入 (Ctrl+Enter)",
                                    style="Primary.TButton",
                                    command=self.start_typing)
        self.start_btn.pack(side="right")
        self.stop_btn = ttk.Button(buttons, text="停止 (Esc)",
                                   command=self.stop_typing)
        self.stop_btn.pack(side="right", padx=(0, self._s(8)))

        # 右键菜单
        self.menu = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei UI", 9))
        self.menu.add_command(label="撤销  Ctrl+Z", command=self._undo)
        self.menu.add_command(label="重做  Ctrl+Y", command=self._redo)
        self.menu.add_separator()
        self.menu.add_command(label="剪切  Ctrl+X",
                              command=lambda: self._menu_event("<<Cut>>"))
        self.menu.add_command(label="复制  Ctrl+C",
                              command=lambda: self._menu_event("<<Copy>>"))
        self.menu.add_command(label="粘贴  Ctrl+V",
                              command=self.paste_clipboard)
        self.menu.add_separator()
        self.menu.add_command(label="全选  Ctrl+A",
                              command=self._select_all)

    def _bind_events(self):
        self.text.bind("<<Modified>>", self._on_text_modified)
        self.text.bind("<Button-3>", self._show_context_menu)
        # Tk 的 Text 默认不带这些 Windows 习惯快捷键，需要自己接上
        self.text.bind("<Control-v>", self.paste_clipboard)
        self.text.bind("<Control-V>", self.paste_clipboard)
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-z>", self._undo)
        self.text.bind("<Control-Z>", self._redo)
        self.text.bind("<Control-y>", self._redo)
        self.delay_entry.bind("<Return>", self._on_start_shortcut)
        self.root.bind_all("<Control-Return>", self._on_start_shortcut)
        self.root.bind_all("<Escape>", self._on_escape)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self._on_tk_error

    # ---------- 状态 / 按钮 ----------
    def _set_status(self, text, kind="info"):
        colors = {
            "info": self.TEXT_FG,
            "ok": self.OK_FG,
            "warn": self.WARN_FG,
            "error": self.ERROR_FG,
        }
        self.status_var.set(text)
        self.status_label.configure(foreground=colors.get(kind, self.TEXT_FG))

    @staticmethod
    def _enable(widget, enabled):
        widget.state(["!disabled"] if enabled else ["disabled"])

    def _content(self):
        return self.text.get("1.0", "end-1c")

    def _refresh_metrics(self, _event=None):
        text = self._content()
        chars = len(text)
        lines = text.count("\n") + 1 if text else 0
        preset = SPEED_PRESETS[self.speed_var.get()]
        estimate = (chars * preset["key_delay"]
                    + text.count("\n") * preset["enter_delay"])
        self.metrics_var.set(
            f"{chars} 字符 · {lines} 行 · 预计 {format_duration(estimate)}"
        )
        self._refresh_controls()

    def _refresh_controls(self):
        idle = self._phase == "idle"
        has_text = bool(self._content())
        self.text.configure(state="normal" if idle else "disabled")
        self._enable(self.start_btn, idle and has_text)
        self._enable(self.paste_btn, idle)
        self._enable(self.clear_btn, idle and has_text)
        for rb in self.speed_buttons:
            self._enable(rb, idle)
        self._enable(self.delay_entry, idle)
        self._enable(self.stop_btn, not idle)

    # ---------- 文本编辑 ----------
    def _on_text_modified(self, _event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._refresh_metrics()

    def _show_context_menu(self, event):
        if self._phase != "idle":
            return "break"
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def _menu_event(self, event_name):
        self.text.focus_set()
        self.text.event_generate(event_name)
        return "break"

    def _undo(self, _event=None):
        if self._phase != "idle":
            return "break"
        try:
            self.text.edit_undo()
        except tk.TclError:
            pass
        self.text.edit_modified(False)
        self._refresh_metrics()
        return "break"

    def _redo(self, _event=None):
        if self._phase != "idle":
            return "break"
        try:
            self.text.edit_redo()
        except tk.TclError:
            pass
        self.text.edit_modified(False)
        self._refresh_metrics()
        return "break"

    def _select_all(self, _event=None):
        if self._phase != "idle":
            return "break"
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.mark_set("insert", "1.0")
        self.text.focus_set()
        return "break"

    def paste_clipboard(self, _event=None):
        if self._phase != "idle":
            return "break"
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            self._set_status("剪贴板里没有可粘贴的文本。", "warn")
            self.root.bell()
            return "break"
        if not data:
            self._set_status("剪贴板是空的。", "warn")
            self.root.bell()
            return "break"

        data = normalize_text(data)
        self.text.edit_separator()
        # 关闭自动分隔符，让「替换选区 + 插入」成为一步可撤销的操作
        autosep = self.text.cget("autoseparators")
        self.text.configure(autoseparators=False)
        try:
            if self.text.tag_ranges("sel"):
                self.text.delete("sel.first", "sel.last")
            self.text.insert("insert", data)
        finally:
            self.text.configure(autoseparators=autosep)
            self.text.edit_separator()
        self.text.see("insert")
        self.text.focus_set()
        self._refresh_metrics()
        self._set_status(f"已粘贴 {len(data)} 个字符。", "ok")
        return "break"

    def clear_text(self):
        if self._phase != "idle" or not self._content():
            return "break"
        self.text.edit_separator()
        self.text.delete("1.0", "end")
        self.text.edit_separator()
        self.text.focus_set()
        self._refresh_metrics()
        self._set_status("已清空。可随时 Ctrl+Z 撤销。", "info")
        return "break"

    # ---------- 开始 / 停止 ----------
    def _on_start_shortcut(self, _event=None):
        self.start_typing()
        return "break"

    def _on_escape(self, _event=None):
        if self._phase == "idle":
            return "break"
        self.stop_typing()
        return "break"

    def _read_delay(self):
        """读取「开始前等待」：大于 0 的有限数字即可，支持小数"""
        raw = self.delay_var.get().strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return value

    def start_typing(self, _event=None):
        if self._phase != "idle":
            return "break"

        text = normalize_text(self._content())
        if not text.strip():
            self._set_status("输入内容为空，先把文字粘贴到上方。", "warn")
            self.root.bell()
            self.text.focus_set()
            return "break"

        delay = self._read_delay()
        if delay is None:
            self._set_status(
                "「开始前等待」要填大于 0 的数字，可以带小数（例如 0.5、3、10）。",
                "error",
            )
            self.root.bell()
            self.delay_entry.focus_set()
            self.delay_entry.selection_range(0, "end")
            return "break"

        preset = SPEED_PRESETS[self.speed_var.get()]
        self._text = text
        self._total = len(text)
        self._typed = 0
        self._last_shown = -1
        self._key_delay = preset["key_delay"]
        self._enter_delay = preset["enter_delay"]
        self._cancel.clear()
        self._phase = "countdown"
        self._countdown_deadline = time.monotonic() + delay
        self.progress.configure(maximum=max(1, self._total), value=0)
        self._refresh_controls()
        message = (
            f"{preset['name']}模式：{format_wait(delay)} 秒后开始输入，"
            "请立即把光标放到目标窗口…"
        )
        self._last_countdown_text = message
        self._set_status(message, "info")
        self._countdown_job = self.root.after(self.COUNTDOWN_TICK_MS,
                                              self._countdown_tick)
        return "break"

    def _countdown_tick(self):
        self._countdown_job = None
        if self._phase != "countdown":
            return
        remaining = self._countdown_deadline - time.monotonic()
        if remaining <= 0:
            self._begin_typing()
            return
        message = f"还有 {format_wait(remaining)} 秒，请把光标放到目标窗口…"
        if message != self._last_countdown_text:
            self._last_countdown_text = message
            self._set_status(message, "info")
        self._countdown_job = self.root.after(self.COUNTDOWN_TICK_MS,
                                              self._countdown_tick)

    def _foreground_belongs_to_self(self):
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return pid.value == os.getpid()
        except Exception:
            return False

    def _begin_typing(self):
        if self._phase != "countdown":
            return
        # 防止倒计时结束后焦点还在本窗口：那样会把字打回自己的输入框
        if self._foreground_belongs_to_self():
            self._phase = "idle"
            self.progress.configure(value=0)
            self._refresh_controls()
            self._set_status(
                "前台窗口还是本程序。请先把光标放到目标窗口，再重新点「开始输入」。",
                "error",
            )
            self.root.bell()
            return

        self._phase = "typing"
        self._started_at = time.monotonic()
        self._refresh_controls()
        self._set_status("正在输入…", "info")
        threading.Thread(
            target=self._typing_worker,
            name="typing-worker",
            daemon=True,
        ).start()
        self._poll_job = self.root.after(self.POLL_MS, self._poll_worker)

    def _typing_worker(self):
        try:
            type_text(
                self._text,
                self._key_delay,
                self._enter_delay,
                cancel=self._cancel,
                on_progress=self._on_typed_progress,
            )
        except TypingCancelled:
            self._results.put(("cancelled", ""))
        except Exception as exc:  # noqa: BLE001 - 界面上要显示所有失败原因
            self._results.put(("error", str(exc)))
        else:
            self._results.put(("done", ""))

    def _on_typed_progress(self, pos):
        # 工作线程只写这个整数，由主线程轮询读取，避免跨线程操作 Tk
        self._typed = pos

    def _poll_worker(self):
        self._poll_job = None
        if self._phase != "typing":
            return

        done = min(self._typed, self._total)
        self.progress.configure(value=done)
        if done != self._last_shown:
            self._last_shown = done
            percent = int(done * 100 / self._total) if self._total else 100
            self._set_status(
                f"正在输入… {done}/{self._total} 字符（{percent}%）",
                "info",
            )

        try:
            kind, detail = self._results.get_nowait()
        except queue.Empty:
            self._poll_job = self.root.after(self.POLL_MS, self._poll_worker)
        else:
            self._finish_typing(kind, detail)

    def _finish_typing(self, kind, detail):
        self._phase = "idle"
        self._cancel.clear()
        if kind == "done":
            self.progress.configure(value=self._total)
            elapsed = time.monotonic() - self._started_at
            self._set_status(
                f"输入完成：共 {self._total} 字符，用时 {format_duration(elapsed)}。",
                "ok",
            )
        elif kind == "cancelled":
            self.progress.configure(value=min(self._typed, self._total))
            self._set_status(
                f"已停止：已输入 {self._typed}/{self._total} 字符。",
                "warn",
            )
        else:
            self.progress.configure(value=min(self._typed, self._total))
            self._set_status(f"输入出错：{detail}", "error")
        self._refresh_controls()

    def stop_typing(self, _event=None):
        if self._phase == "countdown":
            if self._countdown_job is not None:
                self.root.after_cancel(self._countdown_job)
                self._countdown_job = None
            self._phase = "idle"
            self.progress.configure(value=0)
            self._refresh_controls()
            self._set_status("已取消。", "warn")
        elif self._phase == "typing":
            self._cancel.set()
            self._enable(self.stop_btn, False)
            self._set_status("正在停止…", "warn")
        return "break"

    # ---------- 关闭 / 兜底 ----------
    def on_close(self):
        if self._phase == "idle":
            self.root.destroy()
            return
        if messagebox.askyesno(
            "退出",
            "正在输入中。退出会立即停止输入，确定要退出吗？",
            parent=self.root,
        ):
            self._cancel.set()
            self.root.destroy()

    def _on_tk_error(self, exc, val, tb):
        text = _log_exception(exc, val, tb)
        try:
            messagebox.showerror("程序错误", text, parent=self.root)
        except Exception:
            pass


def main():
    if sys.platform != "win32":
        print("本程序仅支持 Windows。")
        return 1
    enable_dpi_awareness()
    set_taskbar_identity()
    root = tk.Tk()
    KeyboardInputApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
