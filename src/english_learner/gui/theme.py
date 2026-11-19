"""全局样式。

M0 先用一份集中管理的 QSS（≈ Web 的全局 CSS），后续要做深色模式时
再把颜色抽成 dict 生成两份样式即可，不用改各个页面。
"""

from __future__ import annotations

from .fonts import DEFAULT_ENGLISH_FONT, english_font_stack

# QSS 里满是大括号，用不了 f-string；先用占位符写，最后统一替换成字体链。
# 全应用只有这一处决定英文外观：所有控件都继承 `*` 的字体链，新页面自动生效。
_STYLESHEET_TEMPLATE = """
* {
    font-family: __ENGLISH_FONT__;
    font-size: 14px;
    color: #1F2937;
}
QMainWindow, QDialog {
    background: #F5F7FA;
}
QToolTip {
    background: #1F2937;
    color: #FFFFFF;
    border: none;
    padding: 6px 8px;
}

/* ---------- 左侧导航 ---------- */
QFrame#sidebar {
    background: #1D2433;
    border: none;
}
QLabel#appTitle {
    color: #FFFFFF;
    font-size: 20px;
    font-weight: 600;
}
QLabel#appSubtitle {
    color: #8E9AB0;
    font-size: 11px;
}
QLabel#sidebarSection {
    color: #5F6B80;
    font-size: 11px;
    padding: 4px 14px;
}
QPushButton#navButton {
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 8px;
    color: #A9B4C8;
    background: transparent;
    font-size: 14px;
}
QPushButton#navButton:hover {
    background: #262F40;
    color: #FFFFFF;
}
QPushButton#navButton:checked {
    background: #2B364A;
    color: #FFFFFF;
    font-weight: 600;
}
QLabel#sidebarFooter {
    color: #6C7890;
    font-size: 11px;
}

/* ---------- 页面通用 ---------- */
QLabel#pageTitle {
    font-size: 24px;
    font-weight: 600;
    color: #111827;
}
QLabel#pageSubtitle {
    color: #6B7280;
    font-size: 13px;
}
QLabel#cardTitle {
    font-size: 16px;
    font-weight: 600;
}
QLabel#muted {
    color: #6B7280;
    font-size: 13px;
}
QLabel#fieldLabel {
    font-size: 13px;
    color: #374151;
    font-weight: 600;
}
QLabel#stepBadge {
    background: #EAF1FF;
    color: #2563EB;
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#bigNumber {
    font-size: 28px;
    font-weight: 600;
    color: #111827;
}
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #E4E8F0;
    border-radius: 12px;
}
QFrame#divider {
    background: #EEF1F6;
    max-height: 1px;
    border: none;
}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QDoubleSpinBox {
    background: #FFFFFF;
    border: 1px solid #D7DCE6;
    border-radius: 8px;
    padding: 8px 10px;
    min-height: 20px;
    selection-background-color: #BFD6FF;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
    border-color: #3B82F6;
}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {
    background: #F1F3F7;
    color: #9AA3B2;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid #D7DCE6;
    selection-background-color: #EAF1FF;
    selection-color: #1F2937;
    outline: none;
}
QCheckBox {
    color: #4B5563;
    font-size: 13px;
    spacing: 6px;
}

/* ---------- 按钮 ---------- */
QPushButton#primaryButton {
    background: #3B82F6;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 9px 22px;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: #2F6FE0;
}
QPushButton#primaryButton:pressed {
    background: #2760C8;
}
QPushButton#primaryButton:disabled {
    background: #B9CDF2;
    color: #F3F6FD;
}
QPushButton#secondaryButton {
    background: #FFFFFF;
    color: #374151;
    border: 1px solid #D7DCE6;
    border-radius: 8px;
    padding: 9px 18px;
}
QPushButton#secondaryButton:hover {
    border-color: #3B82F6;
    color: #2563EB;
}
QPushButton#secondaryButton:disabled {
    color: #9AA3B2;
    background: #F7F8FA;
}
QPushButton#linkButton {
    background: transparent;
    border: none;
    color: #2563EB;
    padding: 2px 4px;
}
QPushButton#linkButton:hover {
    color: #1D4ED8;
    text-decoration: underline;
}

/* ---------- 滚动区 ---------- */
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #D3D9E4;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #B8C1D1;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* ---------- 测评 ---------- */
/* 字体沿用全局英文链；行距在 assessment_dialog 里用富文本设置。 */
QLabel#passageText {
    font-size: 15px;
    color: #1F2937;
    padding: 4px 0;
}
QLabel#questionPrompt {
    font-size: 14px;
    color: #111827;
    font-weight: 600;
}
QRadioButton {
    color: #374151;
    font-size: 14px;
    spacing: 8px;
    padding: 2px 0;
}

/* ---------- 阅读器与释义卡 ---------- */
QTextEdit#articleView {
    background: #FFFFFF;
    border: 1px solid #E4E8F0;
    border-radius: 12px;
    selection-background-color: #BFD6FF;
}
QFrame#wordPanel {
    background: #FFFFFF;
    border: 1px solid #E4E8F0;
    border-radius: 12px;
}
QLabel#wordTitle {
    font-size: 26px;
    font-weight: 600;
    color: #111827;
}
QLabel#wordPhonetic {
    color: #6B7280;
    font-size: 14px;
}
QLabel#wordPos {
    background: #EAF1FF;
    color: #2563EB;
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#wordMeaningCn {
    font-size: 16px;
    color: #111827;
}
QLabel#wordMeaningEn {
    color: #4B5563;
    font-size: 13px;
    font-style: italic;
}
QLabel#wordExampleEn {
    color: #1F2937;
    font-size: 13px;
}
QLabel#wordExampleCn {
    color: #6B7280;
    font-size: 13px;
}
QLabel#wordContext {
    color: #6B7280;
    font-size: 12px;
    background: #F7F8FA;
    border-radius: 8px;
    padding: 8px 10px;
}

/* ---------- 生词本 ---------- */
QTableWidget#wordTable {
    background: #FFFFFF;
    border: 1px solid #E4E8F0;
    border-radius: 12px;
    selection-background-color: #EAF1FF;
    selection-color: #1F2937;
    outline: none;
}
QTableWidget#wordTable::item {
    padding: 8px 10px;
    border-bottom: 1px solid #F1F4F9;
}
QTableWidget#wordTable::item:selected {
    background: #EAF1FF;
    color: #1F2937;
}
QHeaderView::section {
    background: #F7F8FA;
    color: #4B5563;
    border: none;
    border-bottom: 1px solid #E4E8F0;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
}
QPlainTextEdit {
    background: #FFFFFF;
    border: 1px solid #D7DCE6;
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: #BFD6FF;
}
QPlainTextEdit:focus {
    border-color: #3B82F6;
}

/* ---------- 复习 ---------- */
QFrame#reviewCard {
    background: #FFFFFF;
    border: 1px solid #E4E8F0;
    border-radius: 12px;
}
QLabel#reviewIdleTitle {
    font-size: 22px;
    font-weight: 600;
    color: #111827;
}
QLabel#reviewWord {
    font-size: 40px;
    font-weight: 600;
    color: #111827;
}
QLabel#reviewMeaning {
    font-size: 18px;
    color: #111827;
}
QLabel#reviewContext {
    color: #4B5563;
    font-size: 14px;
    background: #F7F8FA;
    border-radius: 8px;
    padding: 10px 12px;
}
QPushButton#ratingForgot {
    background: #FEE2E2;
    color: #B91C1C;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingForgot:hover {
    background: #FECACA;
}
QPushButton#ratingVague {
    background: #FEF3C7;
    color: #B45309;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingVague:hover {
    background: #FDE68A;
}
QPushButton#ratingKnown {
    background: #DBEAFE;
    color: #1D4ED8;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingKnown:hover {
    background: #BFDBFE;
}
QPushButton#ratingEasy {
    background: #D1FAE5;
    color: #047857;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingEasy:hover {
    background: #A7F3D0;
}

/* ---------- 消息框 ---------- */
QMessageBox {
    background: #FFFFFF;
}
QMessageBox QLabel {
    color: #1F2937;
}
"""


def build_stylesheet(english_font: str | None = None) -> str:
    """生成全局 QSS。`english_font` 是用户选的英文字体 key（见 gui.fonts）。

    设置页切换字体时会重新生成并 `QApplication.setStyleSheet(...)`，
    Qt 会自动重刷所有控件，不需要逐个页面改样式。
    """
    return _STYLESHEET_TEMPLATE.replace("__ENGLISH_FONT__", english_font_stack(english_font))


# 默认样式表：没读到用户偏好时用它兜底。
STYLESHEET = build_stylesheet(DEFAULT_ENGLISH_FONT)
