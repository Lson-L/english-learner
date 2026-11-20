"""阅读反馈调级：把“这篇读起来太难 / 太简单”变成一次重新定级 + 文章重写。

产品动机（用户提出）：
- 测评定的等级可能偏高，第一篇就生词密集，容易打击积极性；
- 希望读完能直接说“太难了 / 还行 / 太简单”，让 AI 结合今日生词重新定级，
  并立刻换一篇更合适的文章；
- 新文章尽量带上今天标注的新词，但**流畅自然优先**，带不上不勉强。

流程：
1. 收集今天标注的生词（优先）和今天查过的词；
2. 把当前画像、今日文章、生词、用户反馈交给 AI，只让它输出新的评级 JSON；
3. 用新等级 + 今日生词（软约束）重写文章；
4. 原子替换当天文章（旧高亮级联清除，生词本保留），并保存新 profile。

注意：本模块是同步阻塞的，只能在 TaskWorker 里调用。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..models.entities import (
    CEFR_LEVELS,
    Article,
    AssessmentResult,
    Profile,
    Word,
    normalize_cefr_level,
    now_iso,
    today_str,
)
from ..storage.repositories import ArticleRepo, ProfileRepo, WordRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .article_service import ArticleService
from .assessment_service import assessment_result_from_data
from .json_utils import JsonParseError, parse_json_object
from .prompts import PROMPT_VERSION, build_level_feedback_messages
from .readability import ReadabilityThresholds, default_wordlist

logger = logging.getLogger(__name__)

# 重写文章时最多带多少个今日生词；太多会挤占篇幅、伤害流畅度。
MAX_FEEDBACK_WORDS = 8

# 调级 JSON 的输出额度：只返回评级与简短说明。
LEVEL_FEEDBACK_MAX_TOKENS = 1200

# 反馈里的方向词：用于本地兜底，防止模型给出与用户感受相反或跨级的结果。
_DIFFICULT_MARKERS = (
    "太难",
    "生词太多",
    "读不下去",
    "很吃力",
    "吃力",
    "too hard",
    "too difficult",
    "hard to read",
    "difficult to read",
)
_EASY_MARKERS = (
    "太简单",
    "没挑战",
    "太容易",
    "too easy",
    "too simple",
    "not challenging",
)


def clamp_reassessed_level(current: str, target: str, feedback: str) -> str:
    """把“阅读反馈调级”的 AI 结果限制在合理范围。

    规则：
    1. 等级最多一次变化一档（与阅读检测、每周复盘保持一致）；
    2. 反馈明确说“太难/吃力”时，不允许升级；
    3. 反馈明确说“太简单/没挑战”时，不允许降级。

    注意：只在 AI 给出“反方向”时才纠正，不代替 AI 做“必须降/升”的决定；
    这样模型返回非法等级、回退到当前等级时不会被反馈词误伤。
    """
    current_level = normalize_cefr_level(current, "B1")
    target_level = normalize_cefr_level(target, current_level)
    current_index = CEFR_LEVELS.index(current_level)
    target_index = CEFR_LEVELS.index(target_level)

    text = (feedback or "").strip().lower()
    if any(marker in text for marker in _DIFFICULT_MARKERS) and target_index > current_index:
        target_index = max(0, current_index - 1)
    if any(marker in text for marker in _EASY_MARKERS) and target_index < current_index:
        target_index = min(len(CEFR_LEVELS) - 1, current_index + 1)

    if target_index > current_index:
        target_index = min(target_index, current_index + 1)
    elif target_index < current_index:
        target_index = max(target_index, current_index - 1)
    return CEFR_LEVELS[target_index]


@dataclass(frozen=True)
class LevelAdjustment:
    """一次“按阅读反馈调级”的完整结果，供界面展示。"""

    previous_level: str
    profile: Profile
    article: Article
    result: AssessmentResult
    target_words: tuple[str, ...]
    feedback: str

    @property
    def level_changed(self) -> bool:
        return self.profile.cefr_level != self.previous_level


class LevelFeedbackService:
    """重新定级 + 重写今日文章。一个实例只在一次后台任务里使用。"""

    def __init__(
        self,
        client: TextCompleter,
        profile_repo: ProfileRepo,
        article_repo: ArticleRepo,
        word_repo: WordRepo,
        *,
        readability: ReadabilityThresholds | None = None,
        wordlist_provider: Callable[[], dict[str, str]] = default_wordlist,
    ) -> None:
        self._client = client
        self._profiles = profile_repo
        self._articles = article_repo
        self._words = word_repo
        # 重写文章也要遵守同一份可读性配置（关闭 / 阈值 / 是否重试）。
        self._article_service = ArticleService(
            client,
            article_repo,
            word_repo,
            readability=readability,
            wordlist_provider=wordlist_provider,
        )

    # ------------------------------------------------------------------ 生词
    def collect_today_words(self, article_id: int | None, today: str | None = None) -> list[Word]:
        """今日生词：优先取本篇正文里标注过的，再补今天查过的，去重限量。"""
        day = today or today_str()
        candidates: list[Word] = []
        if article_id is not None:
            candidates.extend(self._words.list_highlighted_words(article_id))
        candidates.extend(self._words.list_created_on(day))

        seen: set[str] = set()
        words: list[Word] = []
        for word in candidates:
            key = word.lemma.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            words.append(word)
            if len(words) >= MAX_FEEDBACK_WORDS:
                break
        return words

    # ------------------------------------------------------------------ 定级
    def reassess(
        self,
        profile: Profile,
        feedback: str,
        words: list[Word],
        article: Article | None,
    ) -> AssessmentResult:
        """只重新定级，不改数据库。解析失败抛 AIError。"""
        display_words = [word.display_form or word.lemma for word in words]
        messages = build_level_feedback_messages(profile, feedback, display_words, article)
        logger.info(
            "阅读反馈调级 prompt=%s current=%s words=%s",
            PROMPT_VERSION,
            profile.cefr_level,
            len(words),
        )
        text = self._client.complete_json(
            messages,
            temperature=0.2,
            max_tokens=LEVEL_FEEDBACK_MAX_TOKENS,
            task="阅读反馈调级",
        )
        try:
            data = parse_json_object(text)
        except JsonParseError as exc:
            logger.warning("调级 JSON 解析失败：%s", exc)
            raise AIError(
                "AI 调级结果无法解析",
                "模型没有按 JSON 格式返回，可以重试一次，或直接换一篇。",
            ) from exc
        return assessment_result_from_data(data, profile.cefr_level)

    # ------------------------------------------------------------------ 主流程
    def adjust(self, feedback: str, today: str | None = None) -> LevelAdjustment:
        """重新定级并重写今日文章；任何失败都不改动现有文章与画像。"""
        text = (feedback or "").strip()
        if not text:
            raise AIError(
                "请先写下这次的阅读感受",
                "例如：生词太多、句子太长、读得很吃力，或者太简单、没挑战。",
            )

        day = today or today_str()
        profile = self._profiles.get()
        if profile is None:
            raise AIError(
                "还没有水平测评结果",
                "请先在首页完成一次摸底测评，再调整阅读难度。",
            )
        article = self._articles.get_by_date(day)
        if article is None:
            raise AIError(
                "今天还没有文章",
                "请先生成今日文章，再写下阅读感受。",
            )

        # 先取词：重写文章后旧高亮会被级联删除。
        words = self.collect_today_words(article.id, day)
        result = self.reassess(profile, text, words, article)
        clamped = clamp_reassessed_level(profile.cefr_level, result.cefr, text)
        if clamped != result.cefr:
            logger.info(
                "阅读反馈调级被本地收敛：%s → AI %s → 实际 %s",
                profile.cefr_level,
                result.cefr,
                clamped,
            )
            result = replace(result, cefr=clamped)
        result = self._merge_profile_details(profile, result)
        target_words = [word.lemma for word in words]

        # 先生成新文章（软约束目标词），成功后再替换；失败时旧文章原样保留。
        draft = self._article_service.generate_article(
            result.cefr,
            target_words,
            article.topic,
            strict_targets=False,
            learner_feedback=text,
            learner_focus="、".join(
                item.strip() for item in profile.weaknesses if item and item.strip()
            ),
            # 明确避开刚读完的这篇，否则模型容易换个说法重写同一篇。
            avoid_titles=[article.title],
        )
        new_article = self._article_service.replace_today(draft, today=day)

        timestamp = now_iso()
        new_profile = self._profiles.save(
            Profile(
                cefr_level=result.cefr,
                vocab_estimate=result.vocab_estimate,
                strengths=result.strengths,
                weaknesses=result.weaknesses,
                comment=result.comment
                or f"根据阅读反馈由 {profile.cefr_level} 调整为 {result.cefr}。",
                assessed_at=timestamp,
                updated_at=timestamp,
            )
        )
        logger.info(
            "阅读反馈调级完成：%s → %s，新文章《%s》%s 词",
            profile.cefr_level,
            new_profile.cefr_level,
            new_article.title,
            new_article.word_count,
        )
        return LevelAdjustment(
            previous_level=profile.cefr_level,
            profile=new_profile,
            article=new_article,
            result=result,
            target_words=tuple(target_words),
            feedback=text,
        )

    @staticmethod
    def _merge_profile_details(profile: Profile, result: AssessmentResult) -> AssessmentResult:
        """AI 少给字段时保留旧画像，别让一次调级把词汇量/优势清空。"""
        return AssessmentResult(
            cefr=result.cefr,
            vocab_estimate=result.vocab_estimate or profile.vocab_estimate,
            strengths=result.strengths or profile.strengths,
            weaknesses=result.weaknesses or profile.weaknesses,
            comment=result.comment,
        )


__all__ = ["LevelAdjustment", "LevelFeedbackService", "clamp_reassessed_level"]
