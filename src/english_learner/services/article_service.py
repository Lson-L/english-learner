"""每日文章的生成、校验与缓存。

设计要点（对应 HISTORY.md M1-4）：
- 一天一篇，`articles.article_date` 唯一；当天已有直接返回，不花 token；
- AI 返回的 JSON 必须通过本地校验，不通过就重试一次，仍失败抛 AIError；
- “换一篇”= 删除当天记录再生成（会级联删除高亮，但生词本保留）；
- 生成过程是同步阻塞的，调用方必须放进 TaskWorker。
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date

from ..models.entities import Article, Profile, RealText, Word, now_iso, today_str
from ..storage.db import DuplicateError
from ..storage.repositories import ArticleRepo, RealTextRepo, WordRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .json_utils import JsonParseError, as_str_list, as_text, parse_json_object
from .learner_memory import LearnerMemory, load_memory
from .level_progress import DifficultyHint
from .prompts import (
    MEMORY_TASK_ARTICLE,
    PROMPT_VERSION,
    build_article_messages,
    build_learner_memory_block,
)
from .readability import (
    KNOWN_INTERVAL_DAYS,
    ReadabilityMetrics,
    ReadabilityThresholds,
    analyze,
    default_wordlist,
    threshold_warnings,
)

logger = logging.getLogger(__name__)

# 本地校验区间：比 prompt 要求的 250~350 略宽，避免模型差几个词就整篇作废。
WORD_COUNT_MIN = 200
WORD_COUNT_MAX = 400
MIN_PARAGRAPHS = 3
MAX_TARGET_WORDS = 6
DUE_WORDS_PER_ARTICLE = 5

# 文章 JSON 的输出额度：正文 250~350 词 + 目标词 + 难词表（8~15 条），
# 正常只需 1200~1800 token；但带 thinking 的模型会把 reasoning token 也算进
# 输出额度（实测同一模型单次 3000~5000，偶发飙到 5000 直接截断），
# 所以留足 15000 的余量。max_tokens 只是上限，不生成就不计费；
# 网关实测接受 15000（64000 也能接受）。真正的兜底是 300s 读超时，
# 超时后由用户在界面上点「重试生成」，不会自动重试。
ARTICLE_MAX_TOKENS = 15000

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


def load_hint(provider: Callable[[], DifficultyHint | None] | None) -> DifficultyHint:
    """读取档内难度提示；失败或为空时退化为空提示（prompt 与旧版一致）。"""
    if provider is None:
        return DifficultyHint()
    try:
        hint = provider()
    except Exception:  # noqa: BLE001 - 提示是增强信息，失败不能拖垮生成
        logger.warning("读取档内难度提示失败，按默认难度生成", exc_info=True)
        return DifficultyHint()
    return hint if isinstance(hint, DifficultyHint) else DifficultyHint()


class InvalidArticleError(ValueError):
    """AI 返回的文章不满足本地校验规则。"""


@dataclass(frozen=True)
class GlossaryEntry:
    """文章自带的难词释义：只写入查词缓存，不直接进生词本。"""

    word: str  # 正文中的形态
    meaning_cn: str
    lemma: str = ""
    pos: str = ""
    phonetic: str = ""

    @property
    def base(self) -> str:
        """词典原形；模型没给时退回正文词形。"""
        return (self.lemma or self.word).strip().lower()


@dataclass(frozen=True)
class ArticleDraft:
    """通过校验、尚未入库的文章草稿。"""

    title: str
    level: str
    topic: str
    paragraphs: tuple[str, ...]
    target_words: tuple[str, ...] = ()
    glossary: tuple[GlossaryEntry, ...] = ()
    # M5.3 本地可读性指标；只在服务层计算，解析器本身不碰词表。
    metrics: ReadabilityMetrics | None = None
    # M6.5 档内位置：low / mid / high 及推导理由，随指标一起入库。
    within_level: str = ""
    hint_reason: str = ""
    # M7：文章来源。AI 生成保持 generated；真实文章写 real + real_texts.id。
    source: str = "generated"
    source_url: str = ""
    source_ref: str = ""

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
        missing = [word for word in target_words if not _contains_word_form(content, word)]
        if missing:
            raise InvalidArticleError("目标词未出现在正文中：" + ", ".join(missing))

    return ArticleDraft(
        title=title[:200],
        level=level or expected,
        topic=as_text(data.get("topic")),
        paragraphs=paragraphs,
        target_words=target_words,
        glossary=_parse_glossary(data.get("glossary"), content),
    )


def _focus_from_profile(profile: Profile) -> str:
    """把画像里的薄弱点压成一行软提示（最多 3 条）。"""
    items = [item.strip() for item in profile.weaknesses if item and item.strip()]
    return "、".join(items[:3])


def _plan_text(memory: LearnerMemory) -> str:
    """上周复盘重点 → 文章 prompt 的一行软约束（M5.2 计划回流）。"""
    return "、".join(item.strip() for item in memory.focus if item and item.strip())[:160]


def _contains_word_form(content: str, word: str) -> bool:
    """判断正文是否出现了目标词（允许常见词形变化）。

    旧实现只接受完全相同的词，`resilient → resilience` 这种派生词会被误判为
    “没写目标词”，白白重生成一整篇。这里依次接受：原词、常见屈折后缀、
    以及长词的词干匹配（覆盖 -ce/-cy/-ness 等派生）。
    """
    word = word.strip().lower()
    if not word:
        return False
    if re.search(rf"\b{re.escape(word)}\b", content, re.I):
        return True
    if len(word) >= 4 and re.search(
        rf"\b{re.escape(word)}(?:s|es|ed|d|ing|er|est|ly)\b",
        content,
        re.I,
    ):
        return True
    # study → studies / studied；make → making。
    if len(word) >= 3 and word.endswith("y"):
        stem = word[:-1]
        if re.search(rf"\b{re.escape(stem)}(?:ies|ied|ying)\b", content, re.I):
            return True
    if len(word) >= 4 and word.endswith("e"):
        if re.search(rf"\b{re.escape(word[:-1])}ing\b", content, re.I):
            return True
    if len(word) >= 6:
        stem = word[:-2]
        if re.search(rf"\b{re.escape(stem)}[a-z]{{2,6}}\b", content, re.I):
            return True
    return False


def _parse_paragraphs(value: object) -> tuple[str, ...]:
    """兼容 list 与“用空行分隔的整段字符串”两种返回。"""
    if isinstance(value, str):
        raw_items: list[object] = re.split(r"\n\s*\n", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        return ()
    return tuple(text for text in (as_text(item) for item in raw_items) if text)


_GLOSSARY_WORD_RE = re.compile(r"[a-z][a-z'\-]*")
MAX_GLOSSARY_ENTRIES = 20


def _parse_glossary(value: object, content: str = "") -> tuple[GlossaryEntry, ...]:
    """解析文章自带的难词表。

    容错优先：`glossary` 缺失、格式混乱、或词没有真正出现在正文里，都只是
    少缓存几个词，**绝不触发文章重试**。只接受单词（不含空格、数字、中文），
    同词形去重，最多保留 20 条。
    """
    if not isinstance(value, list):
        return ()

    lowered = (content or "").lower()
    seen: set[str] = set()
    items: list[GlossaryEntry] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        word = as_text(entry.get("word")).strip().strip("\"'“”‘’()[]{}.,;:!?").lower()
        meaning = as_text(entry.get("meaning_cn"))
        if not word or not meaning or not _GLOSSARY_WORD_RE.fullmatch(word):
            continue
        if word in seen:
            continue
        # 模型偶尔会编造正文里没有的词；只缓存真实出现过的，避免污染查词缓存。
        if lowered and not re.search(rf"\b{re.escape(word)}\b", lowered):
            continue
        lemma = as_text(entry.get("lemma")).strip().strip("\"'“”‘’()[]{}.,;:!?").lower()
        if lemma and not _GLOSSARY_WORD_RE.fullmatch(lemma):
            lemma = ""
        seen.add(word)
        items.append(
            GlossaryEntry(
                word=word,
                meaning_cn=meaning[:200],
                lemma=lemma or word,
                pos=as_text(entry.get("pos"))[:20],
                phonetic=as_text(entry.get("phonetic"))[:60],
            )
        )
        if len(items) >= MAX_GLOSSARY_ENTRIES:
            break
    return tuple(items)


class ArticleService:
    """文章生成 + 当天缓存。一个实例只在一次后台任务里使用。"""

    def __init__(
        self,
        client: TextCompleter,
        article_repo: ArticleRepo,
        word_repo: WordRepo | None = None,
        *,
        memory_provider: Callable[[], LearnerMemory] | None = None,
        hint_provider: Callable[[], DifficultyHint | None] | None = None,
        readability: ReadabilityThresholds | None = None,
        wordlist_provider: Callable[[], dict[str, str]] = default_wordlist,
        quiz_seeder: Callable[[Article, RealText], object] | None = None,
        real_text_repo: RealTextRepo | None = None,
    ) -> None:
        self._client = client
        self._articles = article_repo
        self._words = word_repo
        self._memory_provider = memory_provider
        # M6.5：档内难度提示由 AppContext 注入，文章服务不直接依赖检测 / 复习仓库。
        self._hint_provider = hint_provider
        # 可读性指标是“记录 / 告警 / 展示”用的增强信息，阈值可配、默认不强制重试。
        self._readability = readability or ReadabilityThresholds()
        self._wordlist_provider = wordlist_provider
        # M7：真实文章上架需要“文章 + 预置题目 + 供给历史”原子完成。
        # 用可调用对象注入题目种子，避免 ArticleService 反向依赖检测服务。
        self._quiz_seeder = quiz_seeder
        self._real_texts = real_text_repo

    # ------------------------------------------------------------ 对外入口
    def get_or_create_today(self, profile: Profile, today: str | None = None) -> Article:
        day = today or today_str()
        existing = self._articles.get_by_date(day)
        if existing is not None:
            logger.info("命中当天文章缓存：%s", day)
            return existing

        target_words = self._pick_target_words(day)
        topic = self._pick_topic(day)
        memory = load_memory(self._memory_provider)
        hint = load_hint(self._hint_provider)
        draft = self.generate_article(
            profile.cefr_level,
            target_words,
            topic,
            avoid_titles=self._avoid_titles(day),
            # 有记忆时用记忆块替代画像里的一行薄弱点；没有记忆时保持旧行为。
            learner_focus="" if not memory.is_empty else _focus_from_profile(profile),
            learner_memory=build_learner_memory_block(memory, MEMORY_TASK_ARTICLE),
            learner_plan=_plan_text(memory),
            within_level=hint.within_level,
            hint_reason=hint.reason,
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
        learner_focus: str = "",
        learner_memory: str = "",
        learner_plan: str = "",
        within_level: str = "",
        hint_reason: str = "",
        angle: str | None = None,
        avoid_titles: list[str] | tuple[str, ...] = (),
        user_request: str = "",
        current_article: Article | None = None,
    ) -> ArticleDraft:
        """调用 AI 生成文章；JSON 不合法或校验不过时带原因重试一次。

        strict_targets=False 表示目标词是“尽量带上”的软约束（阅读反馈重写用）。
        learner_feedback 会一并传给模型，用来控制生词密度与句子长度；
        learner_focus 是画像里的薄弱点，只作为软提示；
        learner_memory / learner_plan 是 M5.2 的学习者记忆与上周计划，
        不传时 prompt 与旧版本完全一致。
        angle=None 时随机选一个切入角度，传空字符串表示不加角度；
        avoid_titles 列出“别再写”的历史标题，保证每次生成（尤其“换一篇”）
        的 prompt 本身就不一样；user_request 是用户在弹窗里写的具体要求。
        current_article 仅在“换一篇 + 用户有要求”时传入，用于理解指代。
        """
        auto_angle = angle is None
        chosen_angle = self.pick_angle() if auto_angle else (angle or "").strip()
        last_reason = ""
        # 档内提示按“实际发出去的 prompt”记录：可读性重试要求更简单时，
        # 第二次必须撤掉 high，否则两条指令互相矛盾。
        normalized_within = within_level.strip().lower()
        # mid 不改变 prompt（builder 只认 low/high），但仍作为“档内中位”落库。
        effective_within = normalized_within if normalized_within in {"low", "mid", "high"} else ""
        effective_reason = hint_reason if effective_within else ""
        # 可读性重试时先留好第一篇合法文章：第二次生成失败（校验不过 / 网络错误）
        # 不能让用户当天没有文章。
        fallback: ArticleDraft | None = None
        for attempt in range(2):
            if attempt > 0 and auto_angle:
                # 重试时换一个切入角度：同一份 prompt 再发一次，模型多半还是给同样的结果。
                chosen_angle = self.pick_angle()
            messages = build_article_messages(
                level.strip().upper(),
                list(target_words),
                topic,
                within_level=effective_within,
                soft_targets=not strict_targets,
                learner_feedback=learner_feedback,
                learner_focus=learner_focus,
                learner_memory=learner_memory,
                learner_plan=learner_plan,
                angle=chosen_angle,
                avoid_titles=avoid_titles,
                user_request=user_request,
                current_article=current_article,
                repair_note=last_reason if attempt else "",
            )
            logger.info(
                "生成文章 prompt=%s level=%s attempt=%s topic=%s angle=%s "
                "target_words=%s soft=%s avoid=%s request=%s with_current=%s repair=%s",
                PROMPT_VERSION,
                level,
                attempt + 1,
                topic or "-",
                chosen_angle or "-",
                list(target_words),
                not strict_targets,
                len(avoid_titles),
                user_request.strip() or "-",
                current_article is not None,
                bool(last_reason),
            )
            try:
                text = self._client.complete_json(
                    messages,
                    temperature=0.8,
                    max_tokens=ARTICLE_MAX_TOKENS,
                    task="每日文章",
                )
            except AIError:
                if fallback is not None:
                    logger.warning("可读性重试失败，保留第一次生成的文章", exc_info=True)
                    return fallback
                raise
            try:
                draft = parse_article_draft(text, level, require_targets=strict_targets)
                draft = replace(
                    draft,
                    within_level=effective_within,
                    hint_reason=effective_reason,
                )
            except InvalidArticleError as exc:
                last_reason = str(exc)
                logger.warning("文章未通过校验（第 %s 次）：%s", attempt + 1, exc)
                continue

            metrics = self._analyze(draft)
            if metrics is not None:
                logger.info(
                    "文章可读性：词数=%s 生词=%s(%.1f%%) 平均句长=%.1f 目标词=%s/%s 词表=%s",
                    metrics.word_total,
                    metrics.unknown_count,
                    metrics.unknown_ratio * 100,
                    metrics.avg_sentence_len,
                    metrics.target_found,
                    metrics.target_total,
                    metrics.wordlist_size,
                )
            # 默认只记录不重试；开启 retry_enabled 后走现有 repair_note 通道
            # （复用本循环的第二次尝试，不新增调用类型）。
            if attempt == 0 and metrics is not None and self._readability.retry_enabled:
                warnings = threshold_warnings(metrics, self._thresholds_for(effective_within))
                if warnings:
                    fallback = replace(draft, metrics=metrics)
                    last_reason = (
                        "文章对当前水平偏难（"
                        + "；".join(warnings)
                        + "），请换更简单的词和更短的句子重写"
                    )
                    logger.info("文章可读性超出阈值（第 1 次）：%s", last_reason)
                    # 重试指令本身就是“更简单”：撤掉 high（与指令相反）；
                    # low 与指令同向，保留，metrics 继续记录真实来源。
                    if effective_within == "high":
                        effective_within = ""
                        effective_reason = ""
                    continue
            return replace(draft, metrics=metrics)
        if fallback is not None:
            logger.warning("可读性重试未通过校验，保留第一次生成的文章")
            return fallback
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

        memory = load_memory(self._memory_provider)
        hint = load_hint(self._hint_provider)
        draft = self.generate_article(
            profile.cefr_level,
            targets,
            topic,
            strict_targets=strict_targets,
            learner_focus="" if not memory.is_empty else _focus_from_profile(profile),
            learner_memory=build_learner_memory_block(memory, MEMORY_TASK_ARTICLE),
            learner_plan=_plan_text(memory),
            within_level=hint.within_level,
            hint_reason=hint.reason,
            angle=angle,
            # 用户写明了要求时，不再把当前标题列进“禁止重复”：
            # 他可能就是要一篇同主题/同风格的文章，不能让两条指令互相打架。
            avoid_titles=self._avoid_titles(
                day,
                current_title=existing.title if (existing and not request) else "",
            ),
            user_request=request,
            # 只有用户写了要求才把当前文章一起发过去：理解“这篇/这个话题”的指代。
            current_article=existing if request else None,
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
            metrics=self._metrics_payload(draft),
            source=draft.source,
            source_url=draft.source_url,
            source_ref=draft.source_ref,
            created_at=now_iso(),
        )
        # M7.2：替换掉真实文章时，无论调用方来自界面还是后台任务（甚至用户
        # 点了取消但请求已经写库），都在服务层记一次 overridden/skip。
        previous = self._articles.get_by_date(day)
        stored = self._articles.replace_by_date(article)
        self._seed_glossary(stored, draft)
        if (
            previous is not None
            and (previous.source or "") == "real"
            and draft.source != "real"
            and previous.source_ref
            and self._real_texts is not None
        ):
            try:
                self._real_texts.mark_overridden(previous.source_ref)
            except Exception:  # noqa: BLE001 - 标记失败不影响新文章
                logger.warning("标记真实文章已换回失败", exc_info=True)
        logger.info("文章已替换：%s《%s》%s 词", day, stored.title, stored.word_count)
        return stored

    def serve_real_text(self, text: RealText, today: str | None = None) -> Article | None:
        """把一篇真实文章写成当天文章 + 预置题目 + 供给历史（M7.1）。

        三步必须原子完成：任一步失败都删除当天刚写入的文章并返回 None，
        阅读页自然回退到 AI 生成，绝不留下“有文章没题目”的半截状态。
        """
        day = today or today_str()
        if self._articles.get_by_date(day) is not None:
            logger.info("当天已有文章，跳过真实文章供给：%s", day)
            return None
        if self._quiz_seeder is None or self._real_texts is None:
            logger.warning("真实文章供给缺少题目种子或历史仓库，跳过")
            return None

        paragraphs = tuple(part.strip() for part in str(text.content).split("\n\n") if part.strip())
        if not paragraphs:
            logger.warning("真实文章正文为空，跳过：%s", text.id)
            return None
        draft = ArticleDraft(
            title=text.title,
            level=text.level,
            topic=text.topics[0] if text.topics else "",
            paragraphs=paragraphs,
            source="real",
            source_url=text.source_url,
            source_ref=text.id,
        )
        draft = replace(draft, metrics=self._analyze(draft))

        try:
            article = self.replace_today(draft, today=day)
            self._quiz_seeder(article, text)
            self._real_texts.mark_served(text, served_on=day, article_id=article.id)
        except Exception:  # noqa: BLE001 - 供给失败必须回退 AI，不能留半截状态
            logger.exception("真实文章上架失败，回滚当天文章：%s", text.id)
            self._articles.delete_by_date(day)
            return None
        logger.info("真实文章已上架：id=%s level=%s words=%s", text.id, text.level, text.word_count)
        return article

    # -------------------------------------------------------------- 内部
    def _thresholds_for(self, within_level: str) -> ReadabilityThresholds:
        """按档内位置微调可读性阈值（M6.5）。

        只影响本次“是否触发重试”的判断，不改 `config.json` 的默认值：
        `low` 收紧约 20%/10%，`high` 放宽约 20%/10%。
        """
        base = self._readability
        if within_level == "low":
            return replace(
                base,
                max_unknown_ratio=base.max_unknown_ratio * 0.8,
                max_avg_sentence_len=max(5.0, base.max_avg_sentence_len * 0.9),
            )
        if within_level == "high":
            return replace(
                base,
                max_unknown_ratio=min(1.0, base.max_unknown_ratio * 1.2),
                max_avg_sentence_len=base.max_avg_sentence_len * 1.1,
            )
        return base

    @staticmethod
    def _metrics_payload(draft: ArticleDraft) -> dict[str, object] | None:
        """入库的 metrics JSON：可读性指标 + 档内位置（M6.5）。

        档内位置是附加键，`ReadabilityMetrics.from_dict()` 会忽略不认识的字键，
        旧读者 / 旧导出不受影响。
        """
        payload: dict[str, object] | None = (
            draft.metrics.to_dict() if draft.metrics is not None else None
        )
        if draft.within_level:
            payload = dict(payload or {})
            payload["within_level"] = draft.within_level
            payload["hint_reason"] = draft.hint_reason
        return payload

    def _analyze(self, draft: ArticleDraft) -> ReadabilityMetrics | None:
        """计算本地可读性指标；关闭或计算失败时返回 None，不影响文章入库。"""
        if not self._readability.enabled:
            return None
        try:
            known = (
                self._words.list_known_lemmas(KNOWN_INTERVAL_DAYS)
                if self._words is not None
                else ()
            )
            return analyze(
                draft.content,
                self._wordlist_provider(),
                known_words=known,
                target_words=draft.target_words,
            )
        except Exception:  # noqa: BLE001 - 指标是增强信息，失败不能拖垮生成
            logger.warning("计算文章可读性指标失败，按无指标处理", exc_info=True)
            return None

    def _store(self, day: str, draft: ArticleDraft) -> Article:
        article = Article(
            article_date=day,
            title=draft.title,
            content=draft.content,
            level=draft.level,
            topic=draft.topic,
            word_count=draft.word_count,
            target_words=draft.target_words,
            metrics=self._metrics_payload(draft),
            source=draft.source,
            source_url=draft.source_url,
            source_ref=draft.source_ref,
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
        self._seed_glossary(stored, draft)
        logger.info("文章已入库：%s《%s》%s 词", day, stored.title, stored.word_count)
        return stored

    def _seed_glossary(self, article: Article, draft: ArticleDraft) -> None:
        """把文章自带的难词释义写入查词缓存（`in_vocabulary=0`，不占生词本）。

        这是查词提速的第一层：生成文章时顺带产出的 glossary 已经包含学习者
        大概率会双击的词，双击时直接命中缓存，不再等 AI。
        缓存失败只记日志，不能影响文章生成结果。
        """
        if self._words is None or article.id is None or not draft.glossary:
            return
        timestamp = now_iso()
        seeded = 0
        try:
            for entry in draft.glossary:
                word = Word(
                    lemma=entry.base,
                    display_form=entry.word if entry.word != entry.base else "",
                    phonetic=entry.phonetic,
                    pos=entry.pos,
                    meaning_cn=entry.meaning_cn,
                    article_id=article.id,
                    status="new",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                stored = self._words.seed_cache(word)
                if stored is None or stored.id is None:
                    continue
                # 正文词形和原形都记住，下一次查同一词形直接命中。
                self._words.remember_form(entry.word, stored.id)
                self._words.remember_form(entry.base, stored.id)
                seeded += 1
        except Exception:  # noqa: BLE001 - 缓存是增强信息，失败不能拖垮生成
            logger.warning("写入文章难词缓存失败，忽略", exc_info=True)
            return
        logger.info("文章难词已写入查词缓存：%s 个（《%s》）", seeded, article.title)

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
    "GlossaryEntry",
    "InvalidArticleError",
    "parse_article_draft",
]
