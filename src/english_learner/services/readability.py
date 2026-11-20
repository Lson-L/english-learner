"""本地可读性指标（M5.3）：用静态词表估算“这篇文章对当前水平难不难”。

设计（AI_TASKS.md M5.3）：
- **纯函数、无 AI、无网络**：给定词表与文本就能算，结果可复现；
- 词表是开发期一次性生成的静态文件 `assets/wordlists/ai_frequency.json`
  （AI 输出常用原形 + CEFR 档），运行时只读，不调用任何模型；
- “已知词” = 词表里的词 + 用户已掌握的词（`in_vocabulary = 1` 或
  `interval_days >= KNOWN_INTERVAL_DAYS`）；
- 指标只用于记录 / 告警 / 展示，**不参与改等级**，也不自己决定重试——
  是否重试由调用方按 `ReadabilityThresholds.retry_enabled` 决定；
- 词表缺失或过小时按“宽容模式”处理：宁可不重试，也不误伤正常文章。

Java 对照：≈ 一个 `ReadabilityAnalyzer` 工具类，输入纯文本 + 词典，
输出一个不可变 DTO；没有任何依赖注入，测试里给个小词表就能跑。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 词表与 gui / services 同级，都在包内；PyInstaller 的 datas 已整目录打包。
WORDLIST_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "wordlists" / "ai_frequency.json"
)

# 用户词条间隔达到这个天数，视为“已掌握”，算作已知词（与学习者记忆口径一致）。
KNOWN_INTERVAL_DAYS = 30

# 默认阈值：生词比例与平均句长。超过只告警 / 展示，默认不强制重试。
DEFAULT_MAX_UNKNOWN_RATIO = 0.07
DEFAULT_MAX_AVG_SENTENCE_LEN = 22.0

# 词表小于这个规模时说明文件残缺，阈值放宽 1.5 倍，避免误伤。
RELIABLE_WORDLIST_SIZE = 500
TOLERANCE_FACTOR = 1.5
# 太短的文本统计噪声大，不参与阈值判断。
MIN_WORDS_FOR_WARNING = 40

_TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
# 句长只是启发式口径：Mr. / e.g. / 3.5 也会被当成句子边界，平均句长会略偏低；
# 该指标只用于区间告警与展示，不追求语言学精确。
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")
_CONTRACTION_SUFFIXES = (
    "'re",
    "'ve",
    "'ll",
    "'d",
    "'m",
    "'s",
    "’re",
    "’ve",
    "’ll",
    "’d",
    "’m",
    "’s",
)
# 少数缩写去掉 n't 后不是原形，单独映射（can't → ca 是错误结果）。
_NT_EXCEPTIONS = {"can't": "can", "won't": "will", "shan't": "shall"}

# 不规则屈折 / 比较级 / 复数 → 原形。词表只收原形，靠后缀规则覆盖不了这些
# 高频形式；不映射的话 is / was / has / said 会被当成生词，把生词比例整体抬高。
_IRREGULAR_FORMS: dict[str, tuple[str, ...]] = {
    "am": ("be",),
    "is": ("be",),
    "are": ("be",),
    "was": ("be",),
    "were": ("be",),
    "been": ("be",),
    "being": ("be",),
    "has": ("have",),
    "had": ("have",),
    "having": ("have",),
    "does": ("do",),
    "did": ("do",),
    "done": ("do",),
    "doing": ("do",),
    "said": ("say",),
    "says": ("say",),
    "made": ("make",),
    "went": ("go",),
    "gone": ("go",),
    "goes": ("go",),
    "could": ("can",),
    "might": ("may",),
    "got": ("get",),
    "gotten": ("get",),
    "took": ("take",),
    "taken": ("take",),
    "came": ("come",),
    "saw": ("see",),
    "seen": ("see",),
    "gave": ("give",),
    "given": ("give",),
    "found": ("find",),
    "told": ("tell",),
    "became": ("become",),
    "left": ("leave",),
    "felt": ("feel",),
    "brought": ("bring",),
    "began": ("begin",),
    "begun": ("begin",),
    "kept": ("keep",),
    "held": ("hold",),
    "wrote": ("write",),
    "written": ("write",),
    "stood": ("stand",),
    "heard": ("hear",),
    "meant": ("mean",),
    "met": ("meet",),
    "ran": ("run",),
    "paid": ("pay",),
    "sat": ("sit",),
    "spoke": ("speak",),
    "spoken": ("speak",),
    "led": ("lead",),
    "grew": ("grow",),
    "grown": ("grow",),
    "lost": ("lose",),
    "fell": ("fall",),
    "fallen": ("fall",),
    "sent": ("send",),
    "built": ("build",),
    "understood": ("understand",),
    "drew": ("draw",),
    "drawn": ("draw",),
    "broke": ("break",),
    "broken": ("break",),
    "spent": ("spend",),
    "rose": ("rise",),
    "risen": ("rise",),
    "drove": ("drive",),
    "driven": ("drive",),
    "bought": ("buy",),
    "wore": ("wear",),
    "worn": ("wear",),
    "chose": ("choose",),
    "chosen": ("choose",),
    "ate": ("eat",),
    "eaten": ("eat",),
    "drank": ("drink",),
    "drunk": ("drink",),
    "sang": ("sing",),
    "sung": ("sing",),
    "swam": ("swim",),
    "swum": ("swim",),
    "flew": ("fly",),
    "flown": ("fly",),
    "threw": ("throw",),
    "thrown": ("throw",),
    "caught": ("catch",),
    "taught": ("teach",),
    "fought": ("fight",),
    "sold": ("sell",),
    "won": ("win",),
    "slept": ("sleep",),
    "woke": ("wake",),
    "woken": ("wake",),
    "forgot": ("forget",),
    "forgotten": ("forget",),
    "forgave": ("forgive",),
    "forgiven": ("forgive",),
    "children": ("child",),
    "men": ("man",),
    "women": ("woman",),
    "feet": ("foot",),
    "teeth": ("tooth",),
    "mice": ("mouse",),
    "better": ("good",),
    "best": ("good",),
    "worse": ("bad",),
    "worst": ("bad",),
    "further": ("far",),
    "farther": ("far",),
    "whose": ("who",),
    "laid": ("lay",),
    "fed": ("feed",),
    "hung": ("hang",),
    "stuck": ("stick",),
    "shook": ("shake",),
    "shaken": ("shake",),
    "stole": ("steal",),
    "stolen": ("steal",),
    "hid": ("hide",),
    "hidden": ("hide",),
    "rode": ("ride",),
    "ridden": ("ride",),
    "rang": ("ring",),
    "rung": ("ring",),
    "sank": ("sink",),
    "sunk": ("sink",),
    "dug": ("dig",),
    "bent": ("bend",),
    "swept": ("sweep",),
    "dealt": ("deal",),
    "learnt": ("learn",),
    "burnt": ("burn",),
    "dreamt": ("dream",),
    "lent": ("lend",),
    "knew": ("know",),
    "known": ("know",),
    "blew": ("blow",),
    "blown": ("blow",),
    "bit": ("bite",),
    "bitten": ("bite",),
    "froze": ("freeze",),
    "frozen": ("freeze",),
    "shone": ("shine",),
    "shot": ("shoot",),
    "shown": ("show",),
    "struck": ("strike",),
    "tore": ("tear",),
    "torn": ("tear",),
    "beaten": ("beat",),
    "bore": ("bear",),
    "born": ("bear",),
}


def _normalize_token(token: str) -> str:
    """把文中词形转成可查词表的原形候选：小写、去掉所有格与缩写后缀。

    只做“够用”的归一化（不引入词干库）：`don't → do`、`it's → it`、
    `we'll → we`；真正的屈折还原在 `_candidates()` 里按后缀尝试。
    """
    value = token.lower().replace("’", "'")
    special = _NT_EXCEPTIONS.get(value)
    if special is not None:
        return special
    if value.endswith("n't") and len(value) > 3:
        return value[:-3]
    for suffix in _CONTRACTION_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def tokenize(text: str) -> list[str]:
    """切成小写单词序列；数字、标点、纯符号都不算词。"""
    return [_normalize_token(match.group(0)) for match in _TOKEN_RE.finditer(text or "")]


def _candidates(word: str) -> Iterator[str]:
    """产出可能的原形：先给原词，再按常见屈折后缀还原。

    还原宁可多给几个候选，也不追求语言学精确 —— 多认一个已知词只会让
    生词比例偏低（不误报难文），比把 `habits` 当生词更符合本模块的用途。
    """
    yield word
    yield from _IRREGULAR_FORMS.get(word, ())
    length = len(word)
    if length > 3 and word.endswith("s") and not word.endswith("ss"):
        yield word[:-1]  # habits → habit
        if word.endswith("ies"):
            yield word[:-3] + "y"  # studies → study
        elif word.endswith("es"):
            yield word[:-2]  # boxes → box
    if length > 3 and word.endswith("ed"):
        yield word[:-2]  # walked → walk
        yield word[:-1]  # used → use
        if word.endswith("ied"):
            yield word[:-3] + "y"  # tried → try
        if length > 4 and word[-3] == word[-4]:
            yield word[:-3]  # stopped → stop
    if length > 4 and word.endswith("ing"):
        yield word[:-3]  # walking → walk
        yield word[:-3] + "e"  # making → make
        if word[-4] == word[-5]:
            yield word[:-4]  # running → run
    if length > 4 and word.endswith("ly"):
        yield word[:-2]  # quickly → quick
    if length > 4 and word.endswith("er"):
        yield word[:-2]  # smaller → small
        yield word[:-1]  # nicer → nice
        if word[-3] == word[-4]:
            yield word[:-3]  # bigger → big
    if length > 5 and word.endswith("est"):
        yield word[:-3]  # smallest → small
        yield word[:-2]  # nicest → nice
        if word[-4] == word[-5]:
            yield word[:-4]  # biggest → big


def _is_known(word: str, wordlist: dict[str, str], known_words: set[str]) -> bool:
    return any(candidate in wordlist or candidate in known_words for candidate in _candidates(word))


# 词表里保留了 A1（比 A2 更基础）；A1 对任何目标等级都算已知。
_BAND_RANK = {"A1": 0, "A2": 1, "B1": 2, "B2": 3, "C1": 4}
# 词表里没有的词视为“超出常见范围”，预取优先级最高。
_UNLISTED_RANK = 99
_RAW_TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


def _at_sentence_start(text: str, start: int) -> bool:
    """判断位置 start 是否处在句首（句号/问号/叹号/换行之后或全文开头）。"""
    index = start - 1
    while index >= 0 and text[index] in " \t\r\"'“‘([":
        index -= 1
    return index < 0 or text[index] in ".!?\n"


def select_lookup_words(
    text: str,
    wordlist: dict[str, str],
    *,
    known_words: Iterable[str] = (),
    level: str = "B1",
    limit: int = 10,
) -> tuple[str, ...]:
    """挑出“对当前等级可能陌生、值得预查”的词（M6 预取查词用）。

    口径与 `analyze()` 的“生词”不同：这里把“词表里存在、但词档高于用户等级”
    也算候选（B1 用户读到 C1 词一样会卡住）；词表里没有的词优先级最高。
    过滤规则：
    - 用户已掌握的词（生词本 / 复习间隔达标）直接排除；
    - 只出现在句中的大写词视为专有名词跳过，句首大写不算；
    - 同一词形按出现次数排序，次数相同按首次出现位置。
    纯函数，无网络、无数据库，便于单测。
    """
    if limit <= 0 or not (text or "").strip():
        return ()

    known = {str(item).strip().lower() for item in known_words if str(item).strip()}
    level_rank = _BAND_RANK.get(level.strip().upper(), _BAND_RANK["B1"])
    matches = list(_RAW_TOKEN_RE.finditer(text))
    # 同一个词只要出现过小写形态，就说明它不是专有名词。
    lowercase_forms = {
        match.group(0).lower().replace("’", "'") for match in matches if match.group(0)[0].islower()
    }

    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    ranks: dict[str, int] = {}
    for match in matches:
        raw = match.group(0)
        surface = raw.lower().replace("’", "'")
        if len(surface) < 3:
            continue
        if raw[0].isupper() and surface not in lowercase_forms:
            if not _at_sentence_start(text, match.start()):
                continue  # 句中大写：专有名词
        candidates = list(_candidates(_normalize_token(raw)))
        if any(candidate in known for candidate in candidates):
            continue
        band = next((wordlist[c] for c in candidates if c in wordlist), "")
        listed = bool(band) or any(candidate in wordlist for candidate in candidates)
        if listed:
            # 词表里且有词档：不超过用户等级就算已知；词档缺失时保守跳过；
            # 出现未收录的更高词档（如 C2）时按“超出常见范围”处理。
            if not band:
                continue
            band_rank = _BAND_RANK.get(band)
            if band_rank is not None and band_rank <= level_rank:
                continue
        counts[surface] = counts.get(surface, 0) + 1
        first_seen.setdefault(surface, match.start())
        ranks[surface] = _BAND_RANK.get(band, _UNLISTED_RANK)

    ordered = sorted(
        counts,
        key=lambda word: (-ranks[word], -counts[word], first_seen[word]),
    )
    return tuple(ordered[:limit])


@dataclass(frozen=True)
class ReadabilityMetrics:
    """一篇文章的可读性快照；字段全部由本地计算得出。"""

    word_total: int = 0
    unknown_count: int = 0
    avg_sentence_len: float = 0.0
    max_sentence_len: int = 0
    target_total: int = 0
    target_found: int = 0
    # 参与计算的词表规模：0 表示词表缺失，指标不可信，展示与告警都要跳过。
    wordlist_size: int = 0

    @property
    def unknown_ratio(self) -> float:
        return self.unknown_count / self.word_total if self.word_total else 0.0

    @property
    def target_coverage(self) -> float:
        return self.target_found / self.target_total if self.target_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        """转成可入库的 JSON 结构；浮点保留固定精度，方便跨次对比。"""
        return {
            "word_total": self.word_total,
            "unknown_count": self.unknown_count,
            "unknown_ratio": round(self.unknown_ratio, 4),
            "avg_sentence_len": round(self.avg_sentence_len, 1),
            "max_sentence_len": self.max_sentence_len,
            "target_total": self.target_total,
            "target_found": self.target_found,
            "wordlist_size": self.wordlist_size,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ReadabilityMetrics | None:
        """从库里的 JSON 对象还原；旧文章没有指标时返回 None。"""
        if not isinstance(data, dict) or not data:
            return None
        try:
            return cls(
                word_total=int(data.get("word_total") or 0),
                unknown_count=int(data.get("unknown_count") or 0),
                avg_sentence_len=float(data.get("avg_sentence_len") or 0.0),
                max_sentence_len=int(data.get("max_sentence_len") or 0),
                target_total=int(data.get("target_total") or 0),
                target_found=int(data.get("target_found") or 0),
                wordlist_size=int(data.get("wordlist_size") or 0),
            )
        except (TypeError, ValueError):
            return None


def analyze(
    text: str,
    wordlist: dict[str, str],
    *,
    known_words: Iterable[str] = (),
    target_words: Iterable[str] = (),
) -> ReadabilityMetrics:
    """计算一篇正文的指标。

    `known_words` 是用户已掌握的原形（来自生词本 / 复习间隔），
    `target_words` 是今天要求覆盖的目标词。
    """
    known = {str(item).strip().lower() for item in known_words if str(item).strip()}
    targets = [str(item).strip().lower() for item in target_words if str(item).strip()]

    tokens = tokenize(text)
    unknown = sum(1 for token in tokens if not _is_known(token, wordlist, known))

    sentence_lengths: list[int] = []
    for chunk in _SENTENCE_SPLIT_RE.split(text or ""):
        length = len(tokenize(chunk))
        if length:
            sentence_lengths.append(length)

    # 目标词覆盖是近似口径：按原形候选匹配，接受屈折与不规则形式；
    # 同形异义（left / rose / saw）会略微高估，派生词（resilience vs resilient）
    # 会略微低估。它只用于记录/导出，不参与阈值告警，不值得为此引入词性分析。
    found = 0
    for target in targets:
        if any(target in _candidates(token) for token in tokens):
            found += 1

    return ReadabilityMetrics(
        word_total=len(tokens),
        unknown_count=unknown,
        avg_sentence_len=(sum(sentence_lengths) / len(sentence_lengths))
        if sentence_lengths
        else 0.0,
        max_sentence_len=max(sentence_lengths, default=0),
        target_total=len(targets),
        target_found=found,
        wordlist_size=len(wordlist),
    )


@dataclass(frozen=True)
class ReadabilityThresholds:
    """可读性阈值（config.json 的 `readability` 段，缺省即默认值）。"""

    enabled: bool = True
    # 先观察一周再决定是否强制重试（M5.3 边界），默认关闭。
    retry_enabled: bool = False
    max_unknown_ratio: float = DEFAULT_MAX_UNKNOWN_RATIO
    max_avg_sentence_len: float = DEFAULT_MAX_AVG_SENTENCE_LEN

    @classmethod
    def from_dict(cls, data: Any) -> ReadabilityThresholds:
        """宽松解析：任何非法值都回退默认，坏配置不能把文章生成搞挂。"""
        if not isinstance(data, dict):
            return cls()

        def _bool(key: str, default: bool) -> bool:
            """只接受真正的 bool 或常见字符串写法；"false" 不能被当成 True。"""
            value = data.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"true", "1", "yes", "on"}:
                    return True
                if text in {"false", "0", "no", "off"}:
                    return False
            return default

        def _number(key: str, default: float, low: float, high: float) -> float:
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            return min(max(value, low), high)

        return cls(
            enabled=_bool("enabled", True),
            retry_enabled=_bool("retry_enabled", False),
            max_unknown_ratio=_number("max_unknown_ratio", DEFAULT_MAX_UNKNOWN_RATIO, 0.0, 1.0),
            max_avg_sentence_len=_number(
                "max_avg_sentence_len", DEFAULT_MAX_AVG_SENTENCE_LEN, 5.0, 200.0
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "retry_enabled": self.retry_enabled,
            "max_unknown_ratio": self.max_unknown_ratio,
            "max_avg_sentence_len": self.max_avg_sentence_len,
        }


def threshold_warnings(
    metrics: ReadabilityMetrics,
    thresholds: ReadabilityThresholds,
) -> tuple[str, ...]:
    """返回超出阈值的说明；空元组表示“在可接受区间内”。

    词表缺失 / 过小、正文太短时一律不告警：宁可漏报，也不误伤正常文章。
    """
    if not thresholds.enabled or metrics.wordlist_size == 0:
        return ()
    if metrics.word_total < MIN_WORDS_FOR_WARNING:
        return ()

    tolerance = 1.0 if metrics.wordlist_size >= RELIABLE_WORDLIST_SIZE else TOLERANCE_FACTOR
    warnings: list[str] = []
    if metrics.unknown_ratio > thresholds.max_unknown_ratio * tolerance:
        warnings.append(f"生词比例 {metrics.unknown_ratio:.0%} 超过上限")
    if metrics.avg_sentence_len > thresholds.max_avg_sentence_len * tolerance:
        warnings.append(f"平均句长 {metrics.avg_sentence_len:.1f} 词超过上限")
    return tuple(warnings)


_wordlist_cache: dict[str, dict[str, str]] = {}


def load_wordlist(path: Path | None = None) -> dict[str, str]:
    """读取静态词表 `{word: band}`；文件缺失或损坏返回空 dict。

    读取失败只记 warning：词表是增强信息，不能阻断文章生成。
    结果按路径缓存，多次调用只读一次文件。
    """
    target = Path(path) if path is not None else WORDLIST_PATH
    key = str(target)
    cached = _wordlist_cache.get(key)
    if cached is not None:
        return cached

    words: dict[str, str] = {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("词表文件不存在，可读性指标进入宽容模式：%s", target)
        data = None
    except (OSError, ValueError) as exc:
        logger.warning("词表文件读取失败，可读性指标进入宽容模式：%s（%s）", target, exc)
        data = None

    if isinstance(data, dict):
        raw = data.get("words")
        if isinstance(raw, dict):
            words = {
                str(word).strip().lower(): str(band).strip().upper()
                for word, band in raw.items()
                if str(word).strip()
            }
        elif data:
            # 兼容“直接就是 {word: band}”的简化格式。
            words = {
                str(word).strip().lower(): str(band).strip().upper()
                for word, band in data.items()
                if str(word).strip()
            }
    if not words:
        logger.warning("词表内容为空，可读性指标进入宽容模式：%s", target)

    _wordlist_cache[key] = words
    return words


def default_wordlist() -> dict[str, str]:
    """运行时使用的内置词表。"""
    return load_wordlist()


__all__ = [
    "DEFAULT_MAX_AVG_SENTENCE_LEN",
    "DEFAULT_MAX_UNKNOWN_RATIO",
    "KNOWN_INTERVAL_DAYS",
    "ReadabilityMetrics",
    "ReadabilityThresholds",
    "analyze",
    "default_wordlist",
    "load_wordlist",
    "select_lookup_words",
    "threshold_warnings",
    "tokenize",
]
