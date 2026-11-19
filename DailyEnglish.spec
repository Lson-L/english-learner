# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 Daily English 打成单文件 exe。

用法（项目根目录）：

    uv run pyinstaller --noconfirm --clean DailyEnglish.spec

产物：`dist/DailyEnglish.exe`（约 100–150MB，双击即用，不需要装 Python）。

Java 对照：≈ maven-shade-plugin + launch4j 的合体 —— 把解释器、依赖、
字体资源全部塞进一个可执行文件。

几个关键点：
- `pathex` 指向 `src/`：项目是 src 布局 + editable 安装，直接让 PyInstaller
  从源码目录找包，不依赖 editable 的 finder；
- `datas` 把内置字体原样放进包内 `english_learner/assets`，和
  `gui/fonts.py` 里 `Path(__file__).parent.parent / "assets"` 的推算保持一致；
- `console=False`：GUI 程序不留黑窗；未捕获异常由 app.py 的 excepthook
  弹窗 + 写 `%APPDATA%/EnglishLearner/logs/app.log`。
"""

from pathlib import Path

# SPECPATH 是 spec 文件所在目录（PyInstaller 预置的全局变量），即项目根。
ROOT = Path(SPECPATH).resolve()

a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ROOT / "src" / "english_learner" / "assets"), "english_learner/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 测试/静态检查工具不该进包；tkinter 是 PySide6 hook 有时会顺带扫到的无关模块。
    excludes=["tkinter", "pytest", "ruff", "_pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DailyEnglish",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
