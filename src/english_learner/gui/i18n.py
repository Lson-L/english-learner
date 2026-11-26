"""低调模式的按钮英文文案。

只翻译 `QPushButton` 的显示文本：中文原文作为 key，低调模式下查表换成英文；
查不到就原样保留，避免漏译时按钮变成空白。页面标题、正文、状态提示等
其它控件不受影响。

实现方式：
- patch `QPushButton.setText`，页面代码照旧写中文，显示时统一翻译；
- 保存中文原文到 `_source_text`，切回默认主题时能还原；
- C++ 构造参数写进去的初始文本不经过 setText，用一个应用级事件过滤器
  在按钮首次 show 时补翻译。

Java 对照：≈ 一个只作用于 Button 的 ResourceBundle，切语言时刷新一次控件树。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget

# 覆盖 QPushButton / QCheckBox / QRadioButton 等所有按钮类控件。

# 低调模式开关。MainWindow 构造/切主题时会同步，保证全局一致。
_low_profile = False

# 中文原文 → 英文。只放按钮文案，别把状态提示塞进来（那些不走 QPushButton）。
BUTTON_EN: dict[str, str] = {
    # 导航
    "今日": "Today",
    "阅读": "Docs",
    "生词本": "Words",
    "生词": "Words",
    "复习": "Review",
    "统计": "Stats",
    "设置": "Settings",
    # 阅读页
    "生成今日文章": "Generate article",
    "生成新文章": "Generate article",
    "开始阅读": "Start reading",
    "阅读完成": "Finish reading",
    "回译练习": "Back-translation",
    "换一篇": "New article",
    "换回 AI 文章": "Switch to AI article",
    "来源": "Source",
    "调整难度": "Adjust level",
    "调整难度…": "Adjust level…",
    "取消": "Cancel",
    "取消生成": "Cancel",
    "取消评分": "Cancel",
    "取消请求": "Cancel",
    "取消测试": "Cancel test",
    # 查词卡
    "加入生词本": "Save word",
    "加入本篇高亮": "Highlight here",
    "已加入生词本": "Saved",
    "重新生成记忆方法": "Regenerate mnemonic",
    "AI 生成记忆方法": "AI mnemonic",
    "按本句重查": "Look up in sentence",
    "查询中…": "Looking up…",
    # 复选框 / 单选项
    "只看失败": "Failed only",
    "自动刷新": "Auto refresh",
    "显示": "Show",
    "打开文章后自动预查生词": "Prefetch words after opening",
    "复习时要求默写英文单词": "Type answers during review",
    "自动安排真实文章": "Auto real articles",
    "不知道": "I don't know",
    # 生词本
    "删除": "Delete",
    "删除选中": "Delete selected",
    "保存释义": "Save meaning",
    "刷新": "Refresh",
    # 复习
    "开始复习": "Start review",
    "切换题型": "Switch mode",
    "显示答案（空格）": "Show answer (Space)",
    "显示释义并默写（空格）": "Show meaning & type (Space)",
    "想不起来，直接看答案（跳过）": "Show answer (skip)",
    "看不到答案？点这里": "Can't see it? Show answer",
    "忘记": "Forgot",
    "模糊": "Vague",
    "认识": "Known",
    "简单": "Easy",
    "轻松": "Easy",
    "刚好": "About right",
    "吃力": "Hard",
    "忘记 (1)": "Forgot (1)",
    "模糊 (2)": "Vague (2)",
    "认识 (3)": "Known (3)",
    "太简单 (4)": "Too easy (4)",
    "继续": "Continue",
    "结束本轮": "End session",
    "撤销上一张": "Undo last",
    "今天到此为止": "Stop for today",
    "今天没有到期的词": "No words due today",
    "今天的复习已完成": "Review done for today",
    "今日目标已完成": "Daily goal reached",
    "查看复习页": "Go to review",
    "提交并评分": "Submit & score",
    "提交批改": "Submit",
    "重新提交": "Resubmit",
    "重试评分": "Retry scoring",
    # 测评 / 对话框
    "开始测评": "Start assessment",
    "开始配置": "Start setup",
    "开始使用": "Get started",
    "重新配置": "Reconfigure",
    "重新测评": "Reassess",
    "上一步": "Back",
    "下一步": "Next",
    "完成": "Done",
    "关闭": "Close",
    "全屏": "Fullscreen",
    "退出全屏": "Exit fullscreen",
    "查看答题结果": "View results",
    "继续检测": "Continue quiz",
    "开始检测": "Start quiz",
    "暂不检测": "Not now",
    "继续阅读": "Continue reading",
    "回到概览": "Back to overview",
    "手动选级": "Choose level",
    "跳过，手动选级": "Skip, pick level",
    "跳过，直接记录": "Skip and record",
    "使用该等级开始": "Start at this level",
    "保持当前": "Keep current",
    "应用建议等级": "Apply suggested level",
    "重新评估并换一篇": "Re-evaluate & replace",
    "生成本周复盘": "Generate weekly review",
    # 阅读难度反馈的快捷选项
    "太难了，生词太多": "Too hard, too many new words",
    "有点难，读得吃力": "A bit hard, slow going",
    "难度刚好": "About right",
    "有点简单，想要难一点": "A bit easy, make it harder",
    # 复习求助的快捷原因
    "和近义词混淆": "Confused with synonyms",
    "只记得大概意思": "Only remember the gist",
    "发音或拼写记不住": "Spelling or sound",
    "不会在句子里用": "Can't use it in a sentence",
    "完全没印象": "No memory of it",
    "换个语境就不认识": "Lost in another context",
    "查看本周复盘": "View weekly review",
    "查看最近复盘": "View last review",
    "返回修改": "Back to edit",
    "请稍候": "Please wait",
    "请 AI 帮我记": "Ask AI for help",
    "重试": "Retry",
    "重试生成": "Retry generate",
    "提交（回车）": "Submit (Enter)",
    "已完成": "Done",
    "正在批改…": "Grading…",
    # 设置页 / 数据
    "测试连接": "Test connection",
    "查看调用记录": "View AI calls",
    "查看运行日志": "View logs",
    "清空记录": "Clear records",
    "导出 JSON": "Export JSON",
    "导出 CSV": "Export CSV",
    "导出数据库": "Export database",
    "导入数据库": "Import database",
    "打开备份目录": "Open backup folder",
    "打开日志目录": "Open log folder",
    "打开配置目录": "Open config folder",
}

# 带数字的动态按钮文案：整句查表查不到，用模式替换。捕获组在模板里用 \1。
BUTTON_EN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^加练一轮（(\d+) 个）$"), r"Extra round (\1)"),
    (re.compile(r"^继续下一轮（还剩 (\d+) 个）$"), r"Next round (\1 left)"),
    (re.compile(r"^继续复习（还差 (\d+) 个）$"), r"Continue (\1 left)"),
)

_ORIGINAL_SET_TEXT = QAbstractButton.setText


def set_low_profile(value: bool) -> None:
    """切换全局按钮语言开关；不会主动刷新已存在的按钮。"""
    global _low_profile
    _low_profile = bool(value)


def translate_button(text: str) -> str:
    """按当前模式翻译一条按钮文案；未收录的中文/已经是英文的返回原样。"""
    if not _low_profile:
        return text
    translated = BUTTON_EN.get(text)
    if translated is not None:
        return translated
    for pattern, template in BUTTON_EN_PATTERNS:
        if pattern.match(text):
            return pattern.sub(template, text)
    return text


def _patched_set_text(self: QAbstractButton, text: str) -> None:
    """拦截页面代码的 setText：记住中文原文，显示翻译后的文本。"""
    self._source_text = text
    _ORIGINAL_SET_TEXT(self, translate_button(text))


# 导入本模块即生效。默认模式是浅色/中文，translate 直接返回原文。
QAbstractButton.setText = _patched_set_text  # type: ignore[method-assign]


def _source_text(button: QAbstractButton) -> str:
    """取按钮的中文原文；C++ 构造参数没有记录时用当前文本兜底。"""
    source = getattr(button, "_source_text", None)
    if source is None:
        source = button.text()
        button._source_text = source
    return source


def refresh_buttons(root: QWidget) -> None:
    """切主题后刷新控件树里所有按钮的显示文本（原文保持不变）。"""
    buttons: list[QAbstractButton] = [root] if isinstance(root, QAbstractButton) else []
    buttons.extend(root.findChildren(QAbstractButton))
    for button in buttons:
        _ORIGINAL_SET_TEXT(button, translate_button(_source_text(button)))


class _ButtonTranslator(QObject):
    """兜底：`QPushButton("中文")` 的初始文本由 C++ 写入，不经 setText。"""

    def eventFilter(self, obj: object, event: QEvent) -> bool:  # noqa: N802 - Qt 约定
        if event.type() == QEvent.Type.Show and isinstance(obj, QAbstractButton):
            _ORIGINAL_SET_TEXT(obj, translate_button(_source_text(obj)))
        return False


_TRANSLATOR: _ButtonTranslator | None = None


def install(app: QApplication) -> None:
    """安装一次应用级过滤器；重复调用是安全的。"""
    global _TRANSLATOR
    if _TRANSLATOR is None:
        _TRANSLATOR = _ButtonTranslator(app)
        app.installEventFilter(_TRANSLATOR)
