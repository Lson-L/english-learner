"""应用入口：日志、启动遮罩、QApplication、首次配置判断、主窗口。

启动顺序（性能敏感，改动前先读）：
1. 顶层只导入 Qt 与轻量模块（字体 / 主题 / 配置），尽快把启动遮罩显示出来；
2. 遮罩可见后再导入 openai SDK 与全部页面 / 弹窗模块（冷启动 1~2 秒）；
3. 备份、建库、构建主窗口，最后收起遮罩。

启动方式：
    uv run english-learner
    uv run python -m english_learner
    uv run python -m english_learner --reconfigure   # 强制重新配置 AI 服务

Java 对照：
- main() ≈ Spring Boot 的 main 方法；
- 首次配置判断 ≈ 启动时检查配置文件，不存在就走 Onboarding 引导；
- StartupSplash ≈ java.awt.SplashScreen，在主窗口就绪前先给用户反馈。
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
from .gui.i18n import install as install_button_translator
from .gui.i18n import set_low_profile
from .gui.splash import StartupSplash
from .gui.theme import build_stylesheet
from .services.config_store import AIConfig, ConfigError, ConfigStore, log_dir

logger = logging.getLogger(__name__)

# 遮罩上的阶段文案：默认 / 低调两套（低调模式全英文，与主窗口保持一致）。
_STARTUP_MESSAGES: dict[bool, tuple[str, str, str]] = {
    False: ("正在加载界面组件…", "正在检查数据…", "正在准备主界面…"),
    True: ("loading components…", "checking data…", "preparing workspace…"),
}


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
    # 按钮文案翻译要在任何窗口（含配置向导）创建之前生效。
    install_button_translator(app)

    store = ConfigStore()
    # 字体/主题是纯界面偏好：文件缺失或字段异常都回退默认值，不能阻塞启动。
    ui = store.load_ui()
    set_low_profile(ui.low_profile)
    app.setApplicationDisplayName("Workspace" if ui.low_profile else "Daily English")
    app.setStyleSheet(build_stylesheet(ui.english_font, low_profile=ui.low_profile))
    _install_excepthook()

    # 先给反馈，再干活：下面这些重依赖的导入放在遮罩显示之后。
    messages = _STARTUP_MESSAGES[ui.low_profile]
    splash = StartupSplash(low_profile=ui.low_profile)
    splash.show()
    app.processEvents()

    try:
        splash.set_message(messages[0], 35)
        app.processEvents()
        # 重依赖：openai SDK（含 pydantic / httpx2）与全部页面、弹窗模块。
        # 冷启动约 1~2 秒，期间遮罩已经显示，用户不会面对“点了没反应”。
        from .gui.main_window import MainWindow
        from .gui.onboarding import OnboardingDialog
        from .gui.workers import wait_for_orphans
        from .services.backup_service import run_startup_backup
        from .services.database_transfer import apply_pending_import
        from .storage.db import Database

        existing: AIConfig | None = None
        try:
            existing = store.load()
        except ConfigError as exc:
            logger.warning("配置文件读取失败：%s", exc)
            splash.hide()
            QMessageBox.warning(
                None,
                "配置文件损坏",
                f"{exc}\n\n将重新进入配置向导。",
            )
            splash.show()
            app.processEvents()

        config = None if args.reconfigure else existing
        if config is None:
            # 配置向导是模态对话框，遮罩先收起，避免一直压在它上面。
            splash.hide()
            dialog = OnboardingDialog(store, initial=existing)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved_config is None:
                logger.info("用户取消了首次配置，退出")
                splash.close()
                return 0
            config = dialog.saved_config
            splash.show()
            app.processEvents()

        splash.set_message(messages[1], 60)
        app.processEvents()

        # 迁移前先备份：即使新版本迁移出问题，也有启动前的完整数据可回退。
        # 备份失败只记日志，不能影响启动（见 backup_service.run_startup_backup）。
        run_startup_backup()

        # 设置页的「导入数据库」只做暂存；真正替换放在这里——此时还没有任何连接，
        # 不会出现运行中替换 SQLite 文件的问题。
        try:
            imported = apply_pending_import()
        except Exception as exc:  # noqa: BLE001 - 导入失败必须保留原数据并继续启动
            logger.exception("启动时应用待导入数据库失败")
            splash.hide()
            QMessageBox.warning(None, "导入数据库失败", f"{exc}\n\n已保留原有数据。")
            splash.show()
            app.processEvents()
        else:
            if imported is not None:
                logger.info("启动时已导入数据库；导入前备份：%s", imported.safety_backup)

        splash.set_message(messages[2], 85)
        app.processEvents()
        db = Database()
        window = MainWindow(config, store, db)
        window.show()
    except BaseException:
        # 启动失败时先收起遮罩，否则置顶窗口会压住 excepthook 的错误弹窗。
        splash.close()
        raise

    splash.finish(window)
    # 首次使用（profile 为空）时弹测评；用户取消也不影响进入主界面。
    window.maybe_show_assessment()
    exit_code = app.exec()
    # `QApplication.quit()`（导入数据库后的“立即重启”）不会触发 closeEvent，
    # 这里补一次，保证窗口大小 / 最大化状态不会丢。
    window.save_window_state()
    # 被取消但仍在等 HTTP 返回的任务可能还会写库，先等它们结束再关连接。
    wait_for_orphans()
    # 正常退出时显式释放连接，WAL 能干净合并，下次启动的备份/导入不受影响。
    db.close()
    return exit_code
