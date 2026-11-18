"""M1/M2 才实现的三个页面占位。"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from .common import PlaceholderPage


class ReaderPage(PlaceholderPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "阅读",
            "文章列表与阅读器：双击任意单词查看语境释义，加入生词本后正文永久高亮。",
            [
                (
                    "每日文章",
                    "按等级和兴趣生成 250~350 词短文，结构化 JSON 落库；当天缓存，"
                    "重启秒开，可手动「换一篇」。",
                ),
                (
                    "划词释义",
                    "双击或拖选单词，右侧弹出音标、词性、语境义、英文释义和例句；"
                    "释义结果缓存，同一个词第二次不再消耗 token。",
                ),
                (
                    "正文高亮",
                    "生词按字符偏移量持久化高亮，支持字号、行距和深色阅读模式。",
                ),
            ],
            parent,
        )


class VocabularyPage(PlaceholderPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "生词本",
            "所有标注过的词都在这里：可筛选、编辑释义、查看出处文章。",
            [
                (
                    "词条列表",
                    "按「未复习 / 学习中 / 已掌握」筛选；重复添加同一个词自动合并，"
                    "保留第一次出现的上下文句子。",
                ),
                (
                    "手动维护",
                    "允许修改 AI 给的释义、补充自己的记忆法，避免被错误释义带偏。",
                ),
                (
                    "导出",
                    "M3 支持导出 JSON / CSV，方便备份或导入其他工具。",
                ),
            ],
            parent,
        )


class ReviewPage(PlaceholderPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "复习",
            "间隔重复：只复习今天到期的词，3~5 分钟走完。",
            [
                (
                    "卡片复习",
                    "正面显示单词和当初的上下文句子，翻面看释义与例句；"
                    "四档反馈：忘记 / 模糊 / 认识 / 太简单。",
                ),
                (
                    "SM-2 调度",
                    "每个词记录 ease、间隔天数和下次到期日；算法集中在 services/srs.py，"
                    "以后想换 FSRS 只改一个文件。",
                ),
                (
                    "离线可用",
                    "复习完全本地化，AI 不可用时也能正常进行。",
                ),
            ],
            parent,
        )
