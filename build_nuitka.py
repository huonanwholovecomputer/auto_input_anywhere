# -*- coding: utf-8 -*-
"""
用 Nuitka 把脚本编译打包成单文件 exe（体积约为 PyInstaller 版的一半）。

前置：
    pip install nuitka zstandard
首次构建时 Nuitka 会自动下载它认可的 MinGW-w64 编译器（约 100+ MB，
一次性，之后走缓存），需要联网。本机已有的其它 gcc 会被忽略。

用法：
    python build_nuitka.py

产物：
    dist/键盘输入助手.exe
与 PyInstaller 的 dist/键盘输入助手.exe 同一个路径；想保留两份的话，
用 pyinstaller --distpath dist_pyinstaller 键盘输入助手.spec 单独输出。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "模拟键盘输入指定内容的脚本.py"
ICON = ROOT / "assets" / "icon.ico"
OUTPUT_DIR = ROOT / "dist"

# 下面的名称/版本要和 模拟键盘输入指定内容的脚本.py 里的 APP_NAME/APP_VERSION
# 以及 version_info.txt 保持一致。
PRODUCT_NAME = "键盘输入助手"
FILE_VERSION = "0.3.0.0"
PRODUCT_VERSION = "0.3.0"
DESCRIPTION = "键盘输入助手 — 图形界面的 Unicode 直发键盘输入工具"
COMPANY = "huonanwholovecomputer"
COPYRIGHT = "Copyright (C) 2026 huonanwholovecomputer"

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    "--mode=onefile",
    # tkinter 的 Tcl/Tk 数据文件
    "--enable-plugin=tk-inter",
    # 窗口程序，不要黑色控制台
    "--windows-console-mode=disable",
    # exe 属性
    f"--company-name={COMPANY}",
    f"--product-name={PRODUCT_NAME}",
    f"--file-version={FILE_VERSION}",
    f"--product-version={PRODUCT_VERSION}",
    f"--file-description={DESCRIPTION}",
    f"--copyright={COPYRIGHT}",
    # 图标：写进 exe 资源（资源管理器/任务栏），同时把 ico 带进包内供窗口使用
    f"--windows-icon-from-ico={ICON}",
    f"--include-data-files={ICON}=assets/{ICON.name}",
    # 首次构建允许自动下载 MinGW-w64 / depends.exe
    "--assume-yes-for-downloads",
    "--mingw64",
    # 体积/速度优化
    "--lto=yes",
    "--python-flag=no_docstrings",
    # 收尾清理中间目录
    "--remove-output",
    f"--output-dir={OUTPUT_DIR.name}",
    f"--output-filename={PRODUCT_NAME}.exe",
    SCRIPT.name,
]


def _check_dependencies() -> bool:
    missing = []
    for module, hint in (
        ("nuitka", "pip install nuitka"),
        ("zstandard", "pip install zstandard"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(f"{module}（{hint}）")
    if missing:
        print("缺少依赖，请先安装：")
        for item in missing:
            print(f"  - {item}")
        print("一次性安装两者：pip install nuitka zstandard")
        print("注意：没有 zstandard 时 onefile 不会压缩，产物会大 3 倍多。")
        return False
    return True


def _exe_is_locked(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("rb+"):
            return False
    except OSError:
        return True


def main() -> int:
    if not SCRIPT.exists():
        print(f"找不到主脚本：{SCRIPT}")
        return 1
    if not ICON.exists():
        print(f"找不到图标：{ICON}")
        print("先生成图标：python tools/make_icon.py")
        return 1
    if not _check_dependencies():
        return 1

    exe = OUTPUT_DIR / f"{PRODUCT_NAME}.exe"
    if _exe_is_locked(exe):
        print(f"{exe} 正在运行或被占用，请先关闭程序再重新构建。")
        return 1

    print("Nuitka 参数：")
    for arg in NUITKA_ARGS[2:]:
        print(f"  {arg}")
    print()
    try:
        subprocess.run(NUITKA_ARGS, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n构建失败，退出码 {exc.returncode}")
        return exc.returncode or 1
    except KeyboardInterrupt:
        print("\n已中断")
        return 130

    if not exe.exists():
        print(f"\n构建命令成功，但没有找到产物：{exe}")
        return 1
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n完成：{exe}")
    print(f"体积：{size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
