# -*- coding: utf-8 -*-
"""
在桌面创建 input.txt，用户写入内容后回到控制台输入 y，
脚本等待 3 秒，读取文件内容并模拟键盘输入到当前光标所在的输入框。

启动时先选择输入速度：1 极速 / 2 保守。

【Unicode 直发版】
所有字符都通过 SendInput 的 Unicode 模式直接发送，完全绕过输入法：
不需要切换中英文，不依赖输入法选词（没有同音词/多音字问题），也不使用剪贴板。
只依赖 Python 标准库，无需安装任何第三方包。
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

# Windows 控制台默认用 GBK，输出 emoji 会直接抛 UnicodeEncodeError。统一切到 UTF-8。
if sys.platform == "win32":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ============== 速度档位 ==============
# 每次运行程序时手动选择。值分别是（每个字符之间的间隔秒，换行后的等待秒）。
# 目标程序丢字就换更慢的档位。
SPEED_PRESETS = {
    "1": ("极速", 0.01, 0.015),
    "2": ("保守", 0.04, 0.05),
}

# 下面两个变量由 choose_speed() 在启动时按选择填入，输入函数直接读它们
KEY_DELAY = 0.01
ENTER_DELAY = 0.015
# ==================================

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
            "若目标程序以管理员身份运行，本脚本也需要用管理员身份运行。"
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


# ---------- 平台相关：获取桌面路径 ----------
def get_desktop_path():
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            )
            path, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def create_input_file():
    path = os.path.join(get_desktop_path(), "input.txt")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
    return path


# ---------- 核心输入函数 ----------
def newline_to(indent):
    """换行，并把编辑器自动插入的缩进换成源文本里的真实缩进"""
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
    time.sleep(ENTER_DELAY)


def type_text(text):
    for i, line in enumerate(text.split("\n")):
        indent = line[:len(line) - len(line.lstrip())]
        if i > 0:
            newline_to(indent)
        for ch in line[len(indent):]:
            if ch != "\r":
                send_char(ch)
                time.sleep(KEY_DELAY)


# ---------- 读文件 ----------
def read_file(path, retries=5):
    for _ in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except PermissionError:
            time.sleep(0.5)
    raise IOError("文件被占用或无法读取")


# ---------- 主流程 ----------
def choose_speed():
    global KEY_DELAY, ENTER_DELAY

    print("请选择输入速度：")
    print("   1) 极速  (每字 0.01 秒，换行 0.015 秒)")
    print("   2) 保守  (每字 0.04 秒，换行 0.05 秒)")

    while True:
        try:
            cmd = input("\n速度 [1/2] >>> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出")
            return False

        if cmd in SPEED_PRESETS:
            name, KEY_DELAY, ENTER_DELAY = SPEED_PRESETS[cmd]
            print(f"✅ 已选择「{name}」：每字 {KEY_DELAY} 秒，换行 {ENTER_DELAY} 秒")
            return True

        print("请输入 1 或 2")


def main():
    path = create_input_file()

    print("=" * 56)
    if not choose_speed():
        return
    print("=" * 56)
    print(f"📄  文件位置: {path}")
    print("   1) 在该文件里写入你要输入的内容并保存 (Ctrl+S)")
    print("   2) 回到本窗口输入 y 并回车")
    print("   3) 脚本等 3 秒后自动模拟键盘输入")
    print("   输入 q 退出")
    print("=" * 56)

    while True:
        try:
            cmd = input("\n>>> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出")
            break

        if cmd == "q":
            print("已退出")
            break
        if cmd != "y":
            print("请输入 y 或 q")
            continue

        print("⏳ 3 秒后开始输入，请把光标放到目标输入框里……")
        for i in (3, 2, 1):
            print(f"   {i} ...")
            time.sleep(1)

        try:
            content = read_file(path)
        except Exception as e:
            print(f"❌ 读取文件失败: {e}")
            continue

        if not content.strip():
            print("⚠️  文件内容为空，跳过。")
            continue

        print("⌨️  开始模拟输入……")
        try:
            type_text(content)
        except Exception as e:
            print(f"❌ 输入过程中出错: {e}")
            continue
        print("✅ 输入完成！")


if __name__ == "__main__":
    main()
