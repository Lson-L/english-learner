# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 Daily English 打成可双击运行的桌面程序。

用法（项目根目录，必须用 Windows x64 + Python 3.13 构建）：

    uv run pyinstaller --noconfirm --clean DailyEnglish.spec

默认产物：`dist/DailyEnglish.exe`（单文件，约 36MB）。
想要启动更快、杀软误报更少的目录版：

    DAILYENGLISH_ONEDIR=1 uv run pyinstaller --noconfirm --clean DailyEnglish.spec

产物：`dist/DailyEnglish/DailyEnglish.exe` + `_internal/`（整个目录一起分发）。

Java 对照：≈ maven-shade-plugin + launch4j 的合体 —— 把解释器、依赖、
字体资源全部塞进一个可执行文件；onedir 模式 ≈ 打一个带 lib/ 的发行目录。

几个关键点：
- `pathex` 指向 `src/`：项目是 src 布局 + editable 安装，直接让 PyInstaller
  从源码目录找包，不依赖 editable 的 finder；
- `datas` 把内置字体原样放进包内 `english_learner/assets`，和
  `gui/fonts.py` 里 `Path(__file__).parent.parent / "assets"` 的推算保持一致；
- `console=False`：GUI 程序不留黑窗；未捕获异常由 app.py 的 excepthook
  弹窗 + 写 `%APPDATA%/EnglishLearner/logs/app.log`。
- 裁剪清单见下方注释。界面只用到 PySide6 的 Core / Gui / Widgets 三个模块，
  其余 Qt 模块都是 QtGui 插件连带进来的；每次升级 PySide6 后应重新跑一次
  打包并做冒烟启动，确认没有裁掉运行时真正需要的插件。

@Author lson
"""

import os
from pathlib import Path

# SPECPATH 是 spec 文件所在目录（PyInstaller 预置的全局变量），即项目根。
ROOT = Path(SPECPATH).resolve()

# 目录版开关：单文件解压到临时目录需要 2~4 秒，目录版没有这一步。
ONEDIR = os.environ.get("DAILYENGLISH_ONEDIR") == "1"

# ---------------------------------------------------------------------------
# 裁剪清单
# ---------------------------------------------------------------------------

# 1) 打包时用不到、也不该混进发行包的 Python 模块。
EXCLUDED_MODULES = [
    "tkinter",      # 项目只用 PySide6
    "pytest",       # 测试工具
    "_pytest",
    "ruff",
    "coverage",
    "pygments",     # 只被 httpx2._main（命令行调试入口）使用，应用不会 import
    "setuptools",   # 运行时不需要构建工具；dist-info 已由 PyInstaller 收集
    "pkg_resources",
    "distutils",
]

# 2) 用不到的 Qt 二进制。
#    Core / Gui / Widgets 是运行必需；下面这些只会被“用不上的插件”连带进来：
#    - Qml / Quick / VirtualKeyboard：virtualkeyboard 输入法插件连带；
#    - Pdf：imageformats/qpdf 插件连带；
#    - Svg：qsvg / qsvgicon 插件连带；
#    - Network：TLS 插件连带（应用的联网走 Python httpx2，不走 Qt）；
#    - OpenGL / opengl32sw：界面是纯 Widgets，没有 QOpenGLWidget；
#      opengl32sw.dll 是软件 OpenGL 兜底，单个就占 20MB 解压体积。
QT_DROP_BINARIES = {
    "PySide6/opengl32sw.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/QtNetwork.pyd",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/Qt6Svg.dll",
    "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlModels.dll",
    "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
}

# Qt 插件只保留三个：平台插件（没有它窗口起不来）、Win11 风格、ico 图标解码。
QT_KEEP_PLUGINS = {
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/styles/qmodernwindowsstyle.dll",
    "PySide6/plugins/imageformats/qico.dll",
}

# 3) 不属于本项目的 DLL。
#    - libcrypto/libssl-3-x64：PyInstaller 在构建机 PATH 上从 Git for Windows
#      抓来的 QtNetwork OpenSSL 后端依赖，去掉 QtNetwork 后不再需要；
#    - ucrtbase / api-ms-win-*：Windows 10+ 由系统提供，原本是从 Miniconda
#      目录误抓的，跟着 exe 走反而可能和使用者系统版本不一致。
DROP_BINARY_NAMES = {
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "ucrtbase.dll",
}


def _keep_binary(entry):
    """entry ≈ (包内目标路径, 源路径, 类型)，路径统一成 / 再比较。"""
    dest = entry[0].replace("\\", "/")
    name = dest.rsplit("/", 1)[-1]
    if dest in QT_DROP_BINARIES or name in DROP_BINARY_NAMES:
        return False
    if name.lower().startswith("api-ms-win-"):
        return False
    if dest.startswith("PySide6/plugins/") and dest not in QT_KEEP_PLUGINS:
        return False
    return True


# 4) `gui/pages/__init__.py` 用模块级 `__getattr__` + `importlib.import_module`
#    按需加载页面（避免循环导入），PyInstaller 的静态分析看不到这些模块名，
#    不显式列出就会在运行时 ModuleNotFoundError。新增页面时记得同步加到这里。
HIDDEN_IMPORTS = [
    "english_learner.gui.pages.reader",
    "english_learner.gui.pages.review",
    "english_learner.gui.pages.settings",
    "english_learner.gui.pages.today",
    "english_learner.gui.pages.vocabulary",
]


a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ROOT / "src" / "english_learner" / "assets"), "english_learner/assets")],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if _keep_binary(entry)]

pyz = PYZ(a.pure)

# 两个模式共用同一份 EXE 参数。
_exe_kwargs = dict(
    name="DailyEnglish",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,             # Qt DLL 上 UPX 容易损坏并显著提高杀软误报，别开
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_exe_kwargs)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="DailyEnglish",
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **_exe_kwargs)
