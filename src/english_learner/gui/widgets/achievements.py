"""成就墙：把统计服务算好的成就渲染成一格格徽章。

徽章只用主题色：未解锁是灰底灰字，解锁后变成浅强调底 + 强调描边；
数字与文案都来自 `StatsOverviewService`，这里不做任何统计计算。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...services.stats_overview import Achievement


class AchievementBadge(QFrame):
    """单个成就：圆形标记 + 标题 / 说明 + 进度条 + 解锁状态。"""

    def __init__(
        self,
        achievement: Achievement,
        *,
        low_profile: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("achievementBadge")
        self.setMinimumHeight(78)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        self._mark = QLabel("·")
        self._mark.setObjectName("badgeMark")
        self._mark.setFixedSize(28, 28)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self._title = QLabel("")
        self._title.setObjectName("badgeTitle")
        title_row.addWidget(self._title, 1)
        self._state = QLabel("")
        self._state.setObjectName("badgeState")
        self._state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self._state, 0)
        body.addLayout(title_row)

        self._desc = QLabel("")
        self._desc.setObjectName("badgeDesc")
        self._desc.setWordWrap(True)
        body.addWidget(self._desc)

        self._progress = QProgressBar()
        self._progress.setObjectName("badgeProgress")
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        body.addWidget(self._progress)

        root.addLayout(body, 1)
        self._low_profile = bool(low_profile)
        self.set_achievement(achievement)

    def set_achievement(self, achievement: Achievement) -> None:
        unlocked = achievement.unlocked
        self._title.setText(achievement.title)
        self._desc.setText(achievement.description)
        self._progress.setValue(round(achievement.progress * 1000))
        if unlocked:
            # 历史数据能推导出首次达标日期时顺带展示，比单纯“已解锁”更有回看价值。
            date_text = achievement.unlocked_on[5:] if achievement.unlocked_on else ""
            if self._low_profile:
                state = f"Unlocked {date_text}".strip()
            else:
                state = f"已解锁 · {date_text}".strip(" ·")
        else:
            state = f"{achievement.current:,}/{achievement.target:,}"
        self._state.setText(state)
        self._mark.setText("✓" if unlocked else "·")
        self.setProperty("unlocked", "true" if unlocked else "false")
        # 动态属性变化后必须重新 polish，QSS 的属性选择器才会生效。
        self.style().unpolish(self)
        self.style().polish(self)
        if self._low_profile:
            tip = f"{achievement.description} · {achievement.current}/{achievement.target}"
        else:
            tip = f"{achievement.description} · 当前 {achievement.current}/{achievement.target}"
        if unlocked and achievement.unlocked_on:
            tip += f" · {achievement.unlocked_on}"
        self.setToolTip(tip)


class AchievementWall(QWidget):
    """3 列徽章网格；刷新时整体重建，数量固定为 12 个，开销可以忽略。"""

    COLUMNS = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        for column in range(self.COLUMNS):
            self._grid.setColumnStretch(column, 1)
        self._achievements: tuple[Achievement, ...] = ()
        self._badges: list[AchievementBadge] = []
        self._low_profile = False

    def set_achievements(self, achievements: tuple[Achievement, ...]) -> None:
        self._achievements = tuple(achievements)
        self._rebuild()

    def set_low_profile(self, low_profile: bool) -> None:
        low_profile = bool(low_profile)
        if low_profile == self._low_profile:
            return
        self._low_profile = low_profile
        self._rebuild()

    def _rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._badges = []
        for index, achievement in enumerate(self._achievements):
            badge = AchievementBadge(
                achievement,
                low_profile=self._low_profile,
                parent=self,
            )
            self._grid.addWidget(badge, index // self.COLUMNS, index % self.COLUMNS)
            self._badges.append(badge)

    @property
    def badges(self) -> tuple[AchievementBadge, ...]:
        return tuple(self._badges)


__all__ = ["AchievementBadge", "AchievementWall"]
