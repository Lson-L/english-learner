"""一次性生成 M5.3 的本地词表（开发期脚本，不进入运行时）。

产出：`src/english_learner/assets/wordlists/ai_frequency.json`

当前入库的词表（3212 词，A1~C1）由 AI 在开发期一次性生成并人工抽查，
生成时网关对长输出会返回 `finish_reason=length`，所以 A1 档没有走通本脚本；
本脚本保留为将来换网关 / 换模型时的复现手段，**不要**在应用运行时调用。

做法：
- 按 CEFR 档 × 首字母区间分批让 AI 输出 `word,band` 行，避免一次输出过大被截断；
- 每批结果缓存在 `verification/wordlist_raw/`（已在 .gitignore 内），重跑不重复花 token；
- 合并时按 A1 → C1 优先级去重（同一个词保留最低档），只接受纯字母 / 连字符；
- 合并后强制补齐 `CORE_WORDS`：AI 只收原形，容易漏掉 to / is / 说 这类功能词，
  漏掉会让生词比例整体虚高（M5.3 验收 P1），所以这里做确定性兜底；
- 输出按字母排序，保证多次生成可直接 diff。

用法（项目根目录）：

    uv run python scripts/generate_wordlist.py
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from english_learner.services.ai_client import AIClient, AIError
from english_learner.services.config_store import ConfigStore

# 开发脚本专用：不规则映射指向的原形也必须在词表里，复用同一份常量避免漂移。
from english_learner.services.readability import _IRREGULAR_FORMS

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "verification" / "wordlist_raw"
OUTPUT = ROOT / "src" / "english_learner" / "assets" / "wordlists" / "ai_frequency.json"

BANDS: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1")
# 每档按首字母切三段，既能覆盖各字母，也能控制单次输出长度。
LETTER_RANGES: tuple[tuple[str, str], ...] = (("a", "d"), ("e", "k"), ("l", "p"), ("q", "z"))
WORDS_PER_CHUNK = 150

_WORD_RE = re.compile(r"^[a-z][a-z-]{0,23}$")

# AI 只收“原形”，实测容易漏掉这些高频功能词；缺了它们生词比例会整体虚高。
# 生成后无条件按 A1 补入（已有更高档位时保留原档位）。
CORE_WORDS: tuple[str, ...] = (
    "a",
    "about",
    "after",
    "again",
    "all",
    "also",
    "always",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "done",
    "even",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "here",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "just",
    "let",
    "may",
    "me",
    "might",
    "more",
    "most",
    "must",
    "my",
    "no",
    "not",
    "now",
    "of",
    "on",
    "only",
    "or",
    "our",
    "ours",
    "out",
    "over",
    "said",
    "say",
    "says",
    "she",
    "should",
    "so",
    "some",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "too",
    "under",
    "up",
    "us",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "whose",
    "why",
    "will",
    "with",
    "would",
    "yes",
    "you",
    "your",
    "yours",
)


def _cache_path(band: str, start: str) -> Path:
    return CACHE_DIR / f"{band.lower()}_{start}.json"


def _prompt(band: str, start: str, end: str) -> str:
    """提示词刻意短、且要求“直接输出”：长指令或要求排序会让模型长时间思考，
    输出额度被 thinking 吃满后触发 finish_reason=length（实测 A1 尤其明显）。"""
    return (
        f"直接输出结果，不要思考过程。列出以 {start} 到 {end} 字母开头的、"
        f"CEFR 等级为 {band} 的常用英语单词，尽量多给"
        f"（目标 {WORDS_PER_CHUNK} 个，凑不满也可以）。\n"
        f"只收原形，不要短语、专有名词。每行一个，格式 word,{band}。"
        f"不要编号、不要解释、不要表头。"
    )


def _parse(text: str, band: str) -> dict[str, str]:
    words: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        if not line or "," not in line:
            continue
        word, _, value = line.partition(",")
        word = word.strip().lower()
        value = value.strip().upper()
        if value not in BANDS:
            value = band
        # 去掉序号残留："1. apple" → "apple"
        word = re.sub(r"^\d+[.)]\s*", "", word).strip()
        if not _WORD_RE.match(word):
            continue
        words.setdefault(word, value)
    return words


def _load_or_generate(client: AIClient, band: str, start: str, end: str) -> dict[str, str]:
    cache = _cache_path(band, start)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                print(f"[cache] {band} {start}-{end}: {len(data)}")
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            print(f"[cache] 损坏，重新生成：{cache}")

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            text = client.complete(
                [{"role": "user", "content": _prompt(band, start, end)}],
                temperature=0.3,
                max_tokens=8000,
                task="词表生成",
            )
        except AIError as exc:  # 网关偶发截断/超时：重试同一批
            last_error = exc
            print(f"[retry {attempt + 1}] {band} {start}-{end}: {exc}")
            continue
        words = _parse(text, band)
        if len(words) < 80:
            last_error = RuntimeError(f"只解析到 {len(words)} 个词")
            print(f"[retry {attempt + 1}] {band} {start}-{end}: {last_error}")
            continue
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(words, ensure_ascii=False, indent=0, sort_keys=True),
            encoding="utf-8",
        )
        print(f"[gen] {band} {start}-{end}: {len(words)}")
        return words
    raise SystemExit(f"生成失败：{band} {start}-{end}（{last_error}）")


def main() -> int:
    config = ConfigStore().load()
    print(f"开始生成词表：{len(BANDS)} 档 × {len(LETTER_RANGES)} 段", flush=True)
    if config is None:
        print("没有可用的 AI 配置，先运行应用完成配置。")
        return 1

    # 单批要 1~2 分钟，串行 20 批太久；底层 OpenAI 客户端本身线程安全，
    # 用 3 个线程并发生成，缓存文件互不重叠。
    jobs = [(band, start, end) for band in BANDS for start, end in LETTER_RANGES]
    merged: dict[str, str] = {}
    with AIClient(config, timeout=300.0) as client:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(
                pool.map(lambda job: _load_or_generate(client, job[0], job[1], job[2]), jobs)
            )
    for words in results:
        for word, value in words.items():
            # 低档优先：A1 里出现过就不再用 B1 覆盖。
            merged.setdefault(word, value)
    for word in CORE_WORDS:
        merged.setdefault(word, "A1")
    for bases in _IRREGULAR_FORMS.values():
        for base in bases:
            merged.setdefault(base, "A1")

    ordered = {word: merged[word] for word in sorted(merged)}
    counts = {band: sum(1 for value in ordered.values() if value == band) for band in BANDS}
    payload = {
        "version": 1,
        "source": "AI 一次性生成（scripts/generate_wordlist.py），仅用于本地可读性指标",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(ordered),
        "bands": counts,
        "words": ordered,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"写入 {OUTPUT}：{len(ordered)} 词，{size_kb:.1f} KB，分布 {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
