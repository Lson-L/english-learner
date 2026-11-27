"""统计页用的自绘图表：不依赖 QtCharts，颜色全部取主题调色板。

设计约束（对应 docs 里的图表规范）：
- 分类色固定槽位（蓝 / 橙 / 青 / 黄），不按排名临时换色；
- 热力图与词档分布用单色阶梯，数量越大颜色越深；
- 网格线是 1px 实线、弱化处理，文字只用主题的 text / muted 色；
- 折线 2px、端点 8px 带 2px 底色描边、面积填充约 10% 透明度；
- 有悬停提示，但页面同时给出汇总数字，不让 tooltip 成为唯一读数方式。

Java 对照：≈ 一组自绘的 `JComponent` + `paintComponent`，数据与配色都从
外部传入，控件本身不查库、不读配置。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QToolTip, QWidget

from ...models.entities import CEFR_LEVELS
from ...services.stats_overview import (
    ActivityDay,
    CumulativePoint,
    DueDay,
    QuizSeries,
    WordStatusSplit,
    nice_max,
)
from ..theme import LIGHT_PALETTE, LOW_PROFILE_PALETTE

_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class ChartPalette:
    """图表用到的颜色；只从 theme 的两套调色板取值，不在控件里写死。"""

    surface: str
    grid: str
    axis: str
    text: str
    muted: str
    accent: str
    track: str
    series: tuple[str, ...]
    seq: tuple[str, ...]
    heat: tuple[str, ...]


def chart_palette(low_profile: bool) -> ChartPalette:
    palette = LOW_PROFILE_PALETTE if low_profile else LIGHT_PALETTE
    return ChartPalette(
        surface=palette["BG_CARD"],
        grid=palette["CHART_GRID"],
        axis=palette["CHART_AXIS"],
        text=palette["TEXT"],
        muted=palette["TEXT_MUTED"],
        accent=palette["ACCENT"],
        track=palette["TRACK"],
        series=(palette["CHART_1"], palette["CHART_2"], palette["CHART_3"], palette["CHART_4"]),
        seq=(palette["CHART_SEQ_1"], palette["CHART_SEQ_2"], palette["CHART_SEQ_3"]),
        heat=(
            palette["CHART_HEAT_0"],
            palette["CHART_HEAT_1"],
            palette["CHART_HEAT_2"],
            palette["CHART_HEAT_3"],
            palette["CHART_HEAT_4"],
        ),
    )


def _alpha(color: str, alpha: int) -> QColor:
    value = QColor(color)
    value.setAlpha(max(0, min(255, int(alpha))))
    return value


def _ink_on(color: str) -> str:
    """深色填充配白字、浅色填充配近黑字；用于分段条内部标签。"""
    value = QColor(color)
    channels = []
    for raw in (value.redF(), value.greenF(), value.blueF()):
        channels.append(raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4)
    luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    return "#111827" if luminance > 0.45 else "#FFFFFF"


def _parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _rounded_top_path(rect: QRectF, radius: float) -> QPainterPath:
    """只有顶部两角圆角的柱子路径；柱底必须与基线齐平。"""
    radius = min(radius, rect.width() / 2, rect.height())
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + radius)
    path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
    path.lineTo(rect.right() - radius, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path


def _fmt(value: int) -> str:
    return f"{int(value):,}"


class _ChartBase(QWidget):
    """图表基类：统一调色板、字体与空状态。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = chart_palette(False)
        self._low_profile = False
        self.setMouseTracking(True)

    def set_chart_palette(self, palette: ChartPalette) -> None:
        self._palette = palette
        self.update()

    def set_low_profile(self, low_profile: bool) -> None:
        self._low_profile = bool(low_profile)
        self.set_chart_palette(chart_palette(self._low_profile))

    def _t(self, zh: str, en: str) -> str:
        return en if self._low_profile else zh

    def _font(self, pixel_size: int, *, bold: bool = False) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        return font

    def _paint_empty(self, painter: QPainter, text: str) -> None:
        painter.setFont(self._font(12))
        painter.setPen(QColor(self._palette.muted))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        QToolTip.hideText()
        super().leaveEvent(event)


class HeatmapCalendar(_ChartBase):
    """26 周打卡热力图：每列一周、每行一天，颜色越深当天学习量越大。"""

    CELL = 18
    GAP = 4
    LEFT = 26
    TOP = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: tuple[ActivityDay, ...] = ()
        self._hover: int | None = None
        self.setMinimumHeight(self.TOP + 7 * (self.CELL + self.GAP) + 2)

    def set_days(self, days: tuple[ActivityDay, ...]) -> None:
        self._days = tuple(days)
        self._hover = None
        # 构造时还没有数据（sizeHint 只有 1 周宽），set_days 后必须让布局
        # 重新按 26 周计算宽度，否则热力图会被压成一条窄缝。
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        weeks = max(1, len(self._days) // 7)
        return QSize(
            self.LEFT + weeks * (self.CELL + self.GAP) + 4,
            self.TOP + 7 * (self.CELL + self.GAP) + 2,
        )

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _cell_rect(self, index: int) -> QRectF:
        week = index // 7
        weekday = index % 7
        return QRectF(
            self.LEFT + week * (self.CELL + self.GAP),
            self.TOP + weekday * (self.CELL + self.GAP),
            self.CELL,
            self.CELL,
        )

    def _month_labels(self) -> list[tuple[int, str]]:
        labels: list[tuple[int, str]] = []
        previous: int | None = None
        for week in range(max(1, len(self._days) // 7)):
            index = week * 7
            if index >= len(self._days):
                break
            parsed = _parse_day(self._days[index].day)
            if parsed is None:
                continue
            if parsed.month != previous:
                text = _MONTHS_EN[parsed.month - 1] if self._low_profile else f"{parsed.month}月"
                labels.append((week, text))
                previous = parsed.month
        return labels

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._days:
            self._paint_empty(painter, self._t("还没有打卡记录", "No activity yet"))
            return

        painter.setFont(self._font(9))
        painter.setPen(QColor(self._palette.axis))
        for week, text in self._month_labels():
            painter.drawText(
                QRectF(
                    self.LEFT + week * (self.CELL + self.GAP),
                    0,
                    42,
                    14,
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        weekday_labels = ("一", "三", "五") if not self._low_profile else ("M", "W", "F")
        for row, text in zip((0, 2, 4), weekday_labels, strict=False):
            painter.drawText(
                QRectF(0, self.TOP + row * (self.CELL + self.GAP), self.LEFT - 6, self.CELL),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        for index, day in enumerate(self._days):
            rect = self._cell_rect(index)
            if day.future:
                # 未来日期只留一圈很淡的网格，不填色。
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(_alpha(self._palette.grid, 80), 1))
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            elif day.level == 0 and self._low_profile:
                # 低调主题：0 档不填色，只用网格线勾出 26 周的骨架。
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(self._palette.grid), 1))
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self._palette.heat[day.level]))
                painter.drawRoundedRect(rect, 3, 3)
            if index == self._hover and not day.future:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(self._palette.text), 1))
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)

    def _index_at(self, position: QPointF) -> int | None:
        x = position.x() - self.LEFT
        y = position.y() - self.TOP
        if x < 0 or y < 0:
            return None
        step = self.CELL + self.GAP
        week = int(x // step)
        weekday = int(y // step)
        if week < 0 or weekday < 0 or weekday > 6:
            return None
        index = week * 7 + weekday
        if 0 <= index < len(self._days):
            return index
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        index = self._index_at(event.position())
        if index is not None and self._days[index].future:
            index = None
        if index != self._hover:
            self._hover = index
            self.update()
        if index is None:
            QToolTip.hideText()
            return
        day = self._days[index]
        if self._low_profile:
            text = f"{day.day} · read {day.articles} · words {day.words} · reviews {day.reviews}"
        else:
            text = f"{day.day} · 阅读 {day.articles} · 生词 {day.words} · 复习 {day.reviews}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        self._hover = None
        self.update()
        super().leaveEvent(event)


class HeatLegend(_ChartBase):
    """热力图图例：少 → 五个色阶 → 多。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setMinimumWidth(150)

    def sizeHint(self) -> QSize:
        return QSize(150, 18)

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = self._font(10)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        less = self._t("少", "Less")
        more = self._t("多", "More")
        x = 0.0
        painter.setPen(QColor(self._palette.muted))
        painter.drawText(
            QRectF(x, 0, metrics.horizontalAdvance(less), 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            less,
        )
        x += metrics.horizontalAdvance(less) + 6
        for index, color in enumerate(self._palette.heat):
            rect = QRectF(x, 5, 11, 11)
            if index == 0 and self._low_profile:
                # 低调主题的 0 档是“未填色”，图例里也画成空心格。
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(self._palette.grid), 1))
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(rect, 2, 2)
            x += 14
        painter.setPen(QColor(self._palette.muted))
        painter.drawText(
            QRectF(x + 2, 0, metrics.horizontalAdvance(more) + 4, 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            more,
        )


class CumulativeChart(_ChartBase):
    """累计成长曲线：一次只画一条指标，面积填充 + 端点直接标值。"""

    METRICS: dict[str, tuple[str, str, int]] = {
        "words": ("累计生词", "Words learned", 0),
        "articles": ("累计阅读", "Articles read", 1),
        "reviews": ("累计复习", "Reviews done", 2),
    }
    MARGIN_LEFT = 52
    MARGIN_RIGHT = 20
    MARGIN_TOP = 22
    MARGIN_BOTTOM = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: tuple[CumulativePoint, ...] = ()
        self._metric = "words"
        self._hover: int | None = None
        self.setMinimumHeight(190)

    def set_points(self, points: tuple[CumulativePoint, ...]) -> None:
        self._points = tuple(points)
        self._hover = None
        self.update()

    def set_metric(self, metric: str) -> None:
        if metric not in self.METRICS:
            return
        self._metric = metric
        self._hover = None
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.MARGIN_LEFT,
            self.MARGIN_TOP,
            max(10.0, self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT),
            max(10.0, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )

    def _x_at(self, index: int, plot: QRectF) -> float:
        count = len(self._points)
        if count <= 1:
            return plot.center().x()
        return plot.left() + plot.width() * index / (count - 1)

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        values = [point.value(self._metric) for point in self._points]
        if not values or max(values) <= 0:
            label_zh, label_en, _color_index = self.METRICS[self._metric]
            self._paint_empty(
                painter,
                self._t(f"还没有{label_zh}数据", f"No {label_en.lower()} data yet"),
            )
            return

        plot = self._plot_rect()
        y_max = nice_max(max(values))
        color = self._palette.series[self.METRICS[self._metric][2]]
        font = self._font(9)
        painter.setFont(font)

        # 水平网格 + Y 轴刻度。
        for step in range(5):
            ratio = step / 4
            y = plot.bottom() - plot.height() * ratio
            painter.setPen(QPen(QColor(self._palette.grid), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(self._palette.axis))
            painter.drawText(
                QRectF(0, y - 7, self.MARGIN_LEFT - 8, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _fmt(round(y_max * ratio)),
            )

        points = [
            QPointF(self._x_at(index, plot), plot.bottom() - value / y_max * plot.height())
            for index, value in enumerate(values)
        ]

        area = QPainterPath()
        area.moveTo(points[0].x(), plot.bottom())
        for point in points:
            area.lineTo(point)
        area.lineTo(points[-1].x(), plot.bottom())
        area.closeSubpath()
        painter.fillPath(area, _alpha(color, 28))

        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(color),
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(line)

        # 端点：2px 底色描边，让圆点跨在线上也看得清。
        end = points[-1]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.surface))
        painter.drawEllipse(end, 6, 6)
        painter.setBrush(QColor(color))
        painter.drawEllipse(end, 4, 4)

        painter.setFont(self._font(10, bold=True))
        painter.setPen(QColor(self._palette.text))
        value_text = _fmt(values[-1])
        text_rect = QRectF(end.x() - 70, end.y() - 24, 66, 16)
        if text_rect.left() < plot.left():
            text_rect.moveLeft(plot.left())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            value_text,
        )

        # X 轴只标首 / 中 / 尾，避免 90 个日期挤在一起。
        painter.setFont(font)
        painter.setPen(QColor(self._palette.axis))
        ticks = sorted({0, len(self._points) // 2, len(self._points) - 1})
        for index in ticks:
            painter.drawText(
                QRectF(self._x_at(index, plot) - 26, plot.bottom() + 6, 52, 14),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._points[index].day[5:],
            )

        if self._hover is not None and 0 <= self._hover < len(points):
            hover = points[self._hover]
            painter.setPen(QPen(QColor(self._palette.grid), 1))
            painter.drawLine(QPointF(hover.x(), plot.top()), QPointF(hover.x(), plot.bottom()))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette.surface))
            painter.drawEllipse(hover, 5, 5)
            painter.setBrush(QColor(color))
            painter.drawEllipse(hover, 3, 3)

    def _index_at(self, position: QPointF) -> int | None:
        if len(self._points) <= 1:
            return 0 if self._points else None
        plot = self._plot_rect()
        if not plot.adjusted(-8, 0, 8, 0).contains(position):
            return None
        ratio = (position.x() - plot.left()) / plot.width()
        return max(0, min(len(self._points) - 1, round(ratio * (len(self._points) - 1))))

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()
        if index is None:
            QToolTip.hideText()
            return
        point = self._points[index]
        label = self.METRICS[self._metric][0 if not self._low_profile else 1]
        text = f"{point.day} · {label} {_fmt(point.value(self._metric))}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        self._hover = None
        self.update()
        super().leaveEvent(event)


class DailyActivityChart(_ChartBase):
    """近 30 天每日活动：阅读 / 生词 / 复习堆叠柱。"""

    MARGIN_LEFT = 40
    MARGIN_RIGHT = 12
    MARGIN_TOP = 28
    MARGIN_BOTTOM = 24

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: tuple[ActivityDay, ...] = ()
        self._hover: int | None = None
        self.setMinimumHeight(200)

    def set_days(self, days: tuple[ActivityDay, ...]) -> None:
        self._days = tuple(days)
        self._hover = None
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.MARGIN_LEFT,
            self.MARGIN_TOP,
            max(10.0, self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT),
            max(10.0, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )

    def _series(self) -> tuple[tuple[str, int, str], ...]:
        return (
            (self._t("阅读", "Read"), 0, "articles"),
            (self._t("新增生词", "Words"), 1, "words"),
            (self._t("复习", "Reviews"), 2, "reviews"),
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._days or sum(day.total for day in self._days) <= 0:
            self._paint_empty(
                painter,
                self._t("最近 30 天还没有学习记录", "No activity in the last 30 days"),
            )
            return

        plot = self._plot_rect()
        y_max = nice_max(max(day.total for day in self._days))
        font = self._font(9)
        painter.setFont(font)

        # 图例在绘图区上方：色点承担身份，文字用 muted 色。
        legend_x = plot.left()
        for label, color_index, _key in self._series():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette.series[color_index]))
            painter.drawRoundedRect(QRectF(legend_x, 5, 8, 8), 2, 2)
            painter.setPen(QColor(self._palette.muted))
            painter.drawText(
                QRectF(legend_x + 12, 0, 86, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            legend_x += 96

        for step in range(4):
            ratio = step / 3
            y = plot.bottom() - plot.height() * ratio
            painter.setPen(QPen(QColor(self._palette.grid), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(self._palette.axis))
            painter.drawText(
                QRectF(0, y - 7, self.MARGIN_LEFT - 6, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _fmt(round(y_max * ratio)),
            )

        count = len(self._days)
        band = plot.width() / count
        bar_width = min(20.0, max(4.0, band * 0.55))

        # 悬停背景先画在柱子下面，避免半透明色盖住数据。
        if self._hover is not None and 0 <= self._hover < count:
            center = plot.left() + band * (self._hover + 0.5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(self._palette.track, 110))
            painter.drawRect(QRectF(center - band / 2, plot.top(), band, plot.height()))

        for index, day in enumerate(self._days):
            if day.total <= 0:
                continue
            center = plot.left() + band * (index + 0.5)
            total_height = day.total / y_max * plot.height()
            cursor = plot.bottom()
            segments = (
                (day.articles, self._palette.series[0]),
                (day.words, self._palette.series[1]),
                (day.reviews, self._palette.series[2]),
            )
            active = [(value, color) for value, color in segments if value > 0]
            for segment_index, (value, color) in enumerate(active):
                height = value / day.total * total_height
                top = cursor - height
                bottom = cursor - (2 if segment_index > 0 else 0)
                rect = QRectF(center - bar_width / 2, top, bar_width, max(0.0, bottom - top))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                if segment_index == len(active) - 1:
                    painter.drawPath(_rounded_top_path(rect, 4))
                else:
                    painter.drawRect(rect)
                cursor = top

        painter.setFont(font)
        painter.setPen(QColor(self._palette.axis))
        for index in range(0, count, 7):
            center = plot.left() + band * (index + 0.5)
            painter.drawText(
                QRectF(center - 22, plot.bottom() + 5, 44, 14),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._days[index].day[5:],
            )

        if self._hover is not None and 0 <= self._hover < count:
            center = plot.left() + band * (self._hover + 0.5)
            painter.setPen(QPen(QColor(self._palette.axis), 1))
            painter.drawLine(QPointF(center, plot.top()), QPointF(center, plot.bottom()))

    def _index_at(self, position: QPointF) -> int | None:
        plot = self._plot_rect()
        if not plot.contains(position) or not self._days:
            return None
        band = plot.width() / len(self._days)
        return max(0, min(len(self._days) - 1, int((position.x() - plot.left()) / band)))

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()
        if index is None:
            QToolTip.hideText()
            return
        day = self._days[index]
        if self._low_profile:
            text = f"{day.day} · read {day.articles} · words {day.words} · reviews {day.reviews}"
        else:
            text = f"{day.day} · 阅读 {day.articles} · 生词 {day.words} · 复习 {day.reviews}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        self._hover = None
        self.update()
        super().leaveEvent(event)


class WordStatusBar(_ChartBase):
    """生词本状态分布：一条 28px 高的分段条 + 带数字的图例。"""

    BAR_TOP = 14
    BAR_HEIGHT = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._split = WordStatusSplit()
        self.setMinimumHeight(92)

    def set_split(self, split: WordStatusSplit) -> None:
        self._split = split
        self.update()

    def _segments(self) -> tuple[tuple[str, int, str], ...]:
        return (
            (self._t("新词", "New"), self._split.new, self._palette.seq[0]),
            (self._t("学习中", "Learning"), self._split.learning, self._palette.seq[1]),
            (self._t("已掌握", "Mastered"), self._split.mastered, self._palette.seq[2]),
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        total = self._split.total
        if total <= 0:
            self._paint_empty(
                painter,
                self._t("生词本还是空的", "No saved words yet"),
            )
            return

        segments = [(label, value, color) for label, value, color in self._segments() if value > 0]
        gap = 2.0
        available = max(10.0, self.width() - gap * max(0, len(segments) - 1))
        # 卡片被同行的高图表撑高时，把分段条和图例垂直居中，避免全挤在顶部。
        content_height = self.BAR_HEIGHT + 26
        top = max(0.0, (self.height() - content_height) / 2)
        x = 0.0
        font = self._font(10, bold=True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        for _index, (_label, value, color) in enumerate(segments):
            width = available * value / total
            rect = QRectF(x, top, max(2.0, width), self.BAR_HEIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(rect, 4, 4)

            text = _fmt(value)
            if width >= metrics.horizontalAdvance(text) + 14:
                painter.setPen(QColor(_ink_on(color)))
                painter.drawText(
                    rect,
                    Qt.AlignmentFlag.AlignCenter,
                    text,
                )
            x += width + gap

        painter.setFont(self._font(10))
        legend_x = 0.0
        legend_y = top + self.BAR_HEIGHT + 8
        for label, value, color in self._segments():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(legend_x, legend_y + 4, 8, 8), 2, 2)
            painter.setPen(QColor(self._palette.muted))
            text = f"{label} {_fmt(value)}"
            painter.drawText(
                QRectF(legend_x + 12, legend_y, 120, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            legend_x += max(96.0, QFontMetrics(self._font(10)).horizontalAdvance(text) + 26)


class DueForecastChart(_ChartBase):
    """未来 N 天复习负载：每天一根柱，柱高 = 到期未掌握词数。"""

    MARGIN_LEFT = 34
    MARGIN_RIGHT = 10
    MARGIN_TOP = 22
    MARGIN_BOTTOM = 24

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: tuple[DueDay, ...] = ()
        self._hover: int | None = None
        self.setMinimumHeight(150)

    def set_days(self, days: tuple[DueDay, ...]) -> None:
        self._days = tuple(days)
        self._hover = None
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.MARGIN_LEFT,
            self.MARGIN_TOP,
            max(10.0, self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT),
            max(10.0, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._days or max((day.count for day in self._days), default=0) <= 0:
            self._paint_empty(
                painter,
                self._t("未来 7 天没有到期复习", "No reviews due in the next 7 days"),
            )
            return

        plot = self._plot_rect()
        peak = max(day.count for day in self._days)
        y_max = nice_max(peak)
        font = self._font(9)
        painter.setFont(font)

        for step in range(3):
            ratio = step / 2
            y = plot.bottom() - plot.height() * ratio
            painter.setPen(QPen(QColor(self._palette.grid), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(self._palette.axis))
            painter.drawText(
                QRectF(0, y - 7, self.MARGIN_LEFT - 6, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _fmt(round(y_max * ratio)),
            )

        slot = plot.width() / max(1, len(self._days))
        bar_width = max(8.0, slot * 0.56)
        color = QColor(self._palette.seq[1])
        for index, day in enumerate(self._days):
            x = plot.left() + slot * index + (slot - bar_width) / 2
            height = plot.height() * day.count / y_max
            rect = QRectF(x, plot.bottom() - height, bar_width, max(2.0, height))
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(color)
            if index == self._hover:
                fill = fill.lighter(120)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor(self._palette.axis))
            painter.drawText(
                QRectF(plot.left() + slot * index, plot.bottom() + 5, slot, 14),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                day.day[5:],
            )

        if self._hover is not None and 0 <= self._hover < len(self._days):
            day = self._days[self._hover]
            painter.setPen(QColor(self._palette.text))
            painter.setFont(self._font(10, bold=True))
            painter.drawText(
                QRectF(plot.left(), 1, plot.width(), 18),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                f"{day.day} · {_fmt(day.count)}",
            )

    def _index_at(self, position: QPointF) -> int | None:
        plot = self._plot_rect()
        if not plot.contains(position) or not self._days:
            return None
        slot = plot.width() / len(self._days)
        index = int((position.x() - plot.left()) // slot)
        return index if 0 <= index < len(self._days) else None

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()
        if index is None:
            QToolTip.hideText()
            return
        day = self._days[index]
        if self._low_profile:
            text = f"{day.day} · due {day.count}"
        else:
            text = f"{day.day} · 到期 {day.count} 个词"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        self._hover = None
        self.update()
        super().leaveEvent(event)


class HBarChart(_ChartBase):
    """横向条形图：错因次数 / 技能正确率等排行。

    `rows` 是 (标签, 数值) 有序序列，调用方负责排序（通常从大到小）。
    条形长度按最大值为 100% 归一，数值文本直接画在条形右侧。
    """

    ROW_HEIGHT = 26
    LABEL_WIDTH = 96

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[tuple[str, int], ...] = ()
        self._empty_zh = "还没有数据"
        self._empty_en = "No data yet"
        self._unit = ""
        self._hover: int | None = None
        self.setMinimumHeight(80)

    def set_rows(
        self,
        rows: tuple[tuple[str, int], ...],
        *,
        empty_zh: str = "还没有数据",
        empty_en: str = "No data yet",
        unit: str = "",
    ) -> None:
        self._rows = tuple(rows)
        self._empty_zh = empty_zh
        self._empty_en = empty_en
        self._unit = unit
        self._hover = None
        self.setMinimumHeight(max(80, len(self._rows) * self.ROW_HEIGHT + 12))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._rows:
            self._paint_empty(painter, self._t(self._empty_zh, self._empty_en))
            return

        peak = max(value for _label, value in self._rows)
        if peak <= 0:
            self._paint_empty(painter, self._t(self._empty_zh, self._empty_en))
            return

        bar_left = self.LABEL_WIDTH
        value_width = 46
        bar_right = max(bar_left + 10, self.width() - value_width)
        font = self._font(10)
        painter.setFont(font)
        for index, (label, value) in enumerate(self._rows):
            y = 6 + index * self.ROW_HEIGHT
            text_color = self._palette.accent if index == self._hover else self._palette.text
            painter.setPen(QColor(text_color))
            painter.drawText(
                QRectF(0, y, self.LABEL_WIDTH - 8, self.ROW_HEIGHT - 8),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            width = max(2.0, (bar_right - bar_left) * value / peak)
            rect = QRectF(bar_left, y + 4, width, self.ROW_HEIGHT - 16)
            bar_color = QColor(self._palette.series[0])
            if index == self._hover:
                bar_color = bar_color.lighter(120)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar_color)
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor(self._palette.muted))
            painter.drawText(
                QRectF(bar_right + 6, y, value_width - 6, self.ROW_HEIGHT - 8),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{_fmt(value)}{self._unit}",
            )

    def _index_at(self, position: QPointF) -> int | None:
        if position.x() < self.LABEL_WIDTH or not self._rows:
            return None
        index = int((position.y() - 6) // self.ROW_HEIGHT)
        return index if 0 <= index < len(self._rows) else None

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()
        if index is None:
            QToolTip.hideText()
            return
        label, value = self._rows[index]
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{label} · {_fmt(value)}{self._unit}",
            self,
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        self._hover = None
        self.update()
        super().leaveEvent(event)


class ScoreTrendChart(_ChartBase):
    """阅读检测得分趋势：按结果等级分线，0~100% 固定纵轴。"""

    MARGIN_LEFT = 38
    MARGIN_RIGHT = 66
    MARGIN_TOP = 28
    MARGIN_BOTTOM = 24

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: tuple[QuizSeries, ...] = ()
        self._hover: tuple[int, int] | None = None
        self.setMinimumHeight(210)

    def set_series(self, series: tuple[QuizSeries, ...]) -> None:
        self._series = tuple(series)
        self._hover = None
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.MARGIN_LEFT,
            self.MARGIN_TOP,
            max(10.0, self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT),
            max(10.0, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )

    @staticmethod
    def _color_index(level: str) -> int:
        try:
            return CEFR_LEVELS.index(level) % 4
        except ValueError:
            return 0

    def _date_span(self) -> tuple[date, date] | None:
        days = [
            parsed
            for series in self._series
            for point in series.points
            if (parsed := _parse_day(point.day)) is not None
        ]
        if not days:
            return None
        return min(days), max(days)

    def _x_at(self, day: date, plot: QRectF, span: tuple[date, date]) -> float:
        first, last = span
        if first == last:
            return plot.center().x()
        ratio = (day - first).days / (last - first).days
        return plot.left() + plot.width() * ratio

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        span = self._date_span()
        if not self._series or span is None:
            self._paint_empty(
                painter,
                self._t("还没有完成的阅读检测", "No reading quizzes completed yet"),
            )
            return

        plot = self._plot_rect()
        font = self._font(9)
        painter.setFont(font)

        for percent in (0, 25, 50, 75, 100):
            y = plot.bottom() - plot.height() * percent / 100
            painter.setPen(QPen(QColor(self._palette.grid), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(self._palette.axis))
            painter.drawText(
                QRectF(0, y - 7, self.MARGIN_LEFT - 8, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{percent}%",
            )

        # 图例：等级名 + 最新得分，文字用 muted 色，色块承担身份。
        # 两列卡片宽度有限，按可用宽度均分，避免 4 条等级线时图例被裁掉。
        legend_x = plot.left()
        item_width = max(56.0, plot.width() / max(1, len(self._series)))
        for series in self._series:
            color = self._palette.series[self._color_index(series.level)]
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(QPointF(legend_x, 10), QPointF(legend_x + 12, 10))
            painter.setPen(QColor(self._palette.muted))
            text = f"{series.level} {series.latest_percent}%"
            painter.drawText(
                QRectF(legend_x + 16, 1, max(30.0, item_width - 16), 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            legend_x += item_width

        placed: list[float] = []
        for series in self._series:
            color = self._palette.series[self._color_index(series.level)]
            points: list[tuple[QPointF, int]] = []
            for point in series.points:
                parsed = _parse_day(point.day)
                if parsed is None:
                    continue
                points.append(
                    (
                        QPointF(
                            self._x_at(parsed, plot, span),
                            plot.bottom() - plot.height() * point.percent / 100,
                        ),
                        point.percent,
                    )
                )
            if not points:
                continue
            if len(points) > 1:
                line = QPainterPath(points[0][0])
                for position, _percent in points[1:]:
                    line.lineTo(position)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(
                    QPen(
                        QColor(color),
                        2,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                )
                painter.drawPath(line)
            for position, _percent in points:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self._palette.surface))
                painter.drawEllipse(position, 6, 6)
                painter.setBrush(QColor(color))
                painter.drawEllipse(position, 4, 4)

            # 端点直接标等级和分数；碰撞时错开并用引线连回端点。
            end, percent = points[-1]
            label_y = end.y()
            for used in placed:
                if abs(label_y - used) < 14:
                    label_y = used + 14
            label_y = max(plot.top() + 8, min(plot.bottom() - 8, label_y))
            placed.append(label_y)
            label_x = plot.right() + 6
            painter.setPen(QPen(QColor(self._palette.grid), 1))
            painter.drawLine(QPointF(end.x() + 4, end.y()), QPointF(label_x, label_y))
            painter.setFont(self._font(10, bold=True))
            painter.setPen(QColor(self._palette.text))
            painter.drawText(
                QRectF(label_x + 2, label_y - 8, 50, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{series.level} {percent}%",
            )
            painter.setFont(font)

        painter.setPen(QColor(self._palette.axis))
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 5, 70, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            span[0].isoformat()[5:],
        )
        painter.drawText(
            QRectF(plot.right() - 70, plot.bottom() + 5, 70, 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            span[1].isoformat()[5:],
        )

        if self._hover is not None:
            index, point_index = self._hover
            if 0 <= index < len(self._series):
                series = self._series[index]
                if 0 <= point_index < len(series.points):
                    point = series.points[point_index]
                    parsed = _parse_day(point.day)
                    if parsed is not None:
                        x = self._x_at(parsed, plot, span)
                        painter.setPen(QPen(QColor(self._palette.grid), 1))
                        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    def _nearest(self, position: QPointF) -> tuple[int, int] | None:
        span = self._date_span()
        if span is None:
            return None
        plot = self._plot_rect()
        if not plot.adjusted(-10, -10, 10, 10).contains(position):
            return None
        best: tuple[int, int] | None = None
        best_distance = 14.0
        for series_index, series in enumerate(self._series):
            for point_index, point in enumerate(series.points):
                parsed = _parse_day(point.day)
                if parsed is None:
                    continue
                x = self._x_at(parsed, plot, span)
                y = plot.bottom() - plot.height() * point.percent / 100
                distance = math.hypot(position.x() - x, position.y() - y)
                if distance < best_distance:
                    best_distance = distance
                    best = (series_index, point_index)
        return best

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        nearest = self._nearest(event.position())
        if nearest != self._hover:
            self._hover = nearest
            self.update()
        if nearest is None:
            QToolTip.hideText()
            return
        series = self._series[nearest[0]]
        point = series.points[nearest[1]]
        text = f"{series.level} · {point.day} · {point.percent}%"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件签名
        self._hover = None
        self.update()
        super().leaveEvent(event)


__all__ = [
    "ChartPalette",
    "CumulativeChart",
    "DailyActivityChart",
    "DueForecastChart",
    "HBarChart",
    "HeatLegend",
    "HeatmapCalendar",
    "ScoreTrendChart",
    "WordStatusBar",
    "chart_palette",
]
