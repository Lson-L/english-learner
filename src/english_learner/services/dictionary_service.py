"""划词释义：单词规范化、上下文句子提取、查词缓存与加入生词本。

设计（对应 HISTORY.md M1-5）：
- 规范化 lemma 后先查 `words` 表，命中就不再消耗 token；
- 未命中才调用 AI，要求“结合上下文给语境义”；
- 查词结果直接写入 `words`（既是生词本，也是查词缓存），`due_date = 今天`，
  M2 的复习从今天开始排队；
- 加入生词本 = 插入一条 `highlights` 记录 + `daily_stats.words_added += 1`，
  同一位置重复添加由 UNIQUE 约束保证幂等。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from ..models.entities import (
    Word,
    WordDefinition,
    normalize_cefr_level,
    now_iso,
    today_str,
)
from ..storage.repositories import StatsRepo, WordRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .json_utils import JsonParseError, as_text, parse_json_object
from .learner_memory import LearnerMemory, load_memory
from .prompts import (
    MEMORY_TASK_DICTIONARY,
    PROMPT_VERSION,
    build_dictionary_messages,
    build_learner_memory_block,
    build_mnemonic_messages,
)

logger = logging.getLogger(__name__)

# 允许的单词形态：字母开头，可含内部连字符或撇号（如 well-known、don't）。
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")

# 句子边界：英文句末标点 + 换行。分号、逗号不作为边界，避免把句子切得太碎。
_SENTENCE_DELIMITERS = ".!?\n"


def normalize_lemma(raw: str) -> str:
    """把双击选中的文本规范成查询键：去首尾标点、转小写。

    只接受“长得像英文单词”的输入；数字、中文、整句都返回空串，
    由界面提示“请选择一个英文单词”，不浪费一次 AI 调用。
    """
    value = (raw or "").strip().strip("\"'“”‘’()[]{}<>.,;:!?…—–-").strip().lower()
    if not value or not _WORD_RE.match(value):
        return ""
    return value


def extract_sentence(text: str, start: int, end: int, max_len: int = 300) -> str:
    """取 start~end 这段文字所在的句子，向左右找最近的句末标点。"""
    if not text:
        return ""
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))

    left = start
    while left > 0 and text[left - 1] not in _SENTENCE_DELIMITERS:
        left -= 1
    right = end
    while right < len(text) and text[right] not in _SENTENCE_DELIMITERS:
        right += 1
    if right < len(text) and text[right] in ".!?":
        right += 1  # 把句号本身也收进来

    if right - left > max_len:
        center = (start + end) // 2
        left = max(left, center - max_len // 2)
        right = min(right, left + max_len)
        left = max(0, right - max_len)

    return " ".join(text[left:right].split())


def _context_mismatch(cached_sentence: str, current_sentence: str) -> bool:
    """缓存的释义是否来自另一句上下文（只做归一化后的精确比较）。"""
    cached = " ".join((cached_sentence or "").split()).lower()
    current = " ".join((current_sentence or "").split()).lower()
    return bool(cached and current and cached != current)


@dataclass(frozen=True)
class LookupResult:
    """一次查词的结果：词条本身 + 是否命中缓存。"""

    word: Word
    cached: bool
    # 命中缓存、但缓存释义来自另一句：界面可以提示“按本句重查”。
    context_mismatch: bool = False

    @property
    def definition(self) -> WordDefinition:
        return WordDefinition.from_word(self.word)


class DictionaryService:
    """查词 + 加入生词本。同步阻塞，必须放在 TaskWorker 里调用。"""

    def __init__(
        self,
        client: TextCompleter,
        word_repo: WordRepo,
        stats_repo: StatsRepo | None = None,
        today: Callable[[], str] = today_str,
        *,
        memory_provider: Callable[[], LearnerMemory] | None = None,
    ) -> None:
        self._client = client
        self._words = word_repo
        self._stats = stats_repo
        self._today = today
        self._memory_provider = memory_provider

    # ------------------------------------------------------------------ 查词
    def lookup(
        self,
        raw_word: str,
        sentence: str = "",
        article_id: int | None = None,
        level: str = "B1",
        *,
        refresh: bool = False,
    ) -> LookupResult:
        """查词；`lemma` 是词典原形，`display_form` 保留文章中的词形。

        缓存顺序：
        1. word_forms 里有这个“文章词形” → 直接命中（went → go）；
        2. words 里正好有同名词条 → 命中；
        3. 都没命中才调用 AI，由 AI 判断原形并生成记忆方法。

        `refresh=True` 跳过缓存，按当前句子重新请求 AI 并更新词条释义。
        界面在“缓存释义来自另一句”时提供这个入口，避免多义词一直显示旧语境义。
        """
        surface = normalize_lemma(raw_word)
        if not surface:
            raise AIError("请选择一个英文单词", "只能查询由字母组成的单词，双击正文即可选中。")

        cached: Word | None = None
        if not refresh:
            cached = self._words.get_by_form(surface)
            if cached is None:
                cached = self._words.get_by_lemma(surface)
            if cached is not None:
                logger.info("查词命中缓存：%s → %s", surface, cached.lemma)
                return LookupResult(
                    word=cached,
                    cached=True,
                    context_mismatch=_context_mismatch(cached.context_sentence, sentence),
                )

        memory = load_memory(self._memory_provider)
        messages = build_dictionary_messages(
            raw_word.strip(),
            sentence.strip(),
            level,
            learner_memory=build_learner_memory_block(memory, MEMORY_TASK_DICTIONARY),
        )
        logger.info("查词调用 AI prompt=%s word=%s", PROMPT_VERSION, surface)
        text = self._client.complete_json(messages, temperature=0.3, max_tokens=1500, task="查词")
        try:
            data = parse_json_object(text)
        except JsonParseError as exc:
            logger.warning("查词 JSON 解析失败：%s", exc)
            raise AIError(
                "查词结果无法解析",
                "模型可能没有按 JSON 格式返回，可以重试或换一个模型。",
            ) from exc

        meaning_cn = as_text(data.get("meaning_cn"))
        meaning_en = as_text(data.get("meaning_en"))
        if not meaning_cn and not meaning_en:
            raise AIError("查词结果缺少释义", "AI 没有返回 meaning_cn / meaning_en，请重试。")

        # AI 给的原形可能缺失或带标点；非法时退回当前词形。
        lemma = normalize_lemma(as_text(data.get("lemma"))) or surface
        display_form = (
            raw_word.strip().strip("\"'“”‘’()[]{}<>.,;:!?…—–-")
            or as_text(data.get("display_form"))
            or lemma
        )

        timestamp = now_iso()
        word = Word(
            lemma=lemma,
            display_form=display_form,
            phonetic=as_text(data.get("phonetic")),
            pos=as_text(data.get("pos")),
            meaning_cn=meaning_cn,
            meaning_en=meaning_en,
            example_en=as_text(data.get("example_en")),
            example_cn=as_text(data.get("example_cn")),
            mnemonic=as_text(data.get("mnemonic")),
            cefr_band=normalize_cefr_level(data.get("cefr_band")),
            context_sentence=sentence.strip()[:500],
            article_id=article_id,
            status="new",
            due_date=self._today(),
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = self._words.upsert(word)
        if stored.id is not None:
            # 记住“文章词形”和原形，下一次查同一词形不再调用 AI。
            self._words.remember_form(surface, stored.id)
            self._words.remember_form(lemma, stored.id)
        return LookupResult(word=stored, cached=False)

    def generate_mnemonic(self, word: Word, level: str = "B1") -> Word:
        """给已有生词单独生成 / 更新记忆方法（老词条或用户手动触发）。"""
        if word.id is None:
            raise ValueError("generate_mnemonic() 需要已落库的词条")

        messages = build_mnemonic_messages(word, level)
        logger.info("生成记忆方法 prompt=%s word=%s", PROMPT_VERSION, word.lemma)
        text = self._client.complete_json(
            messages, temperature=0.5, max_tokens=800, task="记忆方法"
        )
        try:
            data = parse_json_object(text)
        except JsonParseError as exc:
            raise AIError(
                "记忆方法无法解析",
                "模型可能没有按 JSON 格式返回，可以重试或换一个模型。",
            ) from exc

        mnemonic = as_text(data.get("mnemonic"))
        if not mnemonic:
            raise AIError("AI 没有返回记忆方法", "请重试，或换一个模型。")

        updated = self._words.update_mnemonic(word.id, mnemonic[:500])
        if updated is None:
            raise AIError("词条已不存在", "请刷新生词本后重试。")
        return updated

    # -------------------------------------------------------------- 生词本
    def add_to_vocabulary(
        self,
        word: Word,
        *,
        article_id: int | None,
        start_offset: int,
        end_offset: int,
        sentence: str = "",
    ) -> bool:
        """把词条与某个正文位置绑定；重复添加返回 False 且不重复计数。"""
        if word.id is None:
            raise ValueError("word.id 为空：请先调用 lookup() 再入生词本")
        if article_id is None or end_offset <= start_offset:
            raise ValueError("文章或偏移量不合法")

        # 查词只写缓存；只有走到这里（用户点“加入生词本”）才真正入本。
        if self._words.mark_in_vocabulary(word.id) is None:
            raise ValueError("词条不存在，请重新查词")

        inserted = self._words.add_highlight(
            article_id=article_id,
            word_id=word.id,
            start_offset=start_offset,
            end_offset=end_offset,
            sentence=sentence,
        )
        if inserted and self._stats is not None:
            self._stats.bump(self._today(), words_added=1)
        if not inserted:
            logger.info("重复添加同一位置，已忽略：word_id=%s", word.id)
        return inserted
