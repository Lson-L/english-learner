"""领域模型：与数据库表一一对应的不可变 dataclass。

Java 对照：
- 这些类 ≈ 一组不可变 POJO / record；
- `from_row()` ≈ 手写的 RowMapper，把 sqlite3.Row 映射成对象；
- `to_params()` ≈ 反向的 ParameterSource，交给 Repository 拼 SQL 参数。

约定：
- 模型只负责“数据形状”，不写 SQL、不碰界面；
- 所有字段都是不可变的（frozen=True），修改用 dataclasses.replace 生成新对象；
- JSON 字段（如 strengths_json）在这里统一序列化/反序列化，仓库层不关心细节。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# CEFR 等级：V1 只处理这四档，测评结果和文章等级都必须落在这个集合里。
CEFR_LEVELS: tuple[str, ...] = ("A2", "B1", "B2", "C1")

# 生词状态机：new（刚加入）→ learning（复习中）→ mastered（已掌握）。
WORD_STATUSES: tuple[str, ...] = ("new", "learning", "mastered")


# --------------------------------------------------------------------- 时间
def now_iso() -> str:
    """当前本地时间（精确到秒），统一作为 created_at / updated_at。"""
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    """今天的日期字符串（YYYY-MM-DD），作为 article_date / due_date 的键。"""
    return date.today().isoformat()


# --------------------------------------------------------------- JSON 辅助
def load_str_list(value: Any) -> tuple[str, ...]:
    """把数据库里的 JSON 文本（或已经解析好的 list）统一成字符串元组。"""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(str(item).strip() for item in data if str(item).strip())


def dump_str_list(values: tuple[str, ...] | list[str]) -> str:
    """字符串序列 → JSON 文本，写入 *_json 列。"""
    return json.dumps(list(values), ensure_ascii=False)


# --------------------------------------------------------------- 学习画像
@dataclass(frozen=True)
class Profile:
    """用户的英语水平画像（单行表，id 固定为 1）。"""

    cefr_level: str
    vocab_estimate: int = 0
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    comment: str = ""
    assessed_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Profile:
        return cls(
            cefr_level=str(row["cefr_level"]),
            vocab_estimate=int(row["vocab_estimate"]),
            strengths=load_str_list(row["strengths_json"]),
            weaknesses=load_str_list(row["weaknesses_json"]),
            comment=str(row["comment"] or ""),
            assessed_at=str(row["assessed_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "cefr_level": self.cefr_level,
            "vocab_estimate": int(self.vocab_estimate),
            "strengths_json": dump_str_list(self.strengths),
            "weaknesses_json": dump_str_list(self.weaknesses),
            "comment": self.comment,
            "assessed_at": self.assessed_at,
            "updated_at": self.updated_at,
        }


# ------------------------------------------------------------------- 文章
@dataclass(frozen=True)
class Article:
    """每日文章。一天一篇，article_date 唯一。"""

    article_date: str
    title: str
    content: str
    level: str
    topic: str = ""
    word_count: int = 0
    target_words: tuple[str, ...] = ()
    read_at: str | None = None
    created_at: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> Article:
        return cls(
            id=int(row["id"]),
            article_date=str(row["article_date"]),
            title=str(row["title"]),
            content=str(row["content"]),
            level=str(row["level"]),
            topic=str(row["topic"] or ""),
            word_count=int(row["word_count"]),
            target_words=load_str_list(row["target_words_json"]),
            read_at=row["read_at"],
            created_at=str(row["created_at"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        """不含 id：id 由 SQLite 自增生成。"""
        return {
            "article_date": self.article_date,
            "title": self.title,
            "content": self.content,
            "level": self.level,
            "topic": self.topic,
            "word_count": int(self.word_count),
            "target_words_json": dump_str_list(self.target_words),
            "read_at": self.read_at,
            "created_at": self.created_at,
        }


# ------------------------------------------------------------------- 生词
@dataclass(frozen=True)
class Word:
    """生词本条目，同时充当查词缓存（lemma 唯一，大小写不敏感）。"""

    lemma: str
    display_form: str = ""
    phonetic: str = ""
    pos: str = ""
    meaning_cn: str = ""
    meaning_en: str = ""
    example_en: str = ""
    example_cn: str = ""
    context_sentence: str = ""
    article_id: int | None = None
    status: str = "new"
    ease: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_date: str | None = None
    created_at: str = ""
    updated_at: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> Word:
        return cls(
            id=int(row["id"]),
            lemma=str(row["lemma"]),
            display_form=str(row["display_form"] or ""),
            phonetic=str(row["phonetic"] or ""),
            pos=str(row["pos"] or ""),
            meaning_cn=str(row["meaning_cn"] or ""),
            meaning_en=str(row["meaning_en"] or ""),
            example_en=str(row["example_en"] or ""),
            example_cn=str(row["example_cn"] or ""),
            context_sentence=str(row["context_sentence"] or ""),
            article_id=row["article_id"],
            status=str(row["status"] or "new"),
            ease=float(row["ease"]),
            interval_days=int(row["interval_days"]),
            repetitions=int(row["repetitions"]),
            due_date=row["due_date"],
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        """不含 id：upsert 依靠 lemma 唯一约束定位已有行。"""
        return {
            "lemma": self.lemma,
            "display_form": self.display_form,
            "phonetic": self.phonetic,
            "pos": self.pos,
            "meaning_cn": self.meaning_cn,
            "meaning_en": self.meaning_en,
            "example_en": self.example_en,
            "example_cn": self.example_cn,
            "context_sentence": self.context_sentence,
            "article_id": self.article_id,
            "status": self.status,
            "ease": float(self.ease),
            "interval_days": int(self.interval_days),
            "repetitions": int(self.repetitions),
            "due_date": self.due_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WordDefinition:
    """AI 查词返回的释义，不含任何 SRS / 数据库字段。"""

    lemma: str
    display_form: str = ""
    phonetic: str = ""
    pos: str = ""
    meaning_cn: str = ""
    meaning_en: str = ""
    example_en: str = ""
    example_cn: str = ""

    @classmethod
    def from_word(cls, word: Word) -> WordDefinition:
        return cls(
            lemma=word.lemma,
            display_form=word.display_form or word.lemma,
            phonetic=word.phonetic,
            pos=word.pos,
            meaning_cn=word.meaning_cn,
            meaning_en=word.meaning_en,
            example_en=word.example_en,
            example_cn=word.example_cn,
        )


# ------------------------------------------------------------------- 高亮
@dataclass(frozen=True)
class Highlight:
    """正文高亮：记录生词在文章里的字符偏移，用于重绘。"""

    article_id: int
    word_id: int
    start_offset: int
    end_offset: int
    sentence: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> Highlight:
        return cls(
            id=int(row["id"]),
            article_id=int(row["article_id"]),
            word_id=int(row["word_id"]),
            start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]),
            sentence=str(row["sentence"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "article_id": int(self.article_id),
            "word_id": int(self.word_id),
            "start_offset": int(self.start_offset),
            "end_offset": int(self.end_offset),
            "sentence": self.sentence,
        }


# ------------------------------------------------------------------- 复习
@dataclass(frozen=True)
class ReviewLog:
    """一次复习记录：评分前后的间隔天数，用于统计与回溯。"""

    word_id: int
    rating: int
    prev_interval: int = 0
    next_interval: int = 0
    reviewed_at: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> ReviewLog:
        return cls(
            id=int(row["id"]),
            word_id=int(row["word_id"]),
            rating=int(row["rating"]),
            prev_interval=int(row["prev_interval"]),
            next_interval=int(row["next_interval"]),
            reviewed_at=str(row["reviewed_at"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "word_id": int(self.word_id),
            "rating": int(self.rating),
            "prev_interval": int(self.prev_interval),
            "next_interval": int(self.next_interval),
            "reviewed_at": self.reviewed_at,
        }


# --------------------------------------------------------------- 摸底测评
@dataclass(frozen=True)
class AssessmentResult:
    """AI 评分结果（尚未落库）。cefr 必须属于 CEFR_LEVELS。"""

    cefr: str
    vocab_estimate: int = 0
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    comment: str = ""


# --------------------------------------------------------------- 每日统计
@dataclass(frozen=True)
class DailyStats:
    """某一天的学习统计，用于首页连续天数等指标。"""

    stat_date: str
    article_read: int = 0
    words_added: int = 0
    reviews_done: int = 0

    @classmethod
    def from_row(cls, row: Any) -> DailyStats:
        return cls(
            stat_date=str(row["stat_date"]),
            article_read=int(row["article_read"]),
            words_added=int(row["words_added"]),
            reviews_done=int(row["reviews_done"]),
        )

    @property
    def is_active(self) -> bool:
        """当天有过任意学习行为即算打卡。"""
        return bool(self.article_read or self.words_added or self.reviews_done)
