"""每日文章的生成、校验与缓存。

设计要点（对应 PROGRESS.md M1-4）：
- 一天一篇，`articles.article_date` 唯一；当天已有直接返回，不花 token；
- AI 返回的 JSON 必须通过本地校验，不通过就重试一次，仍失败抛 AIError；
- “换一篇”= 删除当天记录再生成（会级联删除高亮，但生词本保留）；
- 生成过程是同步阻塞的，调用方必须放进 TaskWorker。
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from ..models.entities import Article, Profile, now_iso, today_str
from ..storage.db import DuplicateError
from ..storage.repositories import ArticleRepo, WordRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .json_utils import JsonParseError, as_str_list, as_text, parse_json_object
from .prompts import PROMPT_VERSION, build_article_messages

logger = logging.getLogger(__name__)

# 本地校验区间：比 prompt 要求的 250~350 略宽，避免模型差几个词就整篇作废。
WORD_COUNT_MIN = 200
WORD_COUNT_MAX = 400
MIN_PARAGRAPHS = 3
MAX_TARGET_WORDS = 6
DUE_WORDS_PER_ARTICLE = 5

# 每天按日期轮换主题，既稳定又不至于天天同一个话题。
TOPICS: tuple[str, ...] = (
    "science",
    "psychology",
    "history",
    "technology",
    "health",
    "environment",
    "culture",
    "travel",
)

# “换一篇”时随机给模型一个切入角度。
# 只换主题还不够：同一天的重复生成如果 prompt 完全一样，模型（尤其对
# temperature 不敏感的服务端）会给出几乎相同的文章，所以必须让 prompt 本身变化。
ANGLE_HINTS: tuple[str, ...] = (
    "讲一个具体人物的真实经历",
    "从一个常见误解说起",
    "围绕一个具体地点展开",
    "用一项新研究或数据开头",
    "对比两种相反的做法",
    "从一件日常小事切入",
    "讲一段少有人知的历史细节",
    "回答一个孩子常问的问题",
    "描述一天之内的变化",
    "围绕一个具体的物品展开",
)

# 传给模型“别再写这些”的历史标题上限，太多会稀释指令。
AVOID_TITLE_LIMIT = 6


class InvalidArticleError(ValueError):
    """AI 返回的文章不满足本地校验规则。"""


@dataclass(frozen=True)
class ArticleDraft:
    """通过校验、尚未入库的文章草稿。"""

    title: str
    level: str
    topic: str
    paragraphs: tuple[str, ...]
    target_words: tuple[str, ...] = ()
    glossary: tuple[tuple[str, str], ...] = ()

    @property
    def content(self) -> str:
        return "\n\n".join(self.paragraphs)

    @property
    def word_count(self) -> int:
        return len(self.content.split())


def parse_article_draft(
    text: str, expected_level: str, *, require_targets: bool = True
) -> ArticleDraft:
    """解析并校验 AI 返回的文章 JSON；任何不合规都抛 InvalidArticleError。

    require_targets=False 用于“按阅读反馈重写”：生词能自然带上最好，
    带不上不判失败，优先保证文章流畅。
    """
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidArticleError(str(exc)) from exc

    expected = expected_level.strip().upper()
    title = as_text(data.get("title"))
    if not title:
        raise InvalidArticleError("标题为空")

    level = as_text(data.get("level"), expected).upper()
    if expected and level != expected:
        raise InvalidArticleError(f"等级不符：要求 {expected}，返回 {level}")

    paragraphs = _parse_paragraphs(data.get("paragraphs"))
    if len(paragraphs) < MIN_PARAGRAPHS:
        raise InvalidArticleError(f"段落数不足：至少 {MIN_PARAGRAPHS} 段，实际 {len(paragraphs)}")

    content = "\n\n".join(paragraphs)
    word_count = len(content.split())
    if not (WORD_COUNT_MIN <= word_count <= WORD_COUNT_MAX):
        raise InvalidArticleError(
            f"词数不在 {WORD_COUNT_MIN}~{WORD_COUNT_MAX} 之间（实际 {word_count}）"
        )

    target_words = tuple(
        word.lower() for word in as_str_list(data.get("target_words")) if word.strip()
    )[:MAX_TARGET_WORDS]
    if require_targets:
        missing = [
            word for word in target_words if not re.search(rf"\b{re.escape(word)}\b", content, re.I)
        ]
        if missing:
            raise InvalidArticleError("目标词未出现在正文中：" + ", ".join(missing))

    return ArticleDraft(
        title=title[:200],
        level=level or expected,
        topic=as_text(data.get("topic")),
        paragraphs=paragraphs,
        target_words=target_words,
        glossary=_parse_glossary(data.get("glossary")),
    )


def _parse_paragraphs(value: object) -> tuple[str, ...]:
    """兼容 list 与“用空行分隔的整段字符串”两种返回。"""
    if isinstance(value, str):
        raw_items: list[object] = re.split(r"\n\s*\n", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        return ()
    return tuple(text for text in (as_text(item) for item in raw_items) if text)


def _parse_glossary(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    items: list[tuple[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        word = as_text(entry.get("word")).lower()
        meaning = as_text(entry.get("meaning_cn"))
        if word and meaning:
            items.append((word, meaning))
    return tuple(items)


class ArticleService:
    """文章生成 + 当天缓存。一个实例只在一次后台任务里使用。"""

    def __init__(
        self,
        client: TextCompleter,
        article_repo: ArticleRepo,
        word_repo: WordRepo | None = None,
    ) -> None:
        self._client = client
        self._articles = article_repo
        self._words = word_repo

    # ------------------------------------------------------------ 对外入口
    def get_or_create_today(self, profile: Profile, today: str | None = None) -> Article:
        day = today or today_str()
        existing = self._articles.get_by_date(day)
        if existing is not None:
            logger.info("命中当天文章缓存：%s", day)
            return existing

        target_words = self._pick_target_words(day)
        topic = self._pick_topic(day)
        draft = self.generate_article(
            profile.cefr_level, target_words, topic, avoid_titles=self._avoid_titles(day)
        )
        return self._store(day, draft)

    def generate_article(
        self,
        level: str,
        target_words: list[str] | tuple[str, ...] = (),
        topic: str = "",
        *,
        strict_targets: bool = True,
        learner_feedback: str = "",
        angle: str | None = None,
        avoid_titles: list[str] | tuple[str, ...] = (),
        user_request: str = "",
    ) -> ArticleDraft:
        """调用 AI 生成文章；JSON 不合法或校验不过时重试一次。

        strict_targets=False 表示目标词是“尽量带上”的软约束（阅读反馈重写用）。
        learner_feedback 会一并传给模型，用来控制生词密度与句子长度。
        angle=None 时随机选一个切入角度，传空字符串表示不加角度；
        avoid_titles 列出“别再写”的历史标题，保证每次生成（尤其“换一篇”）
        的 prompt 本身就不一样；user_request 是用户在弹窗里写的具体要求。
        """
        auto_angle = angle is None
        chosen_angle = self.pick_angle() if auto_angle else (angle or "").strip()
        last_reason = ""
        for attempt in range(2):
            if attempt > 0 and auto_angle:
                # 重试时换一个切入角度：同一份 prompt 再发一次，模型多半还是给同样的结果。
                chosen_angle = self.pick_angle()
            messages = build_article_messages(
                level.strip().upper(),
                list(target_words),
                topic,
                soft_targets=not strict_targets,
                learner_feedback=learner_feedback,
                angle=chosen_angle,
                avoid_titles=avoid_titles,
                user_request=user_request,
            )
            logger.info(
                "生成文章 prompt=%s level=%s attempt=%s topic=%s angle=%s "
                "target_words=%s soft=%s avoid=%s request=%s",
                PROMPT_VERSION,
                level,
                attempt + 1,
                topic or "-",
                chosen_angle or "-",
                list(target_words),
                not strict_targets,
                len(avoid_titles),
                user_request.strip() or "-",
            )
            text = self._client.complete_json(messages, temperature=0.8)
            try:
                return parse_article_draft(text, level, require_targets=strict_targets)
            except InvalidArticleError as exc:
                last_reason = str(exc)
                logger.warning("文章未通过校验（第 %s 次）：%s", attempt + 1, exc)
        raise AIError(
            "AI 生成的文章没有通过校验",
            f"最后一次原因：{last_reason}。可以点重试，或换一个更稳定的模型。",
        )

    def regenerate_today(
        self,
        profile: Profile,
        today: str | None = None,
        target_words: list[str] | tuple[str, ...] | None = None,
        *,
        strict_targets: bool = True,
        user_request: str = "",
    ) -> Article:
        """换一篇：先生成新文章，成功后原子替换当天记录（级联清理旧高亮）。

        先生成再替换，AI 失败时旧文章与高亮保持原样，不会出现“删了却生成不出来”。
        target_words 为 None 时按到期生词自动挑选。
        user_request 是用户在弹窗里写的方向（可空）；为空时由 AI 自己选题。

        没有用户输入时，为了避免“换一篇还是同一篇”，会做三件事：
        1. 主题避开当天轮换主题与当前文章的话题（同一天按日期算出的主题是固定的）；
        2. 把当前标题和最近几天的标题一并告诉模型，明确要求换选题；
        3. prompt 里带一个随机的切入角度，保证两次请求内容不同。
        """
        day = today or today_str()
        existing = self._articles.get_by_date(day)
        targets = list(target_words) if target_words is not None else self._pick_target_words(day)
        request = (user_request or "").strip()

        if request:
            # 用户点名了方向：主题和角度都交给这句话，不再叠加随机因素。
            topic = ""
            angle = ""
        else:
            # 同时避开“当天轮换主题”和“当前文章自报的主题”：前者是生成时真正发出去的，
            # 后者是模型在 JSON 里回填的，两者可能不一致。
            avoid_topics = [self._pick_topic(day)]
            if existing is not None:
                avoid_topics.append(existing.topic)
            topic = self._pick_topic(day, avoid=avoid_topics)
            angle = None

        draft = self.generate_article(
            profile.cefr_level,
            targets,
            topic,
            strict_targets=strict_targets,
            angle=angle,
            avoid_titles=self._avoid_titles(day, current_title=existing.title if existing else ""),
            user_request=request,
        )
        return self.replace_today(draft, today=day)

    def replace_today(self, draft: ArticleDraft, today: str | None = None) -> Article:
        """把一篇已通过校验的草稿原子替换为当天的文章。"""
        day = today or today_str()
        article = Article(
            article_date=day,
            title=draft.title,
            content=draft.content,
            level=draft.level,
            topic=draft.topic,
            word_count=draft.word_count,
            target_words=draft.target_words,
            created_at=now_iso(),
        )
        stored = self._articles.replace_by_date(article)
        logger.info("文章已替换：%s《%s》%s 词", day, stored.title, stored.word_count)
        return stored

    # -------------------------------------------------------------- 内部
    def _store(self, day: str, draft: ArticleDraft) -> Article:
        article = Article(
            article_date=day,
            title=draft.title,
            content=draft.content,
            level=draft.level,
            topic=draft.topic,
            word_count=draft.word_count,
            target_words=draft.target_words,
            created_at=now_iso(),
        )
        try:
            stored = self._articles.insert(article)
        except DuplicateError:
            # 另一个线程刚生成完同一天的文章：直接回读，不重复生成。
            existing = self._articles.get_by_date(day)
            if existing is None:
                raise
            stored = existing
        logger.info("文章已入库：%s《%s》%s 词", day, stored.title, stored.word_count)
        return stored

    def _pick_target_words(self, day: str) -> list[str]:
        if self._words is None:
            return []
        due = self._words.list_due(day, limit=DUE_WORDS_PER_ARTICLE * 2)
        seen: list[str] = []
        for word in due:
            if word.lemma and word.lemma not in seen:
                seen.append(word.lemma)
            if len(seen) >= DUE_WORDS_PER_ARTICLE:
                break
        return seen

    @staticmethod
    def _pick_topic(day: str, *, avoid: Iterable[str] = ()) -> str:
        """按日期轮换主题；命中 avoid 里的任何话题时随机换一个（“换一篇”用）。"""
        try:
            ordinal = date.fromisoformat(day).toordinal()
        except ValueError:
            ordinal = 0
        topic = TOPICS[ordinal % len(TOPICS)]
        blocked = {item.strip().lower() for item in avoid if item and item.strip()}
        if topic in blocked:
            alternatives = [item for item in TOPICS if item not in blocked] or list(TOPICS)
            topic = random.choice(alternatives)
        return topic

    @staticmethod
    def pick_angle() -> str:
        """随机选一个切入角度，让重复生成时的 prompt 本身产生差异。"""
        return random.choice(ANGLE_HINTS)

    def _avoid_titles(self, day: str, *, current_title: str = "") -> list[str]:
        """收集“别再写”的标题：当前文章（换一篇时）+ 最近几天的文章。"""
        titles: list[str] = []
        if current_title.strip():
            titles.append(current_title.strip())
        for article in self._articles.list_recent(AVOID_TITLE_LIMIT + 4):
            if article.article_date == day or not article.title.strip():
                continue
            if article.title.strip() in titles:
                continue
            titles.append(article.title.strip())
            if len(titles) >= AVOID_TITLE_LIMIT:
                break
        return titles[:AVOID_TITLE_LIMIT]


__all__ = [
    "ArticleDraft",
    "ArticleService",
    "InvalidArticleError",
    "parse_article_draft",
]
