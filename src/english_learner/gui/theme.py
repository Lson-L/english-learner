"""全局样式。

M0 先用一份集中管理的 QSS（≈ Web 的全局 CSS），后续要做深色模式时
再把颜色抽成 dict 生成两份样式即可，不用改各个页面。
"""

from __future__ import annotations

STYLESHEET = """
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
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

/* ---------- 消息框 ---------- */
QMessageBox {
    background: #FFFFFF;
}
QMessageBox QLabel {
    color: #1F2937;
}
"""
