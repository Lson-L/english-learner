"""真实文章库：`corpus.json` 加载、校验、种子写入 SQLite。

职责边界：
- 构建期：`assets/real_texts/corpus.json` 是可读、可 review、可测试的内容源；
- 运行期：`seed_real_texts()` 把内容幂等写入 `real_texts`，之后 app 只读 SQLite；
- 本模块是纯 Python，不 import PySide6、不发起网络请求。

Java 对照：
- `load_corpus()` ≈ 读取打包在 resources 里的 seed JSON；
- `validate_corpus()` ≈ Bean Validation，返回错误列表而不是抛异常；
- `seed_real_texts()` ≈ Flyway seed migration，按 `corpus_version` 只升不降。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ..models.entities import CEFR_LEVELS, RealText
from ..storage.db import Database

logger = logging.getLogger(__name__)

# 与 `gui/fonts.py` / `services/readability.py` 相同的资源定位方式：
# assets 与 services 同级，都在 english_learner 包内。
CORPUS_PATH = Path(__file__).resolve().parent.parent / "assets" / "real_texts" / "corpus.json"

# 只收许可明确的来源；Personal use 允许但会 warning，禁止公开分发。
LICENSE_ALLOWLIST: frozenset[str] = frozenset(
    {"Public Domain", "CC0", "CC BY", "CC BY-SA", "Personal use"}
)
MIN_WORDS = 150
MAX_WORDS = 2000
QUESTIONS_PER_TEXT = 4
MIN_TEXTS_PER_LEVEL = 2

# 正文里出现任意 HTML 标签（含 <script> / <p> / <br> 等）都视为抓取残留。
# 只认“<字母…”开头，避免把数学比较 a < b > c 误判成标签；
# 注释 / DOCTYPE / XML 声明单独覆盖。
HTML_TAG_PATTERN = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")
HTML_DECLARATION_PATTERN = re.compile(r"<!--|<!doctype|<\?xml", re.IGNORECASE)


class RealTextCorpusError(RuntimeError):
    """`corpus.json` 缺失、损坏或顶层结构不合法。"""


@dataclass(frozen=True)
class RealTextCorpus:
    """一次加载得到的文章库快照。"""

    version: int
    texts: tuple[RealText, ...]


def load_corpus(path: Path | str | None = None) -> RealTextCorpus:
    """读取 `corpus.json`；结构性问题抛 `RealTextCorpusError`，字段问题交给校验。"""
    target = Path(path) if path is not None else CORPUS_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RealTextCorpusError(f"找不到真实文章库：{target}") from exc
    except (OSError, ValueError) as exc:
        raise RealTextCorpusError(f"真实文章库读取失败：{exc}") from exc

    if not isinstance(data, dict):
        raise RealTextCorpusError("corpus.json 顶层必须是对象")
    raw_texts = data.get("texts")
    if not isinstance(raw_texts, list):
        raise RealTextCorpusError("corpus.json 缺少 texts 数组")

    # 字段类型问题统一翻译成 RealTextCorpusError，调用方只需要处理一种异常
    # （M7.6 内容包导入要靠它区分“结构错误”和真正的 IO 错误）。
    try:
        version = int(data.get("version") or 0)
        texts = tuple(RealText.from_dict(item) for item in raw_texts if isinstance(item, dict))
    except (TypeError, ValueError) as exc:
        raise RealTextCorpusError(f"真实文章库字段格式错误：{exc}") from exc
    return RealTextCorpus(version=version, texts=texts)


def validate_corpus(corpus: RealTextCorpus, *, require_levels: bool = True) -> list[str]:
    """返回错误列表；空列表表示通过。

    `Personal use` 不报错，只记 warning；`require_levels=False` 用于开发期
    内容尚未补齐时，只校验已有条目的结构。
    """
    errors: list[str] = []
    if corpus.version <= 0:
        errors.append("corpus.version 必须 >= 1")
    if not corpus.texts:
        errors.append("corpus.texts 为空")

    seen: set[str] = set()
    level_counts: dict[str, int] = {level: 0 for level in CEFR_LEVELS}
    for text in corpus.texts:
        prefix = f"[{text.id or '?'}] "
        if not text.id:
            errors.append(prefix + "id 为空")
        elif text.id in seen:
            errors.append(prefix + "id 重复")
        seen.add(text.id)

        if not text.title:
            errors.append(prefix + "title 为空")
        if not text.content.strip():
            errors.append(prefix + "content 为空")
        if text.level not in CEFR_LEVELS:
            errors.append(prefix + f"level 非法：{text.level!r}")
        else:
            level_counts[text.level] += 1

        if text.license not in LICENSE_ALLOWLIST:
            errors.append(prefix + f"license 不在白名单：{text.license!r}")
        elif text.license == "Personal use":
            logger.warning("真实文章 %s 标记为 Personal use，不要公开分发", text.id)

        actual_words = len(text.content.split())
        if text.word_count != actual_words:
            errors.append(
                prefix + f"word_count 不匹配：声明 {text.word_count}，实际 {actual_words}"
            )
        if not (MIN_WORDS <= actual_words <= MAX_WORDS):
            errors.append(prefix + f"词数不在 {MIN_WORDS}~{MAX_WORDS}：{actual_words}")

        lowered = text.content.lower()
        if (
            HTML_TAG_PATTERN.search(text.content)
            or HTML_DECLARATION_PATTERN.search(text.content)
            or "*** start of" in lowered
            or "*** end of" in lowered
        ):
            # 只去掉开头、留下 *** END OF 的语料同样算不合格。
            errors.append(prefix + "content 含 HTML 标签或 Gutenberg 头尾标记")

        if len(text.questions) != QUESTIONS_PER_TEXT:
            errors.append(prefix + f"题目数必须是 {QUESTIONS_PER_TEXT}，实际 {len(text.questions)}")
        for index, question in enumerate(text.questions, start=1):
            qprefix = prefix + f"第 {index} 题："
            if not question.prompt:
                errors.append(qprefix + "prompt 为空")
            if len(question.options) != 4:
                errors.append(qprefix + "必须有 4 个选项")
            if not (0 <= question.answer_index < len(question.options)):
                errors.append(qprefix + "answer_index 越界")
            if not question.explanation:
                errors.append(qprefix + "explanation 为空")

    if require_levels:
        for level, count in level_counts.items():
            if count < MIN_TEXTS_PER_LEVEL:
                errors.append(f"{level} 只有 {count} 篇，至少需要 {MIN_TEXTS_PER_LEVEL} 篇")
    return errors


def seed_real_texts(db: Database, corpus: RealTextCorpus) -> int:
    """按 `corpus_version` 幂等写入；只升不降，返回写入/更新的篇数。"""
    if corpus.version <= 0 or not corpus.texts:
        return 0
    if corpus.version <= db.real_texts.max_corpus_version():
        logger.info("真实文章库已是 v%s，跳过种子", corpus.version)
        return 0

    # 必须整批原子写入：逐条 upsert 会自动提交，中途失败会让
    # MAX(corpus_version) 提前变成新版本，下次启动就再也不会补剩余文章。
    with db.transaction():
        for text in corpus.texts:
            db.real_texts.upsert(replace(text, corpus_version=corpus.version))
    logger.info("真实文章库已种子：v%s，%s 篇", corpus.version, len(corpus.texts))
    return len(corpus.texts)


__all__ = [
    "CORPUS_PATH",
    "LICENSE_ALLOWLIST",
    "MIN_TEXTS_PER_LEVEL",
    "MIN_WORDS",
    "MAX_WORDS",
    "QUESTIONS_PER_TEXT",
    "RealTextCorpus",
    "RealTextCorpusError",
    "load_corpus",
    "seed_real_texts",
    "validate_corpus",
]
