"""划词释义：单词规范化、上下文句子提取、查词缓存与加入生词本。

设计（对应 HISTORY.md M1-5）：
- 规范化 lemma 后先查 `words` 表，命中就不再消耗 token；
- 未命中才调用 AI，要求“结合上下文给语境义”；
- 查词结果直接写入 `words`（既是生词本，也是查词缓存），`due_date = 今天`；
- 加入生词本 = 插入一条 `highlights` 记录 + `daily_stats.words_added += 1`，
  同一位置重复添加由 UNIQUE 约束保证幂等；第一次入本时把 `due_date` 改成
  明天，避免读文章加词后当天复习队列立刻膨胀。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

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
    build_prefetch_messages,
)
from .readability import KNOWN_INTERVAL_DAYS, default_wordlist, select_lookup_words

logger = logging.getLogger(__name__)

# 允许的单词形态：字母开头，可含内部连字符或撇号（如 well-known、don't）。
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")

# 后台预取：每篇文章最多预查多少个词、单次批量请求的输出额度。
# 数量刻意压在一屏阅读会点到的量级：太多会让预取请求本身变慢、也浪费 token。
PREFETCH_LIMIT = 10
PREFETCH_MAX_TOKENS = 1800
# 预取是“失败也无所谓”的后台增强：比业务默认的 300s 读超时短得多，
# 这样关闭窗口时不会为了一个用户根本没看到的请求干等五分钟。
# 刻意略大于 30s（客户端“超时后是否自动重试”的阈值）：预取超时就直接放弃，
# 不再自动重试两轮，避免把最长等待拖到一分半以上。
PREFETCH_TIMEOUT_SECONDS = 35.0
# 加入生词本后的后台补全：用户可见的结果在后台更新，但同样不能拖住关窗口。
# 45s 已覆盖常见完整查词（实测 15~20s），且大于 30s 的自动重试阈值——
# 超时直接放弃，保留简释，不再自动重试两轮。
ENRICH_TIMEOUT_SECONDS = 45.0

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


def needs_definition(word: Word) -> bool:
    """词条是否只有预生成简释（文章难词 / 后台预取），值得再补一次完整查词。

    完整查词的 prompt 必然要求例句与记忆方法（`build_dictionary_messages`），
    两者都为空说明这条来自 glossary / 预取这类“只要短释义”的批量生成。
    """
    return not (word.example_en or "").strip() and not (word.mnemonic or "").strip()


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

        word = self._request_definition(raw_word, sentence, level, article_id=article_id)
        stored = self._words.upsert(word)
        if stored.id is not None:
            # 记住“文章词形”和原形，下一次查同一词形不再调用 AI。
            self._words.remember_form(surface, stored.id)
            self._words.remember_form(word.lemma, stored.id)
        return LookupResult(word=stored, cached=False)

    def enrich_definition(
        self,
        word_id: int,
        sentence: str = "",
        level: str = "B1",
    ) -> Word | None:
        """把预生成简释补全成与双击查词一致的完整词条（后台调用）。

        M6.3：文章难词 / 后台预取只缓存了短释义（无例句、无记忆方法），
        用户把词加入生词本后，再按同一个查词 prompt 补一次完整释义，
        免得生词本里长期只有一行简释。只更新释义类字段：lemma、
        display_form、in_vocabulary 与 SRS 排期都不动。

        词条不存在、或已经有完整释义时直接返回 None（不花 token）。
        AI / 解析失败照常抛 `AIError`，由调用方（TaskWorker）静默处理。
        """
        word = self._words.get_by_id(int(word_id))
        if word is None or not needs_definition(word):
            return None

        # 用文章里的词形发请求，和用户双击时选中的文本一致。
        raw_word = word.display_form or word.lemma
        fresh = self._request_definition(
            raw_word,
            sentence,
            level,
            article_id=word.article_id,
            task="查词补全",
            timeout=ENRICH_TIMEOUT_SECONDS,
        )
        stored = self._words.update_definition(int(word_id), fresh)
        if stored is None:
            return None
        self._words.remember_form(raw_word, int(word_id))
        self._words.remember_form(stored.lemma, int(word_id))
        logger.info("查词补全完成：%s → %s", raw_word, stored.lemma)
        return stored

    def _request_definition(
        self,
        raw_word: str,
        sentence: str,
        level: str,
        *,
        article_id: int | None = None,
        task: str = "查词",
        timeout: float | None = None,
    ) -> Word:
        """调用一次完整查词并解析成 Word（不落库）。

        双击查词与“加入生词本后补全简释”共用这套 prompt 与字段，
        保证两个入口看到的释义一致。`task` 只影响 `ai_calls` 记录。
        """
        surface = normalize_lemma(raw_word)
        memory = load_memory(self._memory_provider)
        messages = build_dictionary_messages(
            raw_word.strip(),
            sentence.strip(),
            level,
            learner_memory=build_learner_memory_block(memory, MEMORY_TASK_DICTIONARY),
        )
        logger.info("查词调用 AI prompt=%s word=%s task=%s", PROMPT_VERSION, surface, task)
        if timeout is None:
            # 普通查词沿用客户端默认超时；也兼容只实现旧签名（没有 timeout）
            # 的 TextCompleter 测试替身。
            text = self._client.complete_json(messages, temperature=0.3, max_tokens=1500, task=task)
        else:
            text = self._client.complete_json(
                messages, temperature=0.3, max_tokens=1500, timeout=timeout, task=task
            )
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
        return Word(
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

    # ------------------------------------------------------------------ 预取
    def prefetch(
        self,
        text: str,
        *,
        level: str = "B1",
        article_id: int | None = None,
        limit: int = PREFETCH_LIMIT,
    ) -> int:
        """后台批量预查文章里可能陌生的词，写入查词缓存（不加入生词本）。

        设计（M6.1）：
        - 候选词完全由本地词表 + 用户已掌握词筛选，不调用 AI；
        - 一次批量请求查最多 `limit` 个词，返回后写入 `words`（`in_vocabulary=0`），
          用户双击时直接命中缓存，0 等待；
        - 任何失败（网络、解析、落库）都只记日志并返回 0：预取是尽力而为，
          不能影响阅读与正文展示。

        返回本次实际写入缓存的词条数。
        """
        content = text or ""
        if limit <= 0 or len(content.strip()) < 40:
            return 0

        try:
            candidates = self._prefetch_candidates(content, level=level, limit=limit)
        except Exception:  # noqa: BLE001 - 词表 / 词库异常不能拖垮阅读
            logger.warning("筛选预取候选词失败，跳过本次预取", exc_info=True)
            return 0
        if not candidates:
            logger.info("预取跳过：没有需要预查的新词")
            return 0

        entries = [(word, self._sentence_for(content, word)) for word in candidates]
        logger.info(
            "查词预取 prompt=%s level=%s words=%s",
            PROMPT_VERSION,
            level,
            candidates,
        )
        try:
            reply = self._client.complete_json(
                build_prefetch_messages(entries, level),
                temperature=0.2,
                max_tokens=PREFETCH_MAX_TOKENS,
                timeout=PREFETCH_TIMEOUT_SECONDS,
                task="查词预取",
            )
        except AIError as exc:
            # 预取失败对用户不可见：他仍然可以双击后走正常查词。
            logger.info("查词预取失败，忽略：%s", exc.message)
            return 0
        except Exception:  # noqa: BLE001
            logger.warning("查词预取异常，忽略", exc_info=True)
            return 0

        try:
            data = parse_json_object(reply)
        except JsonParseError as exc:
            logger.info("查词预取结果无法解析，忽略：%s", exc)
            return 0
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            return 0

        wanted = {word.lower() for word in candidates}
        sentences = {word.lower(): sentence for word, sentence in entries}
        timestamp = now_iso()
        count = 0
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            surface = normalize_lemma(as_text(item.get("word")))
            if not surface or surface not in wanted:
                continue
            meaning_cn = as_text(item.get("meaning_cn"))
            meaning_en = as_text(item.get("meaning_en"))
            if not meaning_cn and not meaning_en:
                continue
            lemma = normalize_lemma(as_text(item.get("lemma"))) or surface
            word = Word(
                lemma=lemma,
                display_form=surface if surface != lemma else "",
                phonetic=as_text(item.get("phonetic"))[:60],
                pos=as_text(item.get("pos"))[:20],
                meaning_cn=meaning_cn,
                meaning_en=meaning_en,
                context_sentence=(sentences.get(surface) or "")[:500],
                article_id=article_id,
                status="new",
                created_at=timestamp,
                updated_at=timestamp,
            )
            try:
                stored = self._words.seed_cache(word)
            except Exception:  # noqa: BLE001 - 单个词失败不影响其它词
                logger.warning("写入预取缓存失败：%s", surface, exc_info=True)
                continue
            if stored is None or stored.id is None:
                continue
            self._words.remember_form(surface, stored.id)
            self._words.remember_form(lemma, stored.id)
            count += 1

        logger.info("查词预取完成：候选 %s 个，写入缓存 %s 个", len(candidates), count)
        return count

    def _prefetch_candidates(self, content: str, *, level: str, limit: int) -> list[str]:
        """本地筛选候选词：已掌握 / 已缓存的词全部排除。"""
        known = set(self._words.list_known_lemmas(KNOWN_INTERVAL_DAYS))
        cached = self._words.cached_forms()
        return list(
            select_lookup_words(
                content,
                default_wordlist(),
                known_words=known | cached,
                level=level,
                limit=limit,
            )
        )

    @staticmethod
    def _sentence_for(content: str, word: str) -> str:
        """取单词在正文中第一次出现时的所在句，作为预取释义的上下文。"""
        match = re.search(rf"\b{re.escape(word)}\b", content, re.I)
        if match is None:
            return ""
        return extract_sentence(content, match.start(), match.end())

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
        # 首次复习放到明天：今天读文章加的 10 个词不会立刻挤进今天的复习队列。
        first_due = (date.fromisoformat(self._today()) + timedelta(days=1)).isoformat()
        if self._words.mark_in_vocabulary(word.id, first_due_date=first_due) is None:
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
