"""应用入口：日志、QApplication、首次配置判断、主窗口。

启动方式：
    uv run english-learner
    uv run python -m english_learner
    uv run python -m english_learner --reconfigure   # 强制重新配置 AI 服务

Java 对照：
- main() ≈ Spring Boot 的 main 方法；
- 首次配置判断 ≈ 启动时检查配置文件，不存在就走 Onboarding 引导。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from . import __version__
from .gui.fonts import load_application_fonts
from .gui.main_window import MainWindow
from .gui.onboarding import OnboardingDialog
from .gui.theme import build_stylesheet
from .services.config_store import AIConfig, ConfigError, ConfigStore, log_dir
from .storage.db import Database

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """日志同时写控制台和轮转文件，方便排查用户环境问题。"""
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        directory / "app.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)


def _install_excepthook() -> None:
    """把未捕获异常写日志并弹窗，避免打包后“窗口一闪就没了”。"""

    def hook(exc_type, exc, traceback) -> None:
        logger.critical("未捕获异常", exc_info=(exc_type, exc, traceback))
        QMessageBox.critical(
            None,
            "程序出错",
            f"{exc_type.__name__}: {exc}\n\n详细信息已写入日志文件。",
        )

    sys.excepthook = hook


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="english-learner",
        description="Daily English · 每日英语学习桌面应用",
    )
    parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="强制打开 AI 配置向导（即使已有配置）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    # argv 为 None 时 argparse 读 sys.argv；入口点调用时 sys.argv[0] 是 exe 路径。
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    setup_logging()
    logger.info("启动 Daily English v%s", __version__)

    # 只把程序名传给 Qt，避免它把 --reconfigure 当作自己的参数。
    app = QApplication([sys.argv[0] if sys.argv else "english-learner"])
    app.setApplicationName("Daily English")
    app.setApplicationDisplayName("Daily English")
    app.setOrganizationName("lson")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    # 字体必须在应用样式表之前注册，否则 QSS 里的族名会被解析成“不存在的字体”而回退。
    load_application_fonts()

    store = ConfigStore()
    # 英文字体是纯界面偏好：文件缺失或字段异常都回退默认值，不能阻塞启动。
    app.setStyleSheet(build_stylesheet(store.load_ui().english_font))
    _install_excepthook()

    existing: AIConfig | None = None
    try:
        existing = store.load()
    except ConfigError as exc:
        logger.warning("配置文件读取失败：%s", exc)
        QMessageBox.warning(
            None,
            "配置文件损坏",
            f"{exc}\n\n将重新进入配置向导。",
        )

    config = None if args.reconfigure else existing
    if config is None:
        dialog = OnboardingDialog(store, initial=existing)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved_config is None:
            logger.info("用户取消了首次配置，退出")
            return 0
        config = dialog.saved_config

    db = Database()
    window = MainWindow(config, store, db)
    window.show()
    # 首次使用（profile 为空）时弹测评；用户取消也不影响进入主界面。
    window.maybe_show_assessment()
    return app.exec()
