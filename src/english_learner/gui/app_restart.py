"""重启应用：导入数据库后用它立即生效。

开发环境（`uv run english-learner`）下用同一个解释器重新执行
`python -m english_learner`；PyInstaller 打包后直接重新拉起 exe。
新进程与当前进程完全独立，当前进程随后由调用方正常退出。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess


def restart_application() -> bool:
    """拉起一个新进程；成功返回 True。失败时由调用方提示用户手动重启。"""
    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, []
    else:
        program, arguments = sys.executable, ["-m", "english_learner"]

    result = QProcess.startDetached(program, arguments, str(Path.cwd()))
    # PySide6 返回 (success, pid)，老版本只返回 bool。
    success = result[0] if isinstance(result, tuple) else result
    return bool(success)
