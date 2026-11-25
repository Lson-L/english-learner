"""真实文章来源弹窗（M7.1）：标题、作者、来源、许可与原文链接。

只读展示 `corpus.json` / SQLite 里记录的元数据，不抓网络、不提供跳转。
外部链接只显示文本，用户自己复制到浏览器打开，避免在应用里引入 WebEngine。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import RealText

# 展示顺序：标题 → 来源 → 署名 → 许可 → 链接 → 时间。
_FIELDS: tuple[tuple[str, str], ...] = (
    ("标题", "title"),
    ("来源", "source"),
    ("作者 / 署名", "attribution"),
    ("许可", "license"),
    ("许可链接", "license_url"),
    ("原文链接", "source_url"),
    ("出版时间", "published"),
    ("抓取时间", "fetched_at"),
)


class RealTextSourceDialog(QDialog):
    """展示一篇真实文章的来源与许可信息。"""

    def __init__(self, text: RealText | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("文章来源")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        if text is None:
            hint = QLabel("找不到这篇文章的来源记录，可能已被内容包更新替换。")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        else:
            form = QFormLayout()
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(8)
            for label, field in _FIELDS:
                value = str(getattr(text, field, "") or "").strip()
                if not value:
                    continue
                item = QLabel(value)
                item.setWordWrap(True)
                item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(f"{label}：", item)
            layout.addLayout(form)

            if text.license == "Personal use":
                note = QLabel("仅限本机个人学习使用，请勿公开分发。")
                note.setObjectName("muted")
                note.setWordWrap(True)
                layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


__all__ = ["RealTextSourceDialog"]
