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


def normalize_cefr_level(value: object, default: str = "") -> str:
    """归一化 CEFR 等级：A1 并入 A2，非法值返回 default。

    模型经常把 A1 或小写等级写进 JSON，统一在这里处理，避免每个服务各写一份。
    """
    text = str(value or "").strip().upper()
    if text == "A1":
        return "A2"
    return text if text in CEFR_LEVELS else default


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
    """用户的英语水平画像（单行表，id 固定为 1）。

    `pending_level / pending_count` 用于“连续两次向上证据才升级”：
    第一次检测建议升级时只记录待定等级，第二次同向建议才真正写入 `cefr_level`。
    降级不等待，直接生效。
    """

    cefr_level: str
    vocab_estimate: int = 0
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    comment: str = ""
    assessed_at: str = ""
    updated_at: str = ""
    pending_level: str = ""
    pending_count: int = 0
    # Phase 2：等级估计的置信度（0~1）与最近一次调整原因，供界面解释“为什么是 B1”。
    level_confidence: float = 0.0
    level_updated_reason: str = ""

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
            pending_level=str(row["pending_level"] or ""),
            pending_count=int(row["pending_count"] or 0),
            level_confidence=float(row["level_confidence"] or 0.0),
            level_updated_reason=str(row["level_updated_reason"] or ""),
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
            "pending_level": self.pending_level,
            "pending_count": int(self.pending_count),
            "level_confidence": float(self.level_confidence),
            "level_updated_reason": self.level_updated_reason,
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
    mnemonic: str = ""
    context_sentence: str = ""
    article_id: int | None = None
    # False = 只是查词缓存；True = 用户点过“加入生词本”
    in_vocabulary: bool = False
    # AI 查词时顺带标注的 CEFR 词档（A2/B1/B2/C1）；旧词条可能为空。
    cefr_band: str = ""
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
            mnemonic=str(row["mnemonic"] or ""),
            context_sentence=str(row["context_sentence"] or ""),
            article_id=row["article_id"],
            in_vocabulary=bool(row["in_vocabulary"]),
            cefr_band=str(row["cefr_band"] or ""),
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
            "mnemonic": self.mnemonic,
            "context_sentence": self.context_sentence,
            "article_id": self.article_id,
            "in_vocabulary": 1 if self.in_vocabulary else 0,
            "cefr_band": self.cefr_band,
            "status": self.status,
            "ease": float(self.ease),
            "interval_days": int(self.interval_days),
            "repetitions": int(self.repetitions),
            "due_date": self.due_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WordForm:
    """词形缓存（went → go），用于查词时避免重复调用 AI。"""

    form: str
    word_id: int
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> WordForm:
        return cls(
            form=str(row["form"]),
            word_id=int(row["word_id"]),
            created_at=str(row["created_at"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "form": self.form,
            "word_id": int(self.word_id),
            "created_at": self.created_at,
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
    note: str = ""
    advice: str = ""
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
            note=str(row["note"] or ""),
            advice=str(row["advice"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "word_id": int(self.word_id),
            "rating": int(self.rating),
            "prev_interval": int(self.prev_interval),
            "next_interval": int(self.next_interval),
            "reviewed_at": self.reviewed_at,
            "note": self.note,
            "advice": self.advice,
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


# --------------------------------------------------------------- 阅读检测
@dataclass(frozen=True)
class QuizQuestion:
    """阅读检测的一道题：选择题（kind=choice）或短答题（kind=short）。

    选择题用 `options + answer_index` 本地判分；短答题没有选项，
    由 AI 按 0/1 评分，`reference_answer` 只用于讲评与展示。
    """

    prompt: str
    options: tuple[str, ...] = ()
    answer_index: int = -1
    explanation: str = ""
    skill: str = ""
    difficulty: str = ""
    kind: str = "choice"
    reference_answer: str = ""

    @property
    def is_short(self) -> bool:
        return self.kind == "short"

    @classmethod
    def from_dict(cls, data: Any) -> QuizQuestion | None:
        """从数据库 JSON 还原；结构不完整时返回 None（跳过该题）。"""
        if not isinstance(data, dict):
            return None
        prompt = str(data.get("prompt") or data.get("question") or "").strip()
        if not prompt:
            return None

        raw_kind = str(data.get("kind") or data.get("type") or "choice").strip().lower()
        # 模型偶尔会把改写/翻译写成 kind=rewrite/translation；都按主动产出题处理。
        kind = (
            "short"
            if raw_kind
            in {
                "short",
                "short_answer",
                "open",
                "rewrite",
                "sentence_rewrite",
                "translation",
                "translate",
            }
            else "choice"
        )
        skill = str(data.get("skill") or "").strip()
        difficulty = str(data.get("difficulty") or "").strip().upper()
        explanation = str(data.get("explanation") or "").strip()

        if kind == "short":
            return cls(
                prompt=prompt,
                explanation=explanation,
                skill=skill,
                difficulty=difficulty,
                kind="short",
                reference_answer=str(data.get("reference_answer") or "").strip(),
            )

        raw_options = data.get("options")
        if not isinstance(raw_options, list):
            return None
        options = tuple(str(item).strip() for item in raw_options if str(item).strip())
        try:
            answer_index = int(data.get("answer_index", -1))
        except (TypeError, ValueError):
            return None
        if len(options) < 2 or not (0 <= answer_index < len(options)):
            return None
        return cls(
            prompt=prompt,
            options=options,
            answer_index=answer_index,
            explanation=explanation,
            skill=skill,
            difficulty=difficulty,
            kind="choice",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "options": list(self.options),
            "answer_index": int(self.answer_index),
            "explanation": self.explanation,
            "skill": self.skill,
            "difficulty": self.difficulty,
            "kind": self.kind,
            "reference_answer": self.reference_answer,
        }

    def letter(self, index: int) -> str:
        """选项下标 → A/B/C…；-1 表示未作答。"""
        if not (0 <= index < len(self.options)):
            return "未作答"
        return chr(ord("A") + index)


@dataclass(frozen=True)
class QuizProduction:
    """一道主动产出题（短答 / 句子改写 / 翻译）的用户答案与得分。

    Phase 3 起一套题可以有 2 道产出题，旧数据只有 1 道；
    `ReadingQuiz.production_answers` 会把两种形态统一成等长元组。
    """

    text: str = ""
    score: int = 0
    feedback: str = ""
    # False = AI 没有给出有效分数（格式问题），不应计入等级证据。
    scored: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> QuizProduction | None:
        if not isinstance(data, dict):
            return None
        try:
            score = int(data.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        return cls(
            text=str(data.get("text") or "").strip(),
            score=1 if score >= 1 else 0,
            feedback=str(data.get("feedback") or "").strip(),
            scored=bool(data.get("scored", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": int(self.score),
            "feedback": self.feedback,
            "scored": bool(self.scored),
        }


@dataclass(frozen=True)
class ReadingQuiz:
    """某篇文章的阅读检测：题目 + 用户作答 + AI 讲评与调级结果。

    一篇文章最多一套题（article_id 唯一）；文章被替换时级联删除。
    `completed_at` 为空表示只出了题、还没提交答案。

    M3.9 起：题目带 `skill / difficulty`，混合难度出题；短答、错因、自评、
    建议等级、待定等级与证据强度都落库。

    Phase 2 / 3 起：
    - `stage`：1 = 只出了第一轮 3 道当前等级题；2 = 已按第一轮表现补齐题目；
    - `productions`：1~2 道主动产出题的答案与得分（旧行用 short_answer_* 兼容）；
    - `level_confidence / posterior`：本地后验与置信度，供结果页解释等级；
    - `practice_mode`：同一天第二次检测只练手、不调级。
    """

    article_id: int
    article_date: str
    level: str
    questions: tuple[QuizQuestion, ...]
    answers: tuple[int, ...] = ()
    explanations: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()
    short_answer_text: str = ""
    short_answer_score: int = 0
    self_report: str = ""
    score: int = 0
    recommended_level: str = ""
    previous_level: str = ""
    result_level: str = ""
    pending_level: str = ""
    pending_count: int = 0
    feedback: str = ""
    evidence_ratio: float = 0.0
    productions: tuple[QuizProduction, ...] = ()
    level_confidence: float = 0.0
    posterior: tuple[float, ...] = ()
    practice_mode: bool = False
    stage: int = 2
    created_at: str = ""
    completed_at: str | None = None
    id: int | None = None

    @property
    def choice_questions(self) -> tuple[QuizQuestion, ...]:
        return tuple(question for question in self.questions if not question.is_short)

    @property
    def production_questions(self) -> tuple[QuizQuestion, ...]:
        return tuple(question for question in self.questions if question.is_short)

    @property
    def short_question(self) -> QuizQuestion | None:
        """旧接口：第一道主动产出题（短答）。"""
        return next((question for question in self.questions if question.is_short), None)

    @property
    def choice_total(self) -> int:
        return len(self.choice_questions)

    @property
    def choice_correct(self) -> int:
        return sum(
            1
            for question, chosen in zip(self.choice_questions, self.answers, strict=False)
            if chosen == question.answer_index
        )

    @property
    def short_total(self) -> int:
        """主动产出题数量（旧数据为 0 或 1，Phase 3 为 1~2）。"""
        return len(self.production_questions)

    @property
    def total(self) -> int:
        return self.choice_total + self.short_total

    @property
    def completed(self) -> bool:
        return self.completed_at is not None

    @property
    def percent(self) -> int:
        if not self.total:
            return 0
        return round(self.score * 100 / self.total)

    @property
    def level_changed(self) -> bool:
        return bool(self.previous_level and self.result_level != self.previous_level)

    @property
    def waiting_for_upgrade(self) -> bool:
        return bool(self.pending_level and self.pending_count > 0)

    @property
    def production_answers(self) -> tuple[QuizProduction, ...]:
        """与 `production_questions` 等长的答案元组，兼容旧的单短答字段。"""
        count = self.short_total
        if count <= 0:
            return ()
        items = list(self.productions[:count])
        if not items and self.short_answer_text:
            items.append(
                QuizProduction(text=self.short_answer_text, score=int(self.short_answer_score))
            )
        while len(items) < count:
            items.append(QuizProduction())
        return tuple(items)

    @property
    def production_score(self) -> int:
        return sum(item.score for item in self.production_answers)

    @property
    def scored_production_total(self) -> int:
        """真正参与判分的产出题数量（排除 AI 漏评分的题）。"""
        return sum(1 for item in self.production_answers if item.scored)

    @property
    def unscored_production_count(self) -> int:
        """AI 没有给出有效分数的产出题数量。"""
        return sum(1 for item in self.production_answers if not item.scored)

    @property
    def posterior_map(self) -> dict[str, float]:
        if len(self.posterior) != len(CEFR_LEVELS):
            return {}
        return dict(zip(CEFR_LEVELS, self.posterior, strict=True))

    @classmethod
    def from_row(cls, row: Any) -> ReadingQuiz:
        questions = tuple(
            question
            for question in (
                QuizQuestion.from_dict(item) for item in _load_json_list(row["questions_json"])
            )
            if question is not None
        )
        answers = tuple(
            int(item)
            for item in _load_json_list(row["answers_json"])
            if isinstance(item, (int, float, str)) and str(item).lstrip("-").isdigit()
        )
        explanations = tuple(
            str(item).strip() for item in _load_json_list(row["explanations_json"])
        )
        error_types = tuple(str(item).strip() for item in _load_json_list(row["error_types_json"]))
        productions = tuple(
            production
            for production in (
                QuizProduction.from_dict(item) for item in _load_json_list(row["productions_json"])
            )
            if production is not None
        )
        completed_at = row["completed_at"]
        return cls(
            id=int(row["id"]),
            article_id=int(row["article_id"]),
            article_date=str(row["article_date"]),
            level=str(row["level"]),
            questions=questions,
            answers=answers,
            explanations=explanations,
            error_types=error_types,
            short_answer_text=str(row["short_answer_text"] or ""),
            short_answer_score=int(row["short_answer_score"] or 0),
            self_report=str(row["self_report"] or ""),
            score=int(row["score"]),
            recommended_level=str(row["recommended_level"] or ""),
            previous_level=str(row["previous_level"] or ""),
            result_level=str(row["result_level"] or ""),
            pending_level=str(row["pending_level"] or ""),
            pending_count=int(row["pending_count"] or 0),
            feedback=str(row["feedback"] or ""),
            evidence_ratio=float(row["evidence_ratio"] or 0.0),
            productions=productions,
            level_confidence=float(row["level_confidence"] or 0.0),
            posterior=_load_posterior(row["posterior_json"]),
            practice_mode=bool(row["practice_mode"]),
            stage=int(row["stage"] or 2),
            created_at=str(row["created_at"] or ""),
            completed_at=str(completed_at) if completed_at else None,
        )

    def to_params(self) -> dict[str, Any]:
        """不含 id：article_id 唯一，插入时由 SQLite 自增 id。"""
        return {
            "article_id": int(self.article_id),
            "article_date": self.article_date,
            "level": self.level,
            "questions_json": json.dumps(
                [question.to_dict() for question in self.questions], ensure_ascii=False
            ),
            "answers_json": json.dumps(list(self.answers), ensure_ascii=False),
            "explanations_json": json.dumps(list(self.explanations), ensure_ascii=False),
            "error_types_json": json.dumps(list(self.error_types), ensure_ascii=False),
            "short_answer_text": self.short_answer_text,
            "short_answer_score": int(self.short_answer_score),
            "self_report": self.self_report,
            "score": int(self.score),
            "recommended_level": self.recommended_level,
            "previous_level": self.previous_level,
            "result_level": self.result_level,
            "pending_level": self.pending_level,
            "pending_count": int(self.pending_count),
            "feedback": self.feedback,
            "evidence_ratio": float(self.evidence_ratio),
            "productions_json": json.dumps(
                [production.to_dict() for production in self.productions], ensure_ascii=False
            ),
            "level_confidence": float(self.level_confidence),
            "posterior_json": json.dumps(self.posterior_map, ensure_ascii=False),
            "practice_mode": 1 if self.practice_mode else 0,
            "stage": int(self.stage),
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


def _load_json_list(value: Any) -> list[Any]:
    """把数据库里的 JSON 文本（或已解析的 list）统一成 list。"""
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return list(data) if isinstance(data, list) else []


def _load_posterior(value: Any) -> tuple[float, ...]:
    """posterior_json → 与 CEFR_LEVELS 对齐的概率元组；缺失时返回空元组。"""
    if isinstance(value, dict):
        data = value
    else:
        try:
            loaded = json.loads(str(value))
        except (TypeError, ValueError):
            return ()
        if not isinstance(loaded, dict):
            return ()
        data = loaded
    if not data:
        return ()
    return tuple(float(data.get(level) or 0.0) for level in CEFR_LEVELS)


@dataclass(frozen=True)
class BandReviewStat:
    """按词档聚合的复习表现：`known` 为评分 >= 3（认识 / 太简单）的次数。"""

    band: str
    total: int
    known: int

    @property
    def accuracy(self) -> float:
        return self.known / self.total if self.total else 0.0


# --------------------------------------------------------------- 等级证据
@dataclass(frozen=True)
class EvidenceObservation:
    """一条可参与后验计算的观测（答对/答错某难度的一道题）。

    `weight` 用来表达证据强度：学生自己的检测题 = 1.0，
    AI 盲评建议 / 复习词档 = 0.3~0.5，越弱的信号越不应该推动等级。
    """

    difficulty: str
    correct: bool
    weight: float = 1.0
    source: str = "quiz"

    @classmethod
    def from_dict(cls, data: Any) -> EvidenceObservation | None:
        if not isinstance(data, dict):
            return None
        difficulty = normalize_cefr_level(data.get("difficulty"))
        if not difficulty:
            return None
        try:
            weight = float(data.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        return cls(
            difficulty=difficulty,
            correct=bool(data.get("correct")),
            weight=max(0.0, min(2.0, weight)),
            source=str(data.get("source") or "quiz"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty": self.difficulty,
            "correct": bool(self.correct),
            "weight": round(float(self.weight), 3),
            "source": self.source,
        }


@dataclass(frozen=True)
class LevelEvidence:
    """一次等级证据事件（当前主要来自阅读检测）。

    表 `level_evidence` 与 `reading_quizzes` 解耦：检测题被文章替换级联删除后，
    历史证据仍然保留，用于后续后验回放。
    """

    source: str
    observed_at: str
    # 学习日（通常是文章的 article_date）；用于“同一天只做一次正式检测”。
    evidence_date: str = ""
    article_level: str = ""
    quiz_id: int | None = None
    choice_correct: int = 0
    choice_total: int = 0
    short_score: int = 0
    short_total: int = 0
    weighted_points: float = 0.0
    evidence_ratio: float = 0.0
    error_types: tuple[str, ...] = ()
    observations: tuple[EvidenceObservation, ...] = ()
    posterior: tuple[float, ...] = ()
    confidence: float = 0.0
    id: int | None = None

    @property
    def posterior_map(self) -> dict[str, float]:
        if len(self.posterior) != len(CEFR_LEVELS):
            return {}
        return dict(zip(CEFR_LEVELS, self.posterior, strict=True))

    @classmethod
    def from_row(cls, row: Any) -> LevelEvidence:
        observations = tuple(
            observation
            for observation in (
                EvidenceObservation.from_dict(item)
                for item in _load_json_list(row["observations_json"])
            )
            if observation is not None
        )
        return cls(
            id=int(row["id"]),
            source=str(row["source"] or ""),
            observed_at=str(row["observed_at"] or ""),
            evidence_date=str(row["evidence_date"] or ""),
            article_level=str(row["article_level"] or ""),
            quiz_id=int(row["quiz_id"]) if row["quiz_id"] is not None else None,
            choice_correct=int(row["choice_correct"] or 0),
            choice_total=int(row["choice_total"] or 0),
            short_score=int(row["short_score"] or 0),
            short_total=int(row["short_total"] or 0),
            weighted_points=float(row["weighted_points"] or 0.0),
            evidence_ratio=float(row["evidence_ratio"] or 0.0),
            error_types=load_str_list(row["error_types_json"]),
            observations=observations,
            posterior=_load_posterior(row["posterior_json"]),
            confidence=float(row["confidence"] or 0.0),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "observed_at": self.observed_at,
            "evidence_date": self.evidence_date,
            "article_level": self.article_level,
            "quiz_id": self.quiz_id,
            "choice_correct": int(self.choice_correct),
            "choice_total": int(self.choice_total),
            "short_score": int(self.short_score),
            "short_total": int(self.short_total),
            "weighted_points": float(self.weighted_points),
            "evidence_ratio": float(self.evidence_ratio),
            "error_types_json": dump_str_list(self.error_types),
            "observations_json": json.dumps(
                [observation.to_dict() for observation in self.observations],
                ensure_ascii=False,
            ),
            "posterior_json": json.dumps(self.posterior_map, ensure_ascii=False),
            "confidence": float(self.confidence),
        }


# --------------------------------------------------------------- 每周复盘
@dataclass(frozen=True)
class WeeklyReview:
    """AI 每周复盘：只给建议，用户确认后才改画像。"""

    week_start: str
    summary: str = ""
    suggested_level: str = ""
    focus: tuple[str, ...] = ()
    quiz_count: int = 0
    review_count: int = 0
    words_added: int = 0
    confidence: float = 0.0
    applied_level: str = ""
    created_at: str = ""
    applied_at: str = ""
    id: int | None = None

    @property
    def applied(self) -> bool:
        return bool(self.applied_at)

    @classmethod
    def from_row(cls, row: Any) -> WeeklyReview:
        return cls(
            id=int(row["id"]),
            week_start=str(row["week_start"]),
            summary=str(row["summary"] or ""),
            suggested_level=str(row["suggested_level"] or ""),
            focus=load_str_list(row["focus_json"]),
            quiz_count=int(row["quiz_count"] or 0),
            review_count=int(row["review_count"] or 0),
            words_added=int(row["words_added"] or 0),
            confidence=float(row["confidence"] or 0.0),
            applied_level=str(row["applied_level"] or ""),
            created_at=str(row["created_at"] or ""),
            applied_at=str(row["applied_at"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        return {
            "week_start": self.week_start,
            "summary": self.summary,
            "suggested_level": self.suggested_level,
            "focus_json": dump_str_list(self.focus),
            "quiz_count": int(self.quiz_count),
            "review_count": int(self.review_count),
            "words_added": int(self.words_added),
            "confidence": float(self.confidence),
            "applied_level": self.applied_level,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
        }


# --------------------------------------------------------------- 每日统计
# --------------------------------------------------------------- AI 调用记录
@dataclass(frozen=True)
class AICall:
    """一次 AI HTTP 请求的用量记录（只追加，不保存提示词与回复正文）。

    一次业务操作可能对应多行：请求被重试、或 JSON 模式先失败后降级，
    每一行代表一次真实的 HTTP 尝试，`attempt` 从 1 开始编号。
    `prompt_tokens` 等字段允许为空：部分兼容网关不返回 usage，
    统计时按 NULL 处理（SUM 会忽略），界面上显示为 “—”。
    """

    called_at: str
    task: str = ""
    model: str = ""
    base_url: str = ""
    json_mode: bool = False
    attempt: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int = 0
    ok: bool = True
    error_kind: str = ""
    error_message: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> AICall:
        def _optional_int(value: Any) -> int | None:
            return None if value is None else int(value)

        return cls(
            id=int(row["id"]),
            called_at=str(row["called_at"] or ""),
            task=str(row["task"] or ""),
            model=str(row["model"] or ""),
            base_url=str(row["base_url"] or ""),
            json_mode=bool(row["json_mode"]),
            attempt=int(row["attempt"] or 1),
            prompt_tokens=_optional_int(row["prompt_tokens"]),
            completion_tokens=_optional_int(row["completion_tokens"]),
            total_tokens=_optional_int(row["total_tokens"]),
            latency_ms=int(row["latency_ms"] or 0),
            ok=bool(row["ok"]),
            error_kind=str(row["error_kind"] or ""),
            error_message=str(row["error_message"] or ""),
        )

    def to_params(self) -> dict[str, Any]:
        """不含 id：id 由 SQLite 自增生成。"""
        return {
            "called_at": self.called_at,
            "task": self.task,
            "model": self.model,
            "base_url": self.base_url,
            "json_mode": 1 if self.json_mode else 0,
            "attempt": int(self.attempt),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": int(self.latency_ms),
            "ok": 1 if self.ok else 0,
            "error_kind": self.error_kind,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class AIUsageSummary:
    """一段时间内的 AI 调用汇总。tokens 只统计服务端回报了 usage 的调用。"""

    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    avg_latency_ms: int = 0

    @property
    def ok_calls(self) -> int:
        return max(0, int(self.calls) - int(self.failed_calls))

    @classmethod
    def from_row(cls, row: Any) -> AIUsageSummary:
        return cls(
            calls=int(row["calls"] or 0),
            failed_calls=int(row["failed_calls"] or 0),
            prompt_tokens=int(row["prompt_tokens"] or 0),
            completion_tokens=int(row["completion_tokens"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            avg_latency_ms=int(row["avg_latency_ms"] or 0),
        )


@dataclass(frozen=True)
class AIUsageByTask:
    """按任务聚合的调用量（界面上看“哪类操作最费 token”）。"""

    task: str
    calls: int = 0
    failed_calls: int = 0
    total_tokens: int = 0
    avg_latency_ms: int = 0

    @classmethod
    def from_row(cls, row: Any) -> AIUsageByTask:
        return cls(
            task=str(row["task"] or ""),
            calls=int(row["calls"] or 0),
            failed_calls=int(row["failed_calls"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            avg_latency_ms=int(row["avg_latency_ms"] or 0),
        )


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
