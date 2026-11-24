"""全局样式。

颜色统一抽在 `LIGHT_PALETTE` / `LOW_PROFILE_PALETTE` 两份 dict 里，模板用
`__KEY__` 占位符，最后一次性替换。加新控件时只写占位符，不要写死色值，
否则深色主题会漏掉一块浅色。

低调主题（`low_profile=True`）：深色 + 等宽字体，观感接近编辑器/终端，
适合在办公环境里不引人注意。它只改外观与侧边栏文案，不改业务逻辑。
"""

from __future__ import annotations

from .fonts import DEFAULT_ENGLISH_FONT, font_stack

# QSS 里满是大括号，用不了 f-string；先用占位符写，最后统一替换。
# 全应用只有这一处决定英文外观：所有控件都继承 `*` 的字体链，新页面自动生效。
_STYLESHEET_TEMPLATE = """
* {
    font-family: __ENGLISH_FONT__;
    font-size: 14px;
    color: __TEXT__;
}
QMainWindow, QDialog {
    background: __BG__;
}
/* 悬浮提示：底色和文字必须成对取调色板，不能复用 TEXT/ON_ACCENT——
   低调主题的 TEXT 是浅灰，配白字等于白底白字，完全看不清。 */
QToolTip {
    background: __TOOLTIP_BG__;
    color: __TOOLTIP_TEXT__;
    border: 1px solid __TOOLTIP_BORDER__;
    border-radius: 6px;
    padding: 6px 8px;
}

/* ---------- 左侧导航 ---------- */
QFrame#sidebar {
    background: __SIDEBAR_BG__;
    border: none;
}
QLabel#appTitle {
    color: __SIDEBAR_TITLE__;
    font-size: 20px;
    font-weight: 600;
}
QLabel#appSubtitle {
    color: __SIDEBAR_SUBTITLE__;
    font-size: 11px;
}
QLabel#sidebarSection {
    color: __SIDEBAR_SECTION__;
    font-size: 11px;
    padding: 4px 14px;
}
QPushButton#navButton {
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 8px;
    color: __SIDEBAR_TEXT__;
    background: transparent;
    font-size: 14px;
}
QPushButton#navButton:hover {
    background: __SIDEBAR_HOVER__;
    color: __SIDEBAR_ACTIVE_TEXT__;
}
QPushButton#navButton:checked {
    background: __SIDEBAR_ACTIVE__;
    color: __SIDEBAR_ACTIVE_TEXT__;
    font-weight: 600;
}
QLabel#sidebarFooter {
    color: __SIDEBAR_FOOTER__;
    font-size: 11px;
}

/* ---------- 页面通用 ---------- */
QLabel#pageTitle {
    font-size: 24px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#pageSubtitle {
    color: __TEXT_MUTED__;
    font-size: 13px;
}
/* 紧凑页头（复习页）：标题/说明各降一档，把高度让给正文卡片。 */
QLabel#pageTitleCompact {
    font-size: 20px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#pageSubtitleCompact {
    color: __TEXT_MUTED__;
    font-size: 12px;
}
QLabel#mutedCompact {
    color: __TEXT_MUTED__;
    font-size: 12px;
}
QLabel#cardTitle {
    font-size: 16px;
    font-weight: 600;
}
/* 阅读页文章标题：比卡片标题再小一档，把高度让给正文。 */
QLabel#articleTitle {
    font-size: 15px;
    font-weight: 600;
}
QLabel#muted {
    color: __TEXT_MUTED__;
    font-size: 13px;
}
QLabel#fieldLabel {
    font-size: 13px;
    color: __TEXT_FIELD__;
    font-weight: 600;
}
QLabel#stepBadge {
    background: __ACCENT_BG__;
    color: __ACCENT__;
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#bigNumber {
    font-size: 28px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
/* 用量汇总：6 个指标排一行，字号比首页大数字小一档。 */
QLabel#usageValue {
    font-size: 22px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QFrame#card {
    background: __BG_CARD__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
}
/* 档内进度条：细条、无文字，具体百分比写在旁边的 muted 文案里。 */
QProgressBar#levelProgress {
    background: __TRACK__;
    border: none;
    border-radius: 4px;
}
QProgressBar#levelProgress::chunk {
    background: __ACCENT__;
    border-radius: 4px;
}
/* AI 处理中的不确定进度条：外观与档内进度条一致，但默认隐藏。 */
QProgressBar#busyProgress {
    background: __TRACK__;
    border: none;
    border-radius: 4px;
}
QProgressBar#busyProgress::chunk {
    background: __ACCENT__;
    border-radius: 4px;
}
QFrame#divider {
    background: __TRACK__;
    max-height: 1px;
    border: none;
}
/* 统计页的累计指标切换：三个互斥小按钮，选中态用浅色强调底。 */
QPushButton#segmentButton {
    background: __BG_SUBTLE__;
    border: 1px solid __BORDER__;
    border-radius: 6px;
    padding: 4px 12px;
    color: __TEXT_SECOND__;
    font-size: 12px;
}
QPushButton#segmentButton:hover {
    background: __HOVER_BG__;
}
QPushButton#segmentButton:checked {
    background: __ACCENT_BG__;
    border-color: __ACCENT__;
    color: __ACCENT__;
    font-weight: 600;
}
/* 成就徽章：默认灰底锁定态；解锁后浅蓝底 + 强调描边。 */
QFrame#achievementBadge {
    background: __BG_SUBTLE__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
}
QFrame#achievementBadge[unlocked="true"] {
    background: __ACCENT_BG__;
    border-color: __ACCENT__;
}
QLabel#badgeMark {
    background: __TRACK__;
    color: __TEXT_MUTED__;
    border-radius: 14px;
    font-size: 14px;
    font-weight: 600;
}
QFrame#achievementBadge[unlocked="true"] QLabel#badgeMark {
    background: __ACCENT__;
    color: __ON_ACCENT__;
}
QLabel#badgeTitle {
    font-size: 13px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#badgeDesc {
    font-size: 11px;
    color: __TEXT_MUTED__;
}
QLabel#badgeState {
    font-size: 11px;
    color: __TEXT_MUTED__;
}
QFrame#achievementBadge[unlocked="true"] QLabel#badgeState {
    color: __ACCENT__;
    font-weight: 600;
}
QProgressBar#badgeProgress {
    background: __TRACK__;
    border: none;
    border-radius: 2px;
}
QProgressBar#badgeProgress::chunk {
    background: __ACCENT__;
    border-radius: 2px;
}
QFrame#achievementBadge[unlocked="false"] QProgressBar#badgeProgress::chunk {
    background: __TEXT_FAINT__;
}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QDoubleSpinBox {
    background: __BG_INPUT__;
    border: 1px solid __BORDER_INPUT__;
    border-radius: 8px;
    padding: 8px 10px;
    min-height: 20px;
    selection-background-color: __SELECTION__;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
    border-color: __ACCENT_STRONG__;
}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {
    background: __BG_DISABLED__;
    color: __TEXT_DISABLED__;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background: __BG_INPUT__;
    border: 1px solid __BORDER_INPUT__;
    selection-background-color: __ACCENT_BG__;
    selection-color: __TEXT__;
    outline: none;
}
QCheckBox {
    color: __TEXT_SECOND__;
    font-size: 13px;
    spacing: 6px;
}

/* ---------- 按钮 ---------- */
QPushButton#primaryButton {
    background: __ACCENT_STRONG__;
    color: __ON_ACCENT__;
    border: none;
    border-radius: 8px;
    padding: 9px 22px;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: __ACCENT_STRONG_HOVER__;
}
QPushButton#primaryButton:pressed {
    background: __ACCENT_STRONG_PRESSED__;
}
QPushButton#primaryButton:disabled {
    background: __ACCENT_DISABLED__;
    color: __ON_ACCENT_DISABLED__;
}
QPushButton#secondaryButton {
    background: __BG_SUBTLE__;
    color: __TEXT_FIELD__;
    border: 1px solid __BORDER_INPUT__;
    border-radius: 8px;
    padding: 9px 18px;
}
QPushButton#secondaryButton:hover {
    border-color: __ACCENT_STRONG__;
    color: __ACCENT__;
}
QPushButton#secondaryButton:disabled {
    color: __TEXT_DISABLED__;
    background: __BG_DISABLED__;
}
QPushButton#linkButton {
    background: transparent;
    border: none;
    color: __ACCENT__;
    padding: 2px 4px;
}
QPushButton#linkButton:hover {
    color: __ACCENT_HOVER__;
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
    background: __SCROLL_HANDLE__;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: __SCROLL_HANDLE_HOVER__;
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
    color: __TEXT__;
    padding: 4px 0;
}
QLabel#questionPrompt {
    font-size: 14px;
    color: __TEXT_STRONG__;
    font-weight: 600;
}
QRadioButton {
    color: __TEXT_FIELD__;
    font-size: 14px;
    spacing: 8px;
    padding: 2px 0;
}
/* 选择题单选框：显式画 indicator，不依赖系统主题的默认圆点。
   深色背景下默认样式几乎看不见，这里用强调色圆环 + 白色内点。 */
/* 选中/未选中都用 5px 边框，保证总尺寸一致，勾选时选项行不会跳动。 */
QRadioButton::indicator {
    width: 6px;
    height: 6px;
    border: 5px solid __RADIO_BORDER__;
    border-radius: 8px;
    background: __RADIO_BG__;
}
QRadioButton::indicator:hover {
    border-color: __ACCENT__;
}
QRadioButton::indicator:checked {
    border-color: __ACCENT__;
    background: __RADIO_DOT__;
}
/* 选中项文字同步加粗提亮，扫一眼就能看出选了哪个。 */
QRadioButton:checked {
    color: __TEXT_STRONG__;
    font-weight: 600;
}

/* ---------- 阅读器与释义卡 ---------- */
QTextEdit#articleView {
    background: __BG_CARD__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
    selection-background-color: __SELECTION__;
    /* 双击取词的选中文字：不设前景色时暗色主题会沿用正文浅灰，对比不足。 */
    selection-color: __SELECTION_TEXT__;
}
QFrame#wordPanel {
    background: __BG_CARD__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
}
QPushButton#cardCloseButton {
    background: transparent;
    border: none;
    color: __TEXT_FAINT__;
    font-size: 18px;
    padding: 0;
}
QPushButton#cardCloseButton:hover {
    color: __TEXT_STRONG__;
    background: __HOVER_BG__;
    border-radius: 12px;
}
QLabel#wordTitle {
    font-size: 26px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#wordPhonetic {
    color: __TEXT_MUTED__;
    font-size: 14px;
}
QLabel#wordPos {
    background: __ACCENT_BG__;
    color: __ACCENT__;
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#wordMeaningCn {
    font-size: 16px;
    color: __TEXT_STRONG__;
}
QLabel#wordMeaningEn {
    color: __TEXT_SECOND__;
    font-size: 13px;
    font-style: italic;
}
QLabel#wordExampleEn {
    color: __TEXT__;
    font-size: 13px;
}
QLabel#wordExampleCn {
    color: __TEXT_MUTED__;
    font-size: 13px;
}
QLabel#wordContext {
    color: __TEXT_MUTED__;
    font-size: 12px;
    background: __BG_SUBTLE__;
    border-radius: 8px;
    padding: 8px 10px;
}
QLabel#wordMnemonic {
    color: __WARN_TEXT__;
    font-size: 13px;
    background: __WARN_BG__;
    border: 1px solid __WARN_BORDER__;
    border-radius: 8px;
    padding: 8px 10px;
}
QLabel#reviewHelpAdvice {
    color: __TEXT__;
    font-size: 14px;
    background: __OK_BG__;
    border: 1px solid __OK_BORDER__;
    border-radius: 10px;
    padding: 12px 14px;
}
QLabel#reviewHelpLog {
    color: __TEXT__;
    font-size: 12px;
    background: __OK_BG__;
    border: 1px solid __OK_BORDER__;
    border-radius: 8px;
    padding: 8px 10px;
}

/* ---------- 生词本 ---------- */
QTableWidget#wordTable, QTableWidget#dataTable {
    background: __BG_CARD__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
    selection-background-color: __ACCENT_BG__;
    selection-color: __TEXT__;
    outline: none;
}
QTableWidget#wordTable::item, QTableWidget#dataTable::item {
    padding: 8px 10px;
    border-bottom: 1px solid __BORDER_FAINT__;
}
QTableWidget#wordTable::item:selected, QTableWidget#dataTable::item:selected {
    background: __ACCENT_BG__;
    color: __TEXT__;
}
QHeaderView::section {
    background: __BG_SUBTLE__;
    color: __TEXT_SECOND__;
    border: none;
    border-bottom: 1px solid __BORDER__;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
}
QPlainTextEdit {
    background: __BG_INPUT__;
    border: 1px solid __BORDER_INPUT__;
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: __SELECTION__;
}
QPlainTextEdit:focus {
    border-color: __ACCENT_STRONG__;
}

/* ---------- 复习 ---------- */
QFrame#reviewCard {
    background: __BG_CARD__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
}
/* 复习卡片正文的滚动区：透出卡片底色，只留滚动条。 */
QScrollArea#reviewScroll {
    background: transparent;
    border: none;
}
QScrollArea#reviewScroll > QWidget > QWidget {
    background: transparent;
}
QLabel#reviewIdleTitle {
    font-size: 22px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#reviewWord {
    font-size: 40px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#reviewMeaning {
    font-size: 18px;
    color: __TEXT_STRONG__;
}
QLabel#reviewPrompt {
    font-size: 22px;
    font-weight: 600;
    color: __TEXT_STRONG__;
}
QLabel#reviewContext {
    color: __TEXT_SECOND__;
    font-size: 14px;
    background: __BG_SUBTLE__;
    border-radius: 8px;
    padding: 10px 12px;
}
QPushButton#ratingForgot {
    background: __DANGER_BG__;
    color: __DANGER_TEXT__;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingForgot:hover {
    background: __DANGER_HOVER__;
}
QPushButton#ratingVague {
    background: __VAGUE_BG__;
    color: __VAGUE_TEXT__;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingVague:hover {
    background: __VAGUE_HOVER__;
}
QPushButton#ratingKnown {
    background: __KNOWN_BG__;
    color: __KNOWN_TEXT__;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingKnown:hover {
    background: __KNOWN_HOVER__;
}
QPushButton#ratingEasy {
    background: __EASY_BG__;
    color: __EASY_TEXT__;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    font-weight: 600;
}
QPushButton#ratingEasy:hover {
    background: __EASY_HOVER__;
}

/* ---------- 消息框 ---------- */
QMessageBox {
    background: __BG_CARD__;
}
QMessageBox QLabel {
    color: __TEXT__;
}
"""

# 浅色主题：原配色，未改动。
LIGHT_PALETTE: dict[str, str] = {
    "TEXT": "#1F2937",
    "TEXT_STRONG": "#111827",
    "TEXT_SECOND": "#4B5563",
    "TEXT_FIELD": "#374151",
    "TEXT_MUTED": "#6B7280",
    "TEXT_DISABLED": "#9AA3B2",
    "TEXT_FAINT": "#9CA3AF",
    "BG": "#F5F7FA",
    "BG_CARD": "#FFFFFF",
    "BG_INPUT": "#FFFFFF",
    "BG_SUBTLE": "#F7F8FA",
    "BG_DISABLED": "#F1F3F7",
    "BORDER": "#E4E8F0",
    "BORDER_INPUT": "#D7DCE6",
    "BORDER_FAINT": "#F1F4F9",
    "TRACK": "#EEF1F6",
    "ACCENT": "#2563EB",
    "ACCENT_HOVER": "#1D4ED8",
    "ACCENT_STRONG": "#3B82F6",
    "ACCENT_STRONG_HOVER": "#2F6FE0",
    "ACCENT_STRONG_PRESSED": "#2760C8",
    "ACCENT_DISABLED": "#B9CDF2",
    "ACCENT_BG": "#EAF1FF",
    "ON_ACCENT": "#FFFFFF",
    "ON_ACCENT_DISABLED": "#F3F6FD",
    "TOOLTIP_BG": "#1F2937",
    "TOOLTIP_TEXT": "#FFFFFF",
    "TOOLTIP_BORDER": "#1F2937",
    "SELECTION": "#BFD6FF",
    "SELECTION_TEXT": "#1F2937",
    "RADIO_BORDER": "#C7CFDB",
    "RADIO_BG": "#FFFFFF",
    "RADIO_DOT": "#FFFFFF",
    "HOVER_BG": "#F3F4F6",
    "SCROLL_HANDLE": "#D3D9E4",
    "SCROLL_HANDLE_HOVER": "#B8C1D1",
    "WARN_TEXT": "#92400E",
    "WARN_BG": "#FFFBEB",
    "WARN_BORDER": "#FDE68A",
    "OK_BG": "#F0FDF4",
    "OK_BORDER": "#BBF7D0",
    "DANGER_BG": "#FEE2E2",
    "DANGER_TEXT": "#B91C1C",
    "DANGER_HOVER": "#FECACA",
    "VAGUE_BG": "#FEF3C7",
    "VAGUE_TEXT": "#B45309",
    "VAGUE_HOVER": "#FDE68A",
    "KNOWN_BG": "#DBEAFE",
    "KNOWN_TEXT": "#1D4ED8",
    "KNOWN_HOVER": "#BFDBFE",
    "EASY_BG": "#D1FAE5",
    "EASY_TEXT": "#047857",
    "EASY_HOVER": "#A7F3D0",
    # 图表专用色：分类色按固定槽位分配（蓝 / 橙 / 青 / 黄），
    # 词档三段用同色系 ordinal 阶梯，热力图用单色 sequential 阶梯。
    # 已用 dataviz 校验脚本在卡片底色 #FFFFFF 上验证通过；改色必须重跑校验。
    "CHART_1": "#2A78D6",
    "CHART_2": "#EB6834",
    "CHART_3": "#1BAF7A",
    "CHART_4": "#EDA100",
    "CHART_SEQ_1": "#86B6EF",
    "CHART_SEQ_2": "#2A78D6",
    "CHART_SEQ_3": "#104281",
    "CHART_HEAT_0": "#EEF1F6",
    "CHART_HEAT_1": "#CDE2FB",
    "CHART_HEAT_2": "#9EC5F4",
    "CHART_HEAT_3": "#5598E7",
    "CHART_HEAT_4": "#184F95",
    "CHART_GRID": "#E4E8F0",
    "CHART_AXIS": "#9CA3AF",
    "SIDEBAR_BG": "#1D2433",
    "SIDEBAR_TITLE": "#FFFFFF",
    "SIDEBAR_SUBTITLE": "#8E9AB0",
    "SIDEBAR_SECTION": "#5F6B80",
    "SIDEBAR_TEXT": "#A9B4C8",
    "SIDEBAR_ACTIVE_TEXT": "#FFFFFF",
    "SIDEBAR_HOVER": "#262F40",
    "SIDEBAR_ACTIVE": "#2B364A",
    "SIDEBAR_FOOTER": "#6C7890",
}

# 低调主题：VS Code 风格的深色配色。刻意不使用纯白/大面积浅色，
# 远看更像编辑器或终端窗口。
LOW_PROFILE_PALETTE: dict[str, str] = {
    "TEXT": "#D4D4D4",
    "TEXT_STRONG": "#E8E8E8",
    "TEXT_SECOND": "#B0B8C4",
    "TEXT_FIELD": "#C9D1D9",
    "TEXT_MUTED": "#8B949E",
    "TEXT_DISABLED": "#6A6A6A",
    "TEXT_FAINT": "#6E7681",
    "BG": "#1E1E1E",
    "BG_CARD": "#252526",
    "BG_INPUT": "#2A2A2A",
    "BG_SUBTLE": "#2D2D30",
    "BG_DISABLED": "#262626",
    "BORDER": "#3C3C3C",
    "BORDER_INPUT": "#3C3C3C",
    "BORDER_FAINT": "#333333",
    "TRACK": "#333333",
    "ACCENT": "#4A9EFF",
    "ACCENT_HOVER": "#7DB8FF",
    "ACCENT_STRONG": "#0E639C",
    "ACCENT_STRONG_HOVER": "#1177BB",
    "ACCENT_STRONG_PRESSED": "#0A5488",
    "ACCENT_DISABLED": "#2D4A66",
    "ACCENT_BG": "#1E3A5F",
    "ON_ACCENT": "#FFFFFF",
    "ON_ACCENT_DISABLED": "#A7B8C8",
    "TOOLTIP_BG": "#2D2D30",
    "TOOLTIP_TEXT": "#E8E8E8",
    "TOOLTIP_BORDER": "#4F4F4F",
    "SELECTION": "#264F78",
    "SELECTION_TEXT": "#FFFFFF",
    "RADIO_BORDER": "#6E7681",
    "RADIO_BG": "#252526",
    "RADIO_DOT": "#FFFFFF",
    "HOVER_BG": "#3A3A3A",
    "SCROLL_HANDLE": "#424242",
    "SCROLL_HANDLE_HOVER": "#4F4F4F",
    "WARN_TEXT": "#E2B65C",
    "WARN_BG": "#332B1A",
    "WARN_BORDER": "#5C4A22",
    "OK_BG": "#1E3328",
    "OK_BORDER": "#2E5A3E",
    "DANGER_BG": "#4A2222",
    "DANGER_TEXT": "#F48771",
    "DANGER_HOVER": "#5C2A2A",
    "VAGUE_BG": "#4A3A1E",
    "VAGUE_TEXT": "#D7B45A",
    "VAGUE_HOVER": "#5C4A22",
    "KNOWN_BG": "#1E3A5F",
    "KNOWN_TEXT": "#7DB8FF",
    "KNOWN_HOVER": "#26476B",
    "EASY_BG": "#1E3B2E",
    "EASY_TEXT": "#5FD3A0",
    "EASY_HOVER": "#2A5240",
    # 图表色：与浅色主题同一组色相，但按深色卡片底 #252526 重新取步进；
    # 分类色与 ordinal 阶梯均已通过 dataviz 校验（含 2:1 浅端对比度）。
    "CHART_1": "#3987E5",
    "CHART_2": "#D95926",
    "CHART_3": "#199E70",
    "CHART_4": "#C98500",
    "CHART_SEQ_1": "#1C5CAB",
    "CHART_SEQ_2": "#3987E5",
    "CHART_SEQ_3": "#86B6EF",
    # 热力图 0 档必须比卡片底 #252526 亮一档，否则 26 周的空白格
    # 会完全消失，只剩最近几周有活动的格子，看起来像图表没画出来。
    "CHART_HEAT_0": "#3A3A3A",
    "CHART_HEAT_1": "#1C5CAB",
    "CHART_HEAT_2": "#256ABF",
    "CHART_HEAT_3": "#3987E5",
    "CHART_HEAT_4": "#86B6EF",
    "CHART_GRID": "#3C3C3C",
    "CHART_AXIS": "#898781",
    "SIDEBAR_BG": "#181818",
    "SIDEBAR_TITLE": "#CCCCCC",
    "SIDEBAR_SUBTITLE": "#7A8290",
    "SIDEBAR_SECTION": "#6E7681",
    "SIDEBAR_TEXT": "#9DA5B4",
    "SIDEBAR_ACTIVE_TEXT": "#FFFFFF",
    "SIDEBAR_HOVER": "#2A2D2E",
    "SIDEBAR_ACTIVE": "#37373D",
    "SIDEBAR_FOOTER": "#5F6672",
}


def build_stylesheet(english_font: str | None = None, *, low_profile: bool = False) -> str:
    """生成全局 QSS。`english_font` 是用户选的英文字体 key（见 gui.fonts）。

    设置页切换字体或主题时会重新生成并 `QApplication.setStyleSheet(...)`，
    Qt 会自动重刷所有控件，不需要逐个页面改样式。

    `low_profile=True` 时使用深色调色板，并把全局字体换成等宽链
    （低调模式下用户选的英文字体会被忽略，见 `gui.fonts.font_stack`）。
    """
    font = font_stack(english_font, mono=low_profile)
    qss = _STYLESHEET_TEMPLATE.replace("__ENGLISH_FONT__", font)
    palette = LOW_PROFILE_PALETTE if low_profile else LIGHT_PALETTE
    for name, value in palette.items():
        qss = qss.replace(f"__{name}__", value)
    return qss


# 默认样式表：没读到用户偏好时用它兜底。
STYLESHEET = build_stylesheet(DEFAULT_ENGLISH_FONT)
