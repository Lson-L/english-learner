"""简化版 SM-2 间隔重复调度。

为什么单独一个文件：算法是纯函数（输入词条 + 评分，输出新词条），
不碰数据库、不碰界面，方便单测，也方便以后整体换成 FSRS。

四档评分（与复习页的按钮一一对应）：

| rating | 含义     | 行为                                             |
|--------|----------|--------------------------------------------------|
| 1      | 忘记     | 次数清零、间隔回到 1 天、易度 -0.2（最低 1.3）   |
| 2      | 模糊     | 次数 +1、易度 -0.15、间隔按 1.2 倍增长           |
| 3      | 认识     | 次数 +1、易度不变、间隔按易度增长                |
| 4      | 太简单   | 次数 +1、易度 +0.15、间隔按 1.3 × 易度增长       |

前两次成功复习固定为 1 天、6 天（SM-2 的经典起步），之后才乘易度。
`interval_days >= 30` 视为已掌握，不再出现在到期队列里。

Java 对照：
- `schedule()` ≈ 一个无状态的静态工具方法（类似 `DateUtils.plusDays`）；
- `ReviewService` ≈ 应用服务，负责“算法 + 持久化 + 统计”的组合；
- 所有方法同步阻塞，但只做本地 SQLite 操作，UI 线程直接调用即可。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta

from ..models.entities import Word, now_iso, today_str
from ..storage.repositories import ReviewRepo, StatsRepo, WordRepo

logger = logging.getLogger(__name__)

# 复习评分：页面按钮、快捷键、数据库里的 rating 都用这套数字。
RATING_FORGOT = 1
RATING_VAGUE = 2
RATING_KNOWN = 3
RATING_EASY = 4
VALID_RATINGS: tuple[int, ...] = (
    RATING_FORGOT,
    RATING_VAGUE,
    RATING_KNOWN,
    RATING_EASY,
)

RATING_LABELS: dict[int, str] = {
    RATING_FORGOT: "忘记",
    RATING_VAGUE: "模糊",
    RATING_KNOWN: "认识",
    RATING_EASY: "太简单",
}

MIN_EASE = 1.3
DEFAULT_EASE = 2.5
# 间隔达到这个天数就认为“已掌握”，不再排进每日队列。
MASTERED_INTERVAL_DAYS = 30
# 防止易度/间隔算到离谱的值（十年以后再复习没有意义）。
MAX_INTERVAL_DAYS = 365

_EASE_DELTA = {RATING_VAGUE: -0.15, RATING_KNOWN: 0.0, RATING_EASY: 0.15}


@dataclass(frozen=True)
class ReviewOutcome:
    """一次复习的结果：落库后的词条 + 评分前后间隔，供界面与日志使用。"""

    word: Word
    rating: int
    prev_interval: int
    next_interval: int


def _clamp_ease(value: float) -> float:
    return max(MIN_EASE, round(value, 2))


def schedule(word: Word, rating: int, *, on: date | None = None) -> Word:
    """按四档评分计算下一次复习时间，返回新的 Word（不落库）。

    纯函数：同样的输入永远得到同样的输出（`updated_at` 除外）。
    非法 rating 直接抛 ValueError，避免把脏数据写进数据库。
    """
    if rating not in VALID_RATINGS:
        raise ValueError(f"rating 必须是 {VALID_RATINGS} 之一，收到 {rating!r}")

    day = on or date.today()
    prev_interval = max(0, int(word.interval_days))
    ease = _clamp_ease(float(word.ease or DEFAULT_EASE))

    if rating == RATING_FORGOT:
        # 忘记：从头开始，但保留一点易度惩罚，让这个词下次更快出现。
        next_ease = _clamp_ease(ease - 0.2)
        repetitions = 0
        interval = 1
        status = "learning"
    else:
        next_ease = _clamp_ease(ease + _EASE_DELTA[rating])
        repetitions = int(word.repetitions) + 1
        if repetitions == 1:
            interval = 1
        elif repetitions == 2:
            interval = 6
        else:
            base = max(1, prev_interval)
            if rating == RATING_VAGUE:
                factor = 1.2
            elif rating == RATING_EASY:
                factor = next_ease * 1.3
            else:
                factor = next_ease
            interval = max(1, min(MAX_INTERVAL_DAYS, round(base * factor)))
        status = "mastered" if interval >= MASTERED_INTERVAL_DAYS else "learning"

    due_date = (day + timedelta(days=interval)).isoformat()
    return replace(
        word,
        ease=next_ease,
        interval_days=interval,
        repetitions=repetitions,
        status=status,
        due_date=due_date,
        updated_at=now_iso(),
    )


class ReviewService:
    """复习队列 + 评分落库 + 复习历史 + 每日统计。

    只依赖仓库，不依赖 AI / PySide6：断网时复习依然可用。
    """

    def __init__(
        self,
        word_repo: WordRepo,
        review_repo: ReviewRepo | None = None,
        stats_repo: StatsRepo | None = None,
        today: Callable[[], str] = today_str,
    ) -> None:
        self._words = word_repo
        self._logs = review_repo
        self._stats = stats_repo
        self._today = today

    # ------------------------------------------------------------------ 队列
    def due_words(self, limit: int = 200) -> list[Word]:
        """今天到期且未掌握的词，按到期日升序；limit 只是保护界面别一次加载太多。"""
        return self._words.list_due(self._today(), limit=limit)

    def count_due(self) -> int:
        return len(self.due_words())

    # ------------------------------------------------------------------ 评分
    def review(self, word: Word, rating: int) -> ReviewOutcome:
        """应用评分：写回调度字段 → 记一条复习历史 → 今日复习数 +1。"""
        if word.id is None:
            raise ValueError("review() 需要已落库的词条（word.id 不能为空）")

        updated = schedule(word, rating, on=date.fromisoformat(self._today()))
        saved = self._words.save_schedule(updated)

        if self._logs is not None:
            self._logs.log(
                word_id=int(word.id),
                rating=rating,
                prev_interval=int(word.interval_days),
                next_interval=int(saved.interval_days),
            )
        if self._stats is not None:
            self._stats.bump(self._today(), reviews_done=1)

        logger.info(
            "复习 word=%s rating=%s interval=%s→%s due=%s",
            saved.lemma,
            rating,
            word.interval_days,
            saved.interval_days,
            saved.due_date,
        )
        return ReviewOutcome(
            word=saved,
            rating=rating,
            prev_interval=int(word.interval_days),
            next_interval=int(saved.interval_days),
        )
