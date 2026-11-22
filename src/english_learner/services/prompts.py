"""所有 AI 提示词集中在这里，并带版本号。

为什么要集中：
- 调 prompt 时只改这一个文件，不用在业务代码里翻字符串；
- 版本号写进日志（未来也可以入库），方便对比“同一篇文章是哪个 prompt 生成的”；
- JSON 契约与本地校验（article_service / assessment_service）保持一致，
  改契约时两边必须一起改。

所有 builder 都返回 OpenAI messages 列表（system + user）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models.entities import (
    CEFR_LEVELS,
    Article,
    BandReviewStat,
    Profile,
    ReadingQuiz,
    Word,
)
from .assessment_bank import (
    OPTION_LETTERS,
    UNKNOWN_ANSWER,
    UNKNOWN_LABEL,
    AssessmentSet,
    Question,
)
from .back_translation import WRITING_ERROR_LABELS, WRITING_ERROR_TYPES
from .learner_memory import LearnerMemory

if TYPE_CHECKING:  # 避免与 assessment_service 循环导入
    from .assessment_service import AnswerSheet, ObjectiveScore

# M6.1：文章 prompt 要求附带 glossary（难词语境义），查词预取新增批量 prompt，
# 让阅读时双击查词尽量走本地缓存。
# M6.2：阅读检测选择题支持「不知道 + 补充说明」，讲评与盲评 prompt 带上原因。
# M6.5：文章生成支持档内位置（low / mid / high），空值时不改变 prompt。
# m6-5a：查词 prompt 只在词形与原形不同时才说明原形，避免释义重复“原形就是 X”。
PROMPT_VERSION = "m6-5a"

# ------------------------------------------------------------ 学习者记忆
# 记忆按任务过滤：不同 AI 任务只该看到与自己相关的那几行。
# 阅读检测不在这里渲染整块，而是把 error_types 并入 `_weak_skills()`。
MEMORY_TASK_ARTICLE = "article"
MEMORY_TASK_DICTIONARY = "dictionary"
MEMORY_TASK_REVIEW_HELP = "review_help"

_MEMORY_FIELDS: dict[str, tuple[str, ...]] = {
    # 上周计划不在这里渲染：文章走独立的 learner_plan 参数，避免同一句话出现两次。
    MEMORY_TASK_ARTICLE: (
        "level",
        "weaknesses",
        "errors",
        "forgotten",
        "misspelled",
        "writing",
        "backlog",
    ),
    MEMORY_TASK_DICTIONARY: ("level", "errors"),
    MEMORY_TASK_REVIEW_HELP: ("level", "forgotten", "misspelled", "focus"),
}

# 记忆块字符上限：字段本身都很短，正常情况远低于上限；
# 这里只是防止用户把 focus / 调级原因写成长文本后挤占正文。
# 按中英混合 tokenizer 估算约 150~200 token。
MAX_MEMORY_CHARS = 280


def _clip(text: str, limit: int) -> str:
    """截断到 limit 个字符；保留换行（记忆块是多行文本）。"""
    value = (text or "").strip()
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def build_learner_memory_block(memory: LearnerMemory, task: str) -> str:
    """把学习记忆渲染成 prompt 片段；空记忆返回空串。

    调用方只在返回值非空时插入 prompt，因此“没有数据”的用户
    拿到的 prompt 与 M5.2 之前逐字节一致（有快照测试兜底）。
    """
    fields = _MEMORY_FIELDS.get(task)
    if memory is None or memory.is_empty or not fields:
        return ""

    lines = ["【学习者记忆】"]
    if "level" in fields and memory.level:
        detail = f"- 等级：{memory.level}"
        extras: list[str] = []
        if memory.confidence > 0:
            extras.append(f"置信度 {memory.confidence:.2f}")
        if memory.level_reason:
            extras.append(_clip(memory.level_reason, 40))
        if extras:
            detail += "（" + "；".join(extras) + "）"
        lines.append(detail)
    if "weaknesses" in fields and memory.weaknesses:
        items = "、".join(_clip(item, 20) for item in memory.weaknesses[:3])
        lines.append("- 测评薄弱点：" + items)
    if "errors" in fields and memory.error_types:
        labels = [_SKILL_LABELS.get(item, item) for item in memory.error_types]
        lines.append("- 近 7 天主要错因：" + "、".join(labels))
    if "forgotten" in fields and memory.forgotten_words:
        lines.append("- 反复忘记：" + "、".join(memory.forgotten_words))
    if "misspelled" in fields and memory.misspelled_words:
        lines.append("- 拼写薄弱：" + "、".join(memory.misspelled_words))
    if "writing" in fields and memory.writing_error_types:
        labels = [WRITING_ERROR_LABELS.get(item, item) for item in memory.writing_error_types[:3]]
        lines.append("- 英文写作薄弱：" + "、".join(labels))
    if "backlog" in fields and memory.due_backlog > 0:
        lines.append(f"- 到期未复习：{memory.due_backlog} 个词")
    if "focus" in fields and memory.focus:
        lines.append("- 上周计划重点：" + "、".join(_clip(item, 20) for item in memory.focus[:4]))

    if len(lines) == 1:
        return ""
    return _clip("\n".join(lines), MAX_MEMORY_CHARS)


# ---------------------------------------------------------------- 摸底测评
ASSESSMENT_SYSTEM = (
    "你是一名严谨的英语水平评估专家。你会根据用户的客观题作答和英译中表现，"
    "判断其 CEFR 等级（A2/B1/B2/C1）。你只输出 JSON 对象，"
    "不输出解释、Markdown 标题或代码围栏。"
)


def build_assessment_messages(
    assessment_set: AssessmentSet,
    sheet: AnswerSheet,
    score: ObjectiveScore,
) -> list[dict[str, str]]:
    """把题目、标准答案、用户答案和翻译打包成评分请求。"""
    parts: list[str] = [
        "请评估下面这份英语测评答卷。",
        f"【起点参考】这套题按 {assessment_set.level_hint} 难度设计；"
        "如果证据不足，就以该等级为准。",
        f"【客观题得分】{score.summary()}",
        _format_questions(
            "【阅读理解】",
            assessment_set.comprehension,
            sheet.comprehension,
            sheet.comprehension_notes,
        ),
        _format_questions(
            "【词汇】",
            assessment_set.vocabulary,
            sheet.vocabulary,
            sheet.vocabulary_notes,
        ),
        f"【翻译题】题目：{assessment_set.translation_prompt}",
        f"【用户译文】{sheet.translation.strip() or '（未作答）'}",
        (
            "说明：用户答案里的“不知道”表示用户主动承认不会，按答错处理；"
            "“未作答”表示题目被跳过。“用户补充”是用户对某道题的疑问或自述"
            "（可能是中文），只能作为判断薄弱点、区分“词汇不认识”和"
            "“题意没读懂”的参考，不要把它当成答案。"
        ),
        (
            "说明：翻译题是英译中，重点看是否准确理解原句的关键词、指代与逻辑关系，"
            "不要求英文写作；中文表达稍生硬不影响评级。\n"
            "请综合客观题正确率与英译中的理解准确度给出评级，并输出 JSON：\n"
            "{\n"
            '  "cefr": "B1",\n'
            '  "vocab_estimate": 3800,\n'
            '  "strengths": ["阅读理解", "基础语法"],\n'
            '  "weaknesses": ["学术词汇", "长难句"],\n'
            '  "comment": "整体 B1 偏上，建议先扩大阅读量。"\n'
            "}\n"
            "要求：cefr 只能是 A2/B1/B2/C1；vocab_estimate 为整数；"
            "strengths/weaknesses 用中文短语，各 1~3 条；comment 不超过 80 字。"
        ),
    ]
    return [
        {"role": "system", "content": ASSESSMENT_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _format_questions(
    title: str,
    questions: tuple[Question, ...],
    answers: tuple[int, ...],
    notes: tuple[str, ...] = (),
) -> str:
    lines = [title]
    for index, question in enumerate(questions):
        lines.append(f"{index + 1}. {question.prompt}")
        for option_index, option in enumerate(question.options):
            lines.append(f"   {OPTION_LETTERS[option_index]}. {option}")
        chosen = answers[index] if index < len(answers) else -1
        lines.append(f"   标准答案：{question.letter()}；用户答案：{question.answer_text(chosen)}")
        note = notes[index].strip() if index < len(notes) else ""
        if note:
            lines.append(f"   用户补充：{note}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 每日文章
ARTICLE_SYSTEM = (
    "你是一位为中文母语者撰写分级英语短文的作者。文章要有信息量、语言自然，"
    "不写说教式励志文。你只输出 JSON 对象，不输出解释或代码围栏。"
)

ARTICLE_WORD_MIN = 250
ARTICLE_WORD_MAX = 350
ARTICLE_PARAGRAPH_MIN = 3


def build_article_messages(
    level: str,
    target_words: list[str],
    topic: str,
    *,
    word_min: int = ARTICLE_WORD_MIN,
    word_max: int = ARTICLE_WORD_MAX,
    paragraph_min: int = ARTICLE_PARAGRAPH_MIN,
    within_level: str = "",
    soft_targets: bool = False,
    learner_feedback: str = "",
    learner_focus: str = "",
    learner_memory: str = "",
    learner_plan: str = "",
    angle: str = "",
    avoid_titles: list[str] | tuple[str, ...] = (),
    user_request: str = "",
    current_article: Article | None = None,
    repair_note: str = "",
) -> list[dict[str, str]]:
    """每日文章生成请求。target_words 来自生词本里到期的词。

    soft_targets=True 用于“按阅读反馈重写文章”：目标是让文章更流畅、更适合
    用户当前状态，生词能自然带上最好，带不上不强制。
    learner_feedback 非空时，把上一篇的阅读感受也告诉模型，让它控制生词密度。
    learner_focus 是画像里的薄弱点，作为软提示，不要求生硬覆盖；
    learner_memory 非空时（M5.2 学习者记忆）优先使用它，调用方不再传 focus。
    learner_plan 是上周复盘给出的重点，作为本周选词与内容的软约束。
    angle 是随机的切入角度；avoid_titles 是已经写过的标题，用来防止“换一篇”
    时模型重复同一个选题（例如反复写丝绸之路）。
    user_request 是用户在“换一篇”弹窗里写的要求，优先于默认主题。
    current_article 只在“用户写了换一篇要求”时传入：让模型理解“这篇的风格 /
    这个话题 / 这段结尾”之类指代；没有用户要求时不传，免得模型被原文带偏。
    repair_note 是上一次校验失败的原因，重试时喂回模型，避免盲目重抽。
    """
    if target_words and soft_targets:
        target_rule = (
            "3. 下面是学习者今天标注的生词，请尽量自然地用上：能自然用几个就用几个，"
            "数量不限，**绝不要为了塞词牺牲句子的流畅和地道**：\n"
            f"   {', '.join(target_words)}\n"
            "4. 再从正文里挑 1~3 个对学习者有价值的进阶词，放进 target_words；\n"
        )
    elif target_words:
        target_rule = (
            "3. 必须自然复现下列目标词，每个最多出现 2 次，不要堆砌：\n"
            f"   {', '.join(target_words)}\n"
            "4. 再从正文里挑 1~3 个对学习者有价值的进阶词，一并放进 target_words；\n"
        )
    else:
        target_rule = "3. 从正文里挑 3 个对学习者有价值的进阶词，放进 target_words；\n"

    if soft_targets:
        target_check = (
            "另外：文章通顺自然优先；target_words 只统计正文里实际出现过的词，不要硬塞；\n"
        )
    else:
        target_check = (
            "另外：target_words 里的每个词都必须真实出现在正文中，"
            "可以使用常见词形变化（复数、过去式、派生词等）；\n"
        )

    # 同一篇文章顺带产出“难词表”：文章落库后直接写成查词缓存，
    # 阅读时双击这些词不再等 AI。只要求核心释义，例句/记忆法留给后续按需生成。
    glossary_rule = (
        "5. glossary 从正文里挑 8~15 个对 CEFR "
        f"{level} 学习者可能陌生的词（不要专有名词），word 用正文中的形态，"
        "并给出它在这篇文章语境下的中文释义；只给词、词性、音标和释义，"
        "不要例句和记忆方法；\n"
    )

    feedback_rule = ""
    if learner_feedback.strip():
        feedback_rule = (
            f"补充要求：学习者对上一篇的反馈是“{learner_feedback.strip()[:200]}”。\n"
            "请据此调整：优先使用高频常见词、缩短句子、控制生词密度，"
            f"但整体等级仍保持 CEFR {level}。\n"
        )

    # M6.5：档内位置。空串时完全不加这一段，保证 prompt 与旧版逐字节一致。
    within_rule = ""
    normalized_level = level.strip().upper()
    level_index = CEFR_LEVELS.index(normalized_level) if normalized_level in CEFR_LEVELS else None
    if within_level == "low":
        within_rule = (
            f"补充要求：在 CEFR {level} 内偏易——以高频词为主，平均句长靠近本档下限，"
            "避免抽象概念和复杂从句。\n"
        )
    elif within_level == "high" and level_index is not None:
        if level_index + 1 < len(CEFR_LEVELS):
            higher_level = CEFR_LEVELS[level_index + 1]
            within_rule = (
                f"补充要求：在 CEFR {level} 内偏难——可含 1~2 个 {higher_level} 词，"
                f"句长靠近本档上限，但语法与抽象度不得越过 {higher_level}。\n"
            )
        else:
            # C1 没有更高一档：只要求句式与论证更复杂，不引入超纲词汇。
            within_rule = (
                f"补充要求：在 CEFR {level} 内偏难——句式与论证可以更复杂，"
                f"但不要使用超出 {level} 范围的生僻词。\n"
            )

    focus_rule = ""
    if learner_focus.strip():
        focus_rule = (
            f"补充要求：学习者近期的薄弱点是“{learner_focus.strip()[:160]}”。"
            "如果与当前主题自然契合，可以适当覆盖这些点；不要为了覆盖而牺牲可读性。\n"
        )

    memory_rule = ""
    if learner_memory.strip():
        memory_rule = (
            f"{learner_memory.strip()[:MAX_MEMORY_CHARS]}\n"
            "以上是学习者的长期记忆：写作时如果与主题自然契合，优先覆盖这些薄弱点；"
            "不要为了覆盖而牺牲可读性。\n"
        )

    plan_rule = ""
    if learner_plan.strip():
        plan_rule = (
            f"补充要求：上周复盘给学习者定的重点是“{learner_plan.strip()[:160]}”。"
            "请把它们作为本周选词与内容的软约束，能自然覆盖就覆盖，不要求生硬照做。\n"
        )

    repair_rule = ""
    if repair_note.strip():
        repair_rule = (
            f"上一次生成未通过本地校验：{repair_note.strip()[:300]}。"
            "请针对这个问题修正后重新输出完整 JSON，不要解释原因。\n"
        )

    subject = topic.strip() or "daily life"
    request = user_request.strip()[:300]
    if request:
        # 用户明确点了要求，就不再强加默认主题，避免两条指令互相打架。
        topic_line = (
            f"请写一篇 CEFR {level} 水平的英语短文。\n"
            f"学习者这次的要求：{request}\n"
            "请严格按这个要求选题；如果要求里没有提到难度和长度，按下面的规则执行。\n"
        )
    else:
        topic_line = f"请写一篇 CEFR {level} 水平的英语短文，主题：{subject}。\n"

    angle_line = ""
    if angle.strip():
        angle_line = (
            f"本次的切入角度：{angle.strip()}。请围绕这个角度选一个具体、有新意的选题，"
            "不要泛泛而谈。\n"
        )

    avoid_line = ""
    titles = [title.strip() for title in avoid_titles if title and title.strip()][:8]
    if titles:
        avoid_line = (
            "下面这些文章已经写过了，请换一个全新的具体选题；"
            "不要重复它们的主题、事件或叙事角度，也不要换个说法继续写同一个题材"
            "（比如同一段历史、同一个地点、同一个物件）：\n"
            + "\n".join(f"- {title}" for title in titles)
            + "\n"
        )

    # 只有用户明确写了要求时才附带当前文章，帮他理解“这篇 / 这个话题”等指代。
    current_line = ""
    if current_article is not None and request:
        content = current_article.content.strip()
        if len(content) > 4000:
            content = content[:4000] + "…"
        current_line = (
            "【当前文章（仅作参考）】\n"
            f"标题：{current_article.title}\n"
            f"等级：{current_article.level}；约 {current_article.word_count} 词；"
            f"主题：{current_article.topic or '未标注'}\n"
            f"正文：\n{content}\n\n"
            "学习者是在读完这篇文章后提出上面的换一篇要求的。"
            "请理解要求里的指代（例如“这个话题”“这种结构”“类似的难度”），"
            "写一篇新的文章；不要逐句改写、照抄或只替换几个词。\n\n"
        )

    user = (
        f"{topic_line}"
        f"{angle_line}"
        f"{avoid_line}"
        f"{current_line}"
        "要求：\n"
        f"1. 分 {paragraph_min}~4 段，全文 {word_min}~{word_max} 词；\n"
        "2. 句子长度和词汇符合该等级，内容完整、有开头和结尾；\n"
        f"{target_rule}"
        f"{target_check}"
        f"{glossary_rule}"
        f"{within_rule}"
        f"{feedback_rule}"
        f"{focus_rule}"
        f"{memory_rule}"
        f"{plan_rule}"
        f"{repair_rule}"
        "最后：只输出 JSON，不要解释、不要代码围栏。\n\n"
        "JSON 结构：\n"
        "{\n"
        '  "title": "The Quiet Power of Small Habits",\n'
        f'  "level": "{level}",\n'
        f'  "topic": "{subject}",\n'
        '  "paragraphs": ["第一段", "第二段", "第三段"],\n'
        '  "target_words": ["resilient", "consistent"],\n'
        '  "glossary": [\n'
        '    {"word": "resilient", "lemma": "resilient", "pos": "adj.", '
        '"phonetic": "/rɪˈzɪliənt/", "meaning_cn": "有韧性的"}\n'
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": ARTICLE_SYSTEM},
        {"role": "user", "content": user},
    ]


# ------------------------------------------------------------ 阅读反馈调级
LEVEL_FEEDBACK_SYSTEM = (
    "你是一名经验丰富的英语分级阅读老师。你会根据学习者的阅读感受和当天标注的生词，"
    "重新判断适合他的 CEFR 阅读等级。你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_level_feedback_messages(
    profile: Profile,
    feedback: str,
    words: list[str],
    article: Article | None,
) -> list[dict[str, str]]:
    """根据“这篇读起来怎么样”重新定级；只评级，不出题、不写文章。"""
    if article is not None:
        article_line = f"《{article.title}》（{article.level}，{article.word_count} 词）"
    else:
        article_line = "（今天还没有文章）"

    word_line = "、".join(words) if words else "（今天没有标注生词）"
    current = profile.cefr_level
    if profile.vocab_estimate:
        current += f"，估算词汇量约 {profile.vocab_estimate}"

    user = (
        "学习者读完了今天的文章，给出了下面的阅读感受。"
        "请据此重新判断他/她当前适合的阅读等级。\n\n"
        f"【当前等级】{current}\n"
        f"【今日文章】{article_line}\n"
        f"【今日标注的生词】{word_line}\n"
        f"【阅读感受】{feedback.strip()}\n\n"
        "请只输出 JSON：\n"
        "{\n"
        '  "cefr": "A2",\n'
        '  "vocab_estimate": 3000,\n'
        '  "strengths": ["基本阅读理解"],\n'
        '  "weaknesses": ["词汇量不足"],\n'
        '  "comment": "反馈显示生词偏多，下调到 A2，先建立阅读信心。"\n'
        "}\n"
        "规则：\n"
        "1. cefr 只能是 A2/B1/B2/C1；\n"
        "2. 反馈表达“太难、生词太多、读不下去、很吃力”时，至少下调一档"
        "（B1→A2，B2→B1，C1→B2，A2 保持）；\n"
        "3. 反馈表达“太简单、没挑战”时可以上调一档；表达“刚刚好”时保持原等级；\n"
        "4. 结合今日生词的数量和难度判断；vocab_estimate 给整数；\n"
        "5. strengths/weaknesses 用中文短语，各 1~3 条；"
        "comment 用中文说明调整理由，不超过 80 字；\n"
        "6. 不要重新出题，不要输出文章本身。"
    )
    return [
        {"role": "system", "content": LEVEL_FEEDBACK_SYSTEM},
        {"role": "user", "content": user},
    ]


# ------------------------------------------------------------ 阅读检测（答题）
QUIZ_SYSTEM = (
    "你是一名英语阅读理解老师，擅长根据一篇分级短文出题，既能检查理解，"
    "也能暴露词汇与推理上的薄弱点。你只输出 JSON 对象，不输出解释或代码围栏。"
)

QUIZ_QUESTION_COUNT = 5
QUIZ_SHORT_ANSWER_COUNT = 1

# Phase 3 自适应出题：第一轮 3 道当前等级题，第二轮 2 道边界题 + 2 道产出题。
QUIZ_STAGE_ONE_COUNT = 3
QUIZ_STAGE_TWO_CHOICE_COUNT = 2
QUIZ_PRODUCTION_COUNT = 2


def _quiz_difficulty_plan(level: str) -> tuple[str, str, str, str]:
    """返回（低一档、当前、高一档、高两档），用于混合难度出题。"""
    current = level.strip().upper()
    if current not in CEFR_LEVELS:
        current = "B1"
    index = CEFR_LEVELS.index(current)
    lower = CEFR_LEVELS[max(0, index - 1)]
    higher = CEFR_LEVELS[min(len(CEFR_LEVELS) - 1, index + 1)]
    much_higher = CEFR_LEVELS[min(len(CEFR_LEVELS) - 1, index + 2)]
    return lower, current, higher, much_higher


def build_reading_quiz_messages(
    article: Article,
    level: str,
    question_count: int = QUIZ_QUESTION_COUNT,
) -> list[dict[str, str]]:
    """旧版整卷出题：混合难度选择题 + 1 道英文短答。

    当前自适应流程已改用 `build_quiz_stage_one_messages` /
    `build_quiz_stage_two_messages`；本函数只保留给历史回放与回归测试，
    产出题仍是英文短答，不要在新流程里调用。
    """
    content = article.content.strip()
    if len(content) > 4000:
        content = content[:4000] + "…"

    lower, current, higher, much_higher = _quiz_difficulty_plan(level)
    user = (
        f"请根据下面这篇 CEFR {level} 的英语短文出一套阅读检测题。\n\n"
        f"【标题】{article.title}\n"
        f"【等级】{article.level}\n"
        f"【主题】{article.topic or '未标注'}\n"
        f"【正文】\n{content}\n\n"
        "题目结构（严格遵守）：\n"
        f"1. {question_count} 道单项选择题，每题 4 个选项，只有一个正确答案；\n"
        "2. 选择题的难度按下面的配比混合，不要全部出同一档：\n"
        f"   - 2 题难度 = {current}（当前等级，检查基本理解）；\n"
        f"   - 1 题难度 = {lower}（低一档，排查粗心/基础漏洞）；\n"
        f"   - 1 题难度 = {higher}（高一档，看是否具备向上能力）；\n"
        f"   - 1 题难度 = {much_higher}（高两档，探上限；如果太难可以只考字面理解）；\n"
        f"3. 再加 1 道短答题（kind=short），难度 {current}~{higher}，"
        "要求学习者用英文写 1~2 句概括观点或解释句子，不要出成选择题；\n"
        "4. 选择题题型尽量覆盖：主旨、细节、推断、语境词义；每题给出 skill；\n"
        "5. 题目和选项用英文；答案必须能从文中找到依据，不要考外部常识；\n"
        "6. 干扰项要合理，不要一眼就能排除；\n"
        "7. answer_index 用 0~3 的整数（0=A，1=B，2=C，3=D）；\n"
        "8. explanation 用中文写正确答案依据，40~120 字；短答题的 explanation "
        "写评分要点，reference_answer 给出一个参考英文答案。\n\n"
        "只输出 JSON：\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "kind": "choice",\n'
        '      "prompt": "What is the main idea of the passage?",\n'
        '      "options": ["...", "...", "...", "..."],\n'
        '      "answer_index": 1,\n'
        '      "skill": "main_idea",\n'
        '      "difficulty": "' + current + '",\n'
        '      "explanation": "第一段点明……，后文都在支撑这个观点。"\n'
        "    },\n"
        "    {\n"
        '      "kind": "short",\n'
        '      "prompt": "In 1-2 sentences, summarize the main idea of the passage.",\n'
        '      "skill": "writing",\n'
        '      "difficulty": "' + current + '",\n'
        '      "reference_answer": "A possible answer...",\n'
        '      "explanation": "评分要点：能否抓住……，语法是否基本正确。"\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": QUIZ_SYSTEM},
        {"role": "user", "content": user},
    ]


STAGE_ONE_SYSTEM = (
    "你是一名英语阅读理解老师。你只为当前等级出一组基础题，检查学习者是否真正读懂。"
    "你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_quiz_stage_one_messages(
    article: Article,
    level: str,
    question_count: int = QUIZ_STAGE_ONE_COUNT,
    *,
    weak_skills: Sequence[str] = (),
    repair_note: str = "",
) -> list[dict[str, str]]:
    """第一轮：3 道当前等级选择题，不提前暴露高难度题。

    `weak_skills` 来自最近几次检测的技能正确率，用来让新题优先覆盖薄弱项；
    `repair_note` 是上一次校验失败的原因，重试时喂回模型。
    """
    content = article.content.strip()
    if len(content) > 4000:
        content = content[:4000] + "…"
    current = level.strip().upper()
    if current not in CEFR_LEVELS:
        current = "B1"

    focus_rule = ""
    skills = [skill.strip() for skill in weak_skills if skill and skill.strip()]
    if skills:
        focus_rule = (
            f"9. 优先覆盖这些薄弱技能：{'、'.join(skills[:3])}；"
            "如果与文章内容冲突，以文章本身为准。\n"
        )
    repair_rule = ""
    if repair_note.strip():
        repair_rule = (
            f"上一次出题未通过校验：{repair_note.strip()[:300]}。"
            "请针对这个问题修正后重新输出完整 JSON。\n"
        )

    user = (
        f"请根据下面这篇 CEFR {article.level} 的英语短文出 {question_count} 道选择题。\n\n"
        f"【标题】{article.title}\n"
        f"【主题】{article.topic or '未标注'}\n"
        f"【正文】\n{content}\n\n"
        "题目要求（严格遵守）：\n"
        f"1. 恰好 {question_count} 道单项选择题，每题 4 个选项；\n"
        f"2. 全部题目难度 = {current}（当前等级，检查基本理解）；\n"
        "3. 题型尽量覆盖主旨、细节、推断、语境词义，每题给出 skill；\n"
        "4. 题目和选项用英文；答案必须能从文中找到依据；\n"
        "5. 干扰项要合理，不要一眼排除；\n"
        "6. answer_index 用 0~3 的整数（0=A，1=B，2=C，3=D）；\n"
        "7. explanation 用中文写正确答案依据，40~120 字；\n"
        "8. 不要出短答题，不要出高难度题；\n"
        f"{focus_rule}"
        f"{repair_rule}"
        "\n只输出 JSON：\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "kind": "choice",\n'
        '      "prompt": "...",\n'
        '      "options": ["...", "...", "...", "..."],\n'
        '      "answer_index": 1,\n'
        '      "skill": "detail",\n'
        f'      "difficulty": "{current}",\n'
        '      "explanation": "..."\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": STAGE_ONE_SYSTEM},
        {"role": "user", "content": user},
    ]


STAGE_TWO_SYSTEM = (
    "你是一名英语阅读理解老师，擅长根据学习者的第一轮表现做边界探测。"
    "你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_quiz_stage_two_messages(
    article: Article,
    level: str,
    target_level: str,
    *,
    choice_count: int = QUIZ_STAGE_TWO_CHOICE_COUNT,
    weak_skills: Sequence[str] = (),
    repair_note: str = "",
) -> list[dict[str, str]]:
    """第二轮：2 道边界难度选择题 + 2 道英译中题。

    产出题统一改成「从原文选句子 → 翻译成中文」，只检验阅读理解，
    不再要求学习者用英文写作。`target_level` 由第一轮正确率决定：
    答得好往上一档探测，答得差别往下确认。`weak_skills` 让选择题优先
    覆盖薄弱技能；`repair_note` 用于重试时带回失败原因。
    """
    content = article.content.strip()
    if len(content) > 4000:
        content = content[:4000] + "…"
    current = level.strip().upper()
    if current not in CEFR_LEVELS:
        current = "B1"
    target = target_level.strip().upper()
    if target not in CEFR_LEVELS:
        target = current

    focus_rule = ""
    skills = [skill.strip() for skill in weak_skills if skill and skill.strip()]
    if skills:
        focus_rule = (
            f"6. 优先覆盖这些薄弱技能：{'、'.join(skills[:3])}；"
            "如果与文章内容冲突，以文章本身为准。\n"
        )
    repair_rule = ""
    if repair_note.strip():
        repair_rule = (
            f"上一次出题未通过校验：{repair_note.strip()[:300]}。"
            "请针对这个问题修正后重新输出完整 JSON。\n"
        )

    user = (
        f"学习者刚刚完成了 {QUIZ_STAGE_ONE_COUNT} 道 {current} 难度的基础题。"
        f"现在请根据这篇文章再出 {choice_count} 道边界探测题和 2 道英译中题。\n\n"
        f"【标题】{article.title}\n"
        f"【主题】{article.topic or '未标注'}\n"
        f"【正文】\n{content}\n\n"
        "题目要求（严格遵守）：\n"
        f"1. 恰好 {choice_count} 道单项选择题，难度统一为 {target}，每题 4 个选项；\n"
        "2. 再加 2 道英译中题，都用 kind=short、skill=translation：\n"
        "   - 每道题从文章中选 1 句完整的英文句子，要求学习者翻译成中文；\n"
        "   - 第 1 道选包含关键信息或主旨的句子；第 2 道选包含指代、转折、因果等逻辑关系的句子；\n"
        "   - 每句控制在 12~35 个英文单词，不要选整段，也不要把句子拆得太碎；\n"
        '   - 题面写成 "Translate the following sentence into Chinese: \\"<原文句子>\\""；\n'
        "   - reference_answer 必须给中文参考翻译，不要给英文；\n"
        "   - 不要要求学习者写英文，不要出成选择题；\n"
        f"   - 两道题难度分别标为 {current} 和 {target}（至少落在 {current}~{target} 之间）；\n"
        "3. 选择题要覆盖不同技能，干扰项合理；\n"
        "4. answer_index 用 0~3 的整数；explanation 用中文写依据/评分要点；\n"
        "5. 两道英译中题都要给 reference_answer；不要出成选择题；\n"
        f"{focus_rule}"
        f"{repair_rule}"
        "\n只输出 JSON：\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "kind": "choice",\n'
        '      "prompt": "...",\n'
        '      "options": ["...", "...", "...", "..."],\n'
        '      "answer_index": 2,\n'
        '      "skill": "inference",\n'
        f'      "difficulty": "{target}",\n'
        '      "explanation": "..."\n'
        "    },\n"
        "    {\n"
        '      "kind": "short",\n'
        '      "prompt": "Translate the following sentence into Chinese: '
        '\\"Small habits add up over time.\\"",\n'
        '      "skill": "translation",\n'
        f'      "difficulty": "{current}",\n'
        '      "reference_answer": "小习惯会随着时间的推移不断累积。",\n'
        '      "explanation": "评分要点：add up 译为“累积”，over time 译为“随着时间推移”。"\n'
        "    },\n"
        "    {\n"
        '      "kind": "short",\n'
        '      "prompt": "Translate the following sentence into Chinese: '
        '\\"They change our lives in ways we rarely notice.\\"",\n'
        '      "skill": "translation",\n'
        f'      "difficulty": "{target}",\n'
        '      "reference_answer": "它们以我们很少察觉的方式改变着我们的生活。",\n'
        '      "explanation": "评分要点：in ways 表示方式；rarely 是“很少、几乎不”。"\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": STAGE_TWO_SYSTEM},
        {"role": "user", "content": user},
    ]


QUESTION_REVIEW_SYSTEM = (
    "你是一名严格的英语试题审核员。你会先独立作答，再判断题目是否答案唯一、"
    "干扰项是否合理、是否必须读文章才能作答。你只输出 JSON 对象，"
    "不输出解释或代码围栏。"
)


def build_question_review_messages(
    article: Article,
    questions: Sequence[object],
) -> list[dict[str, str]]:
    """题目审核 pass：独立作答 + 标出不合格题。

    关键设计：**不告诉模型出题人标注的答案**，让它像考生一样独立作答；
    调用方再用本地答案键比对，发现“模型独立作答与标注答案不一致”的题。
    这比只问“答案是否唯一”更容易发现答案键标错。
    审核失败时调用方会保留原题，不能因为审核模型抽风就让整个检测不可用。
    """
    lines: list[str] = []
    for index, question in enumerate(questions):
        prompt = str(getattr(question, "prompt", ""))
        options = getattr(question, "options", ()) or ()
        if options:
            option_lines = "\n".join(
                f"   {chr(ord('A') + position)}. {option}"
                for position, option in enumerate(options)
            )
            lines.append(f"[{index}] 选择题：{prompt}\n{option_lines}")
        else:
            reference = str(getattr(question, "reference_answer", "") or "（未提供）")
            label = "英译中" if getattr(question, "is_translation", False) else "产出题"
            lines.append(f"[{index}] {label}：{prompt}\n   参考要点：{reference}")

    content = article.content.strip()
    if len(content) > 3000:
        content = content[:3000] + "…"
    user = (
        "请审核下面这组阅读题。请先**不看任何标注答案**，像考生一样独立作答，"
        "然后只挑出**明确不合格**的题：\n"
        "- 答案不唯一、两个选项都说得通；\n"
        "- 答案与文章依据矛盾；\n"
        "- 不读文章也能凭常识答对；\n"
        "- 干扰项明显荒谬或与题干无关；\n"
        "- 题目超出文章范围或无法作答。\n\n"
        f"【文章标题】{article.title}\n"
        f"【正文】\n{content}\n\n"
        "【题目】\n" + "\n\n".join(lines) + "\n\n"
        "只输出 JSON：\n"
        '{"answers": [1, 2, null, null], '
        '"invalid_indices": [0], "reasons": ["第 0 题两个选项都可以"]}\n'
        "规则：\n"
        "- answers 与题目顺序一一对应；选择题填你独立作答的选项下标（0~3），"
        "产出题填 null；\n"
        "- invalid_indices 只放你确认不合格的选择题下标；产出题不要放进这里；\n"
        "- 下标从 0 开始；没有问题就返回空数组；不要因为风格偏好判不合格。"
    )
    return [
        {"role": "system", "content": QUESTION_REVIEW_SYSTEM},
        {"role": "user", "content": user},
    ]


QUIZ_GRADING_SYSTEM = (
    "你是一名英语阅读老师。你会根据学习者的答题情况讲评，重点判断英译中是否"
    "真正理解了原文。客观题得分已经由系统算好，不要重新计算。"
    "你只输出 JSON 对象，不输出解释或代码围栏。"
)


@dataclass(frozen=True)
class QuizHistoryEntry:
    """讲评 prompt 用的历史检测摘要（服务层组装，避免 prompt 依赖仓库）。"""

    date: str
    title: str
    level: str
    score: int
    total: int
    choice_correct: int
    choice_total: int
    production_score: int
    production_total: int
    difficulties: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()


def _format_history(entries: Sequence[QuizHistoryEntry]) -> str:
    if not entries:
        return ""
    lines = ["【最近检测历史（新 → 旧）】"]
    for entry in entries[:8]:
        difficulty = "、".join(entry.difficulties[:8]) or "未标注"
        errors = "、".join(item for item in entry.error_types if item and item != "none")
        error_text = f"；错因：{errors}" if errors else ""
        lines.append(
            f"- {entry.date} 《{entry.title}》{entry.level}："
            f"总分 {entry.score}/{entry.total}"
            f"（选择 {entry.choice_correct}/{entry.choice_total}，"
            f"产出 {entry.production_score}/{entry.production_total}）；"
            f"题难度：{difficulty}{error_text}"
        )
    return "\n".join(lines) + "\n\n"


def _format_difficulty_stats(stats: Sequence[object]) -> str:
    items = []
    for stat in stats:
        level = str(getattr(stat, "level", "") or "")
        total = int(getattr(stat, "total", 0) or 0)
        correct = int(getattr(stat, "correct", 0) or 0)
        if not level or not total:
            continue
        items.append(f"{level} {correct}/{total}（{correct * 100 // total}%）")
    if not items:
        return ""
    return "【历史难度正确率】" + "；".join(items) + "\n"


_SKILL_LABELS = {
    "main_idea": "主旨",
    "detail": "细节",
    "inference": "推断",
    "vocabulary": "语境词义",
    "translation": "英译中",
    "writing": "英文短答",
    "rewrite": "句子改写",
    "grammar": "语法",
}


def _format_skill_stats(stats: Sequence[object]) -> str:
    items = []
    for stat in stats:
        skill = str(getattr(stat, "skill", "") or "")
        total = int(getattr(stat, "total", 0) or 0)
        correct = int(getattr(stat, "correct", 0) or 0)
        if not skill or not total:
            continue
        label = _SKILL_LABELS.get(skill, skill)
        items.append(f"{label} {correct}/{total}")
    if not items:
        return ""
    return "【历史技能正确率】" + "；".join(items) + "\n"


_SELF_REPORT_LABELS = {
    "easy": "轻松",
    "ok": "刚好",
    "hard": "吃力",
}


def build_quiz_grading_messages(
    profile: Profile,
    article: Article,
    quiz: ReadingQuiz,
    answers: tuple[int, ...],
    choice_correct: int,
    *,
    short_answer_text: str = "",
    self_report: str = "",
    production_texts: Sequence[str] = (),
    choice_notes: Sequence[str] = (),
    history: Sequence[QuizHistoryEntry] = (),
    difficulty_stats: Sequence[object] = (),
    skill_stats: Sequence[object] = (),
    repair_note: str = "",
) -> list[dict[str, str]]:
    """答题后的讲评请求（讲评模型可以看到当前等级）。

    选择题得分由本地算好（AI 不重算）；每道主动产出题由 AI 给 0/1 分。
    等级建议不在这里做——Phase 3 起由不看当前等级的盲评模型单独给，
    本函数只负责讲评、产出题评分与薄弱点总结。
    `choice_notes` 与选择题一一对应：选「不知道」时用户写的补充说明，
    prompt 里会作为错因线索交给模型，不改变本地判分。
    `repair_note` 用于“上一次漏了产出题分数”时的定向重试。
    """
    texts = list(production_texts) or [short_answer_text]
    lines: list[str] = []
    choice_position = 0
    production_position = 0
    production_notes: list[str] = []
    for index, question in enumerate(quiz.questions, start=1):
        skill = question.skill or "unknown"
        difficulty = question.difficulty or quiz.level
        if question.is_short:
            answer_text = (
                texts[production_position].strip() if production_position < len(texts) else ""
            )
            production_position += 1
            reference = question.reference_answer or "（未提供）"
            if question.is_translation:
                lines.append(
                    f"第 {index} 题（英译中，难度 {difficulty}，技能 translation）："
                    f"{question.prompt}\n"
                    f"   参考翻译：{reference}\n"
                    f"   学习者译文：{answer_text or '（未作答）'}"
                )
                production_notes.append(
                    f"第 {index} 题（英译中）按「理解准确 / 中文通顺」评分："
                    "准确传达原句意思给 1；关键词义、指代、否定或逻辑关系译错、"
                    "漏译、译反给 0；中文稍生硬不扣分。"
                )
            else:
                kind_label = (
                    "句子改写/翻译"
                    if skill in {"rewrite", "sentence_rewrite", "translation"}
                    else "短答"
                )
                lines.append(
                    f"第 {index} 题（{kind_label}，难度 {difficulty}，技能 {skill}）："
                    f"{question.prompt}\n"
                    f"   参考要点：{reference}\n"
                    f"   学习者作答：{answer_text or '（未作答）'}"
                )
                production_notes.append(
                    f"第 {index} 题（{kind_label}）按「内容 / 语法 / 词汇」三项评分，"
                    "达意且基本正确给 1，否则给 0。"
                )
            continue

        chosen = answers[choice_position] if choice_position < len(answers) else -1
        note = choice_notes[choice_position].strip() if choice_position < len(choice_notes) else ""
        choice_position += 1
        option_lines = "\n".join(
            f"   {chr(ord('A') + position)}. {option}"
            for position, option in enumerate(question.options)
        )
        answer_text = (
            f"{UNKNOWN_LABEL}（主动表示不会）"
            if chosen == UNKNOWN_ANSWER
            else question.letter(chosen)
        )
        note_line = f"\n   补充说明：{note}" if note else ""
        lines.append(
            f"第 {index} 题（选择，难度 {difficulty}，技能 {skill}）：{question.prompt}\n"
            f"{option_lines}\n"
            f"   正确答案：{question.letter(question.answer_index)}；"
            f"学习者答案：{answer_text}{note_line}"
        )

    current = profile.cefr_level
    if profile.vocab_estimate:
        current += f"，估算词汇量约 {profile.vocab_estimate}"
    report = _SELF_REPORT_LABELS.get(self_report, self_report or "未填写")
    production_total = quiz.short_total
    production_score_example = ", ".join("1" for _ in range(max(1, production_total)))
    production_feedback_example = ", ".join(
        f'"第 {index} 道产出题的反馈"' for index in range(1, max(1, production_total) + 1)
    )
    repair_rule = ""
    if repair_note.strip():
        repair_rule = (
            f"\n\n上一次输出有问题：{repair_note.strip()[:300]}。"
            "请严格按上面的 JSON 结构重新输出完整结果。"
        )

    rubric_block = (
        "【产出题评分口径】\n" + "\n".join(production_notes) + "\n\n" if production_notes else ""
    )
    user = (
        "学习者读完了下面这篇文章，并完成了一套阅读检测"
        "（自适应选择题 + 英译中题）。\n\n"
        f"【当前等级】{current}\n"
        f"【文章】《{article.title}》（{article.level}，{article.word_count} 词）\n"
        f"【选择题本地得分】{choice_correct}/{quiz.choice_total}\n"
        f"【主动产出题数量】{production_total}\n"
        f"【学习者自评】这篇读起来：{report}\n\n"
        + _format_history(history)
        + _format_difficulty_stats(difficulty_stats)
        + _format_skill_stats(skill_stats)
        + "【题目与作答】\n"
        + "\n\n".join(lines)
        + "\n\n"
        + rubric_block
        + "请完成三件事：\n"
        "1. 逐题用中文讲评，重点解释答错的题；\n"
        "2. 给每道主动产出题打 0 或 1 分，并给一句中文反馈；\n"
        "3. 总结优势与薄弱点（不判断等级，等级由另一次盲评 + 本地后验决定）。\n\n"
        "评分规则：\n"
        "- 选择题得分已由系统算好，不要重新计算；\n"
        f"- 学习者选「{UNKNOWN_LABEL}」表示主动承认不会，按答错处理，"
        "不要当成跳过或未作答；如果附了补充说明，讲评与错因要参考它"
        "（例如单词不认识 → vocabulary，句子结构看不懂 → grammar，"
        "没找到依据 → detail / inference）；\n"
        "- 英译中题按「理解准确 / 中文通顺」评分：准确传达原句意思给 1；"
        "关键词义、指代、否定或逻辑关系译错、漏译、译反给 0；"
        "不要求逐字直译，也不因中文文采或个别措辞扣分；\n"
        "- 旧题里的短答/改写（如果出现）仍按「内容 / 语法 / 词汇」评分："
        "意思达意、语法基本正确给 1；\n"
        "- 未作答或完全跑题给 0；\n"
        "- 历史正确率只用于解释趋势，不要因为历史分数直接下结论。\n\n"
        "只输出 JSON：\n"
        "{\n"
        '  "vocab_estimate": 3900,\n'
        '  "strengths": ["细节理解"],\n'
        '  "weaknesses": ["推断题", "英译中关键词"],\n'
        '  "comment": "选择题 4/5，英译中基本准确，但推断题不稳定。",\n'
        '  "explanations": ["第 1 题：……", "第 2 题：……"],\n'
        '  "error_types": ["none", "vocabulary", "..."],\n'
        f'  "production_scores": [{production_score_example}],\n'
        f'  "production_feedbacks": [{production_feedback_example}]\n'
        "}\n\n"
        "要求：\n"
        "- explanations 与题目顺序一一对应，每题一条（共 " + str(len(quiz.questions)) + " 条）；\n"
        "- error_types 与题目顺序一一对应，选择题用 "
        "none/vocabulary/inference/detail/main_idea/grammar/careless/unknown，"
        "英译中用 translation，旧短答/改写用 short_answer/rewrite；\n"
        "- production_scores 与产出题顺序一一对应（共 "
        + str(production_total)
        + " 条，每条 0 或 1）；\n"
        "- strengths/weaknesses 各 1~3 条中文短语；comment 不超过 100 字。" + repair_rule
    )
    return [
        {"role": "system", "content": QUIZ_GRADING_SYSTEM},
        {"role": "user", "content": user},
    ]


BLIND_LEVEL_SYSTEM = (
    "你是一名独立的英语水平评估员。你**不会**看到学习者的当前等级，"
    "只根据文章难度、题目难度和作答表现给出独立的 CEFR 判断。"
    "你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_blind_level_messages(
    article: Article,
    quiz: ReadingQuiz,
    answers: tuple[int, ...],
    choice_correct: int,
    production_texts: Sequence[str] = (),
    choice_notes: Sequence[str] = (),
) -> list[dict[str, str]]:
    """盲评：不告诉模型当前等级，避免它被画像锚定。

    返回的 `cefr` 只会作为一条弱观测进入本地后验，不能直接改等级。
    """
    lines: list[str] = []
    choice_position = 0
    production_position = 0
    for index, question in enumerate(quiz.questions, start=1):
        if question.is_short:
            text = (
                production_texts[production_position].strip()
                if production_position < len(production_texts)
                else ""
            )
            production_position += 1
            label = "英译中" if question.is_translation else "产出题"
            answer_label = "学习者译文" if question.is_translation else "学习者作答"
            lines.append(
                f"第 {index} 题（{label}）：{question.prompt}\n"
                f"   {answer_label}：{text or '（未作答）'}"
            )
            continue
        chosen = answers[choice_position] if choice_position < len(answers) else -1
        note = choice_notes[choice_position].strip() if choice_position < len(choice_notes) else ""
        choice_position += 1
        answer_text = (
            f"{UNKNOWN_LABEL}（主动表示不会）"
            if chosen == UNKNOWN_ANSWER
            else question.letter(chosen)
        )
        note_line = f"\n   补充说明：{note}" if note else ""
        lines.append(
            f"第 {index} 题（选择）：{question.prompt}\n"
            f"   正确答案：{question.letter(question.answer_index)}；"
            f"学习者答案：{answer_text}{note_line}"
        )

    content = article.content.strip()
    if len(content) > 4000:
        content = content[:4000] + "…"
    user = (
        "请根据下面这篇文章与学习者作答，独立判断这篇材料对学习者来说属于什么难度，"
        "以及学习者当前大概处于哪个 CEFR 阅读等级。\n"
        "注意：你不会看到学习者的当前等级；不要猜测系统画像，只根据证据判断。\n\n"
        f"【文章标题】{article.title}\n"
        f"【文章正文】\n{content}\n\n"
        f"【选择题得分】{choice_correct}/{len(quiz.choice_questions)}\n"
        "【题目与作答】\n" + "\n\n".join(lines) + "\n\n"
        "只输出 JSON：\n"
        '{"cefr": "B1", "reason": "选择题在高难度题上表现稳定，但推断题有失误。"}\n'
        "规则：cefr 只能是 A2/B1/B2/C1；reason 用中文，不超过 80 字；"
        "证据不足时优先给中间档（B1）。"
    )
    return [
        {"role": "system", "content": BLIND_LEVEL_SYSTEM},
        {"role": "user", "content": user},
    ]


WEEKLY_REVIEW_SYSTEM = (
    "你是一名英语学习教练。你会根据学习者一周的阅读检测、生词和复习数据，"
    "给出简短复盘与下周建议。你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_weekly_review_messages(
    profile: Profile,
    *,
    week_start: str,
    week_end: str,
    quizzes: Sequence[QuizHistoryEntry] = (),
    difficulty_stats: Sequence[object] = (),
    skill_stats: Sequence[object] = (),
    band_review_stats: Sequence[BandReviewStat] = (),
    words_added: int = 0,
    reviews_done: int = 0,
    articles_read: int = 0,
    previous_focus: Sequence[str] = (),
) -> list[dict[str, str]]:
    """每周复盘：汇总一周数据，输出总结、建议等级与下周重点。

    `previous_focus` 是上周复盘给出的重点（M5.2 计划回流）：
    有值时要求模型在 summary 里交代计划完成度，再据此调整本周 focus。
    """
    band_lines = []
    for stat in band_review_stats:
        if not stat.band or not stat.total:
            continue
        band_lines.append(
            f"- {stat.band} 词档：复习 {stat.total} 次，认识率 {stat.accuracy * 100:.0f}%"
        )
    band_text = "\n".join(band_lines) if band_lines else "- 还没有带词档的复习记录"

    current = profile.cefr_level
    if profile.vocab_estimate:
        current += f"，估算词汇量约 {profile.vocab_estimate}"

    plan_block = ""
    plan_items = [_clip(item, 40) for item in previous_focus if item and item.strip()][:4]
    if plan_items:
        plan_block = (
            f"【上周计划重点】{'、'.join(plan_items)}\n"
            "请在 summary 里说明上周计划完成度（哪些做到了、哪些没做到），"
            "并据此调整本周 focus；不要把没完成的计划直接删掉。\n"
        )

    user = (
        f"请复盘学习者 {week_start} ~ {week_end} 这一周的学习情况。\n\n"
        f"【当前等级】{current}\n"
        f"【本周数据】阅读 {articles_read} 篇；新增生词 {words_added} 个；"
        f"复习 {reviews_done} 次；完成阅读检测 {len(quizzes)} 次。\n"
        f"【本周检测】\n{_format_history(quizzes) or '（本周没有完成检测）\n'}"
        f"{_format_difficulty_stats(difficulty_stats)}"
        f"{_format_skill_stats(skill_stats)}"
        f"【生词复习】\n{band_text}\n\n"
        f"{plan_block}"
        "请输出 JSON：\n"
        "{\n"
        '  "summary": "本周阅读稳定性提升，推断题仍是主要失分点。",\n'
        '  "suggested_level": "B1",\n'
        '  "focus": ["推断题", "B2 高频词"],\n'
        '  "confidence": 0.7\n'
        "}\n"
        "规则：\n"
        "- summary 用中文，80~150 字，必须引用本周数据；\n"
        "- suggested_level 只能是 A2/B1/B2/C1，最多比当前等级高/低一档；\n"
        "- 证据不足时保持当前等级；\n"
        "- focus 给 2~4 条具体建议；confidence 是 0~1 的小数。"
    )
    return [
        {"role": "system", "content": WEEKLY_REVIEW_SYSTEM},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 划词释义
DICTIONARY_SYSTEM = (
    "你是一名英汉词典编纂者，擅长结合上下文给出语境义，并擅长用联想、词根词缀、"
    "谐音和画面感帮助中文母语者记单词。你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_dictionary_messages(
    word: str,
    sentence: str,
    level: str,
    *,
    learner_memory: str = "",
) -> list[dict[str, str]]:
    """查词请求：必须带上下文句子，否则 AI 容易给出不相关的义项。

    `learner_memory` 非空时提醒模型按学习者已暴露的薄弱点调整讲解深度；
    为空时 prompt 与 M5.2 之前逐字节一致。
    """
    memory_block = ""
    if learner_memory.strip():
        memory_block = (
            f"{learner_memory.strip()[:MAX_MEMORY_CHARS]}\n"
            "讲解时使用学习者当前等级能理解的词汇，必要时点出他容易混淆的地方。\n"
        )
    user = (
        f"单词（文章中出现的形态）：{word}\n"
        f"所在句子：{sentence or '（无上下文）'}\n"
        f"学习者等级：{level}\n"
        f"{memory_block}\n"
        "请给出该词在这个句子中的语境义，并给出一个方便记忆的方法，面向中文母语者。\n"
        "JSON 结构：\n"
        "{\n"
        '  "lemma": "go",\n'
        '  "display_form": "went",\n'
        '  "phonetic": "/ɡoʊ/",\n'
        '  "pos": "v.",\n'
        '  "meaning_cn": "去；走（go 的过去式）",\n'
        '  "meaning_en": "to move or travel to a place (past tense of go)",\n'
        '  "example_en": "She went home after work.",\n'
        '  "example_cn": "她下班后回家了。",\n'
        '  "cefr_band": "A2",\n'
        '  "mnemonic": "go-went-gone：想象一个人出门（go）又走远了（went）"\n'
        "}\n"
        "要求：\n"
        "1. lemma 必须是词典原形（went→go、studies→study、better→good），"
        "display_form 保留原文中的形态；\n"
        "2. phonetic 用国际音标并带斜杠，按原形标注；meaning_cn 简洁，只写本句语境下的中文义项；\n"
        "3. 词形说明只在 display_form 与 lemma 不同时写：在 meaning_cn 的义项后用括号补一句"
        "（如 went 写成“去；走（go 的过去式）”）；两者相同（仅大小写不同也算相同）时"
        "meaning_cn 里不要出现“原形”字样"
        "（“原形就是 X”“本句中的 X 就是词典原形”这类说明都不要写）；\n"
        "4. example_en 必须是一个完整英文例句，example_cn 是它的中文翻译；\n"
        "5. cefr_band 是该词在常见词表里的 CEFR 词档，只能是 A2/B1/B2/C1"
        "（比 A2 更基础的词也归到 A2）；\n"
        "6. mnemonic 用中文写 40~80 字，可结合词根词缀、联想、谐音、画面感或一个短场景，"
        "让这个词变得好记；不要空泛地说“多读几遍”。"
    )
    return [
        {"role": "system", "content": DICTIONARY_SYSTEM},
        {"role": "user", "content": user},
    ]


MNEMONIC_SYSTEM = (
    "你是一名擅长词汇记忆法的英语老师，熟悉词根词缀、联想、谐音、画面感和故事法。"
    "你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_mnemonic_messages(word: Word, level: str) -> list[dict[str, str]]:
    """为已有生词单独生成记忆方法（老词条 / 用户手动点“生成”时）。"""
    form_line = (
        f"（文章中出现的形态：{word.display_form}）"
        if word.display_form and word.display_form != word.lemma
        else ""
    )
    user = (
        f"单词：{word.lemma}{form_line}\n"
        f"词性：{word.pos or '（未知）'}\n"
        f"中文释义：{word.meaning_cn or '（无）'}\n"
        f"英文释义：{word.meaning_en or '（无）'}\n"
        f"例句：{word.example_en or '（无）'}\n"
        f"原文句子：{word.context_sentence or '（无）'}\n"
        f"学习者等级：{level}\n\n"
        "请给这个单词设计一个中文记忆方法，要求具体、有画面感、能直接照着回忆，"
        "不要空泛地说“多读几遍”。\n"
        "只输出 JSON：\n"
        '{"mnemonic": "用词根词缀 / 联想 / 谐音 / 画面感写成 40~80 字的中文记忆方法"}'
    )
    return [
        {"role": "system", "content": MNEMONIC_SYSTEM},
        {"role": "user", "content": user},
    ]


PREFETCH_SYSTEM = (
    "你是一名英语词典编辑，擅长根据上下文判断词义。你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_prefetch_messages(entries: list[tuple[str, str]], level: str) -> list[dict[str, str]]:
    """批量预查释义（阅读页后台预取用）。

    `entries` 是 (单词, 所在句子) 列表。刻意只要核心字段、不要例句和记忆方法：
    输出短、往返快，这些释义只作为查词缓存写入 `words` 表，用户双击时直接命中；
    如果语境义不符，界面上的“按本句重查”仍可发起完整查词。
    """
    lines: list[str] = []
    for index, (word, sentence) in enumerate(entries, start=1):
        clean = " ".join((sentence or "").split())
        if len(clean) > 240:
            clean = clean[:240] + "…"
        lines.append(f'{index}. word: "{word}" | sentence: "{clean}"')

    user = (
        f"学习者 CEFR 等级：{level}。下面是一篇文章里的单词及其所在句子。\n"
        "请为每个单词给出它在该句中的语境义，用于本地查词缓存。\n"
        "要求：\n"
        "1. word 原样返回输入里的写法；lemma 给词典原形（went→go、studies→study）；\n"
        "2. phonetic 用国际音标并带斜杠，按原形标注；pos 用 v./n./adj. 等缩写；\n"
        "3. meaning_cn 不超过 20 字，meaning_en 一句话；\n"
        "4. 输出条目数量与输入完全一致，顺序不变；不要例句、不要记忆方法；\n"
        "5. 只输出 JSON，不要解释、不要代码围栏。\n\n"
        "JSON 结构：\n"
        '{"entries": [{"word": "...", "lemma": "...", "phonetic": "/.../", '
        '"pos": "...", "meaning_cn": "...", "meaning_en": "..."}]}\n\n'
        f"单词列表：\n" + "\n".join(lines)
    )
    return [
        {"role": "system", "content": PREFETCH_SYSTEM},
        {"role": "user", "content": user},
    ]


REVIEW_HELP_SYSTEM = (
    "你是一名耐心、专业的英语词汇教练。学习者复习时忘记或记不清某个词，"
    "你要先理解他卡在哪里，再给出针对性的记忆帮助。你只输出 JSON 对象，"
    "不输出解释或代码围栏。"
)


def build_review_help_messages(
    word: Word,
    rating: int,
    reason: str,
    level: str,
    *,
    learner_memory: str = "",
) -> list[dict[str, str]]:
    """复习时点“忘记/模糊”后的求助请求：结合用户说明给具体建议。

    `learner_memory` 非空时带上他反复忘记 / 拼错的词与上周计划，
    让建议能连到已有学习轨迹；为空时 prompt 与 M5.2 之前逐字节一致。
    """
    rating_label = "完全忘记" if rating == 1 else "有点模糊、不确定"
    form_line = (
        f"（文章中出现的形态：{word.display_form}）"
        if word.display_form and word.display_form != word.lemma
        else ""
    )
    reason_text = reason.strip()[:300] or "（用户没有填写具体原因）"
    memory_block = ""
    if learner_memory.strip():
        memory_block = (
            f"\n{learner_memory.strip()[:MAX_MEMORY_CHARS]}\n"
            "如果这个词与上面的薄弱点有关，请顺带点明联系；不要泛泛重复。\n"
        )
    user = (
        f"学习者在复习时选择了「{rating_label}」。\n"
        f"【单词】{word.lemma}{form_line}\n"
        f"【词性】{word.pos or '（未知）'}\n"
        f"【中文释义】{word.meaning_cn or '（无）'}\n"
        f"【英文释义】{word.meaning_en or '（无）'}\n"
        f"【例句】{word.example_en or '（无）'}\n"
        f"【原文句子】{word.context_sentence or '（无）'}\n"
        f"【已有记忆方法】{word.mnemonic or '（无）'}\n"
        f"【学习者说明】{reason_text}\n"
        f"【学习者等级】{level}\n"
        f"{memory_block}\n"
        "请针对他卡住的原因给出具体帮助，而不是泛泛地重复释义。\n"
        "只输出 JSON：\n"
        "{\n"
        '  "advice": "针对用户困难的建议，150 字以内。可包括辨析、词根、联想、'
        '一个更贴近生活的新例句等",\n'
        '  "mnemonic": "可以长期保存的记忆方法，40~80 字；如果已有方法已经很好，'
        '可以在其基础上改进"\n'
        "}\n"
        "要求：advice 必须回应学习者的说明；mnemonic 要具体、有画面感，"
        "不要只写“多复习”。"
    )
    return [
        {"role": "system", "content": REVIEW_HELP_SYSTEM},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 回译练习
BACK_TRANSLATION_TRANSLATE_SYSTEM = (
    "你是一名英语教材编辑。你会把英文句子翻译成自然、准确的中文，"
    "供学习者做回译练习（看中文写英文）。你只输出 JSON 对象，"
    "不输出解释或代码围栏。"
)


def build_back_translation_translate_messages(
    sentences: Sequence[str],
    level: str,
    *,
    repair_note: str = "",
) -> list[dict[str, str]]:
    """回译出题：把本地抽出的 3 句翻成中文。

    `sentences` 已经由 `back_translation.pick_sentences()` 在本地选好，
    模型只负责翻译，不参与选题，保证每次练习的句子可复现。
    """
    current = (level or "").strip().upper()
    if current not in CEFR_LEVELS:
        current = "B1"
    numbered = "\n".join(f"{index + 1}. {sentence}" for index, sentence in enumerate(sentences))
    repair_rule = ""
    if repair_note.strip():
        repair_rule = (
            f"上一次译文未通过校验：{repair_note.strip()[:300]}。"
            "请针对这个问题修正后重新输出完整 JSON。\n"
        )
    user = (
        f"学习者的英语等级是 CEFR {current}。请把下面 {len(sentences)} 句英文"
        "翻译成自然、准确的中文。\n\n"
        f"【英文句子】\n{numbered}\n\n"
        "要求：\n"
        "1. 准确传达原句的信息、指代与逻辑关系，不增删内容；\n"
        "2. 中文要自然通顺，不要逐词硬译；\n"
        "3. 不要提示英文关键词、语法点或参考答案，译文只作为回译题面；\n"
        "4. 顺序与输入一致，一句对应一条；\n"
        f"{repair_rule}"
        "\n只输出 JSON：\n"
        '{"translations": ["第一句的中文", "第二句的中文", "第三句的中文"]}'
    )
    return [
        {"role": "system", "content": BACK_TRANSLATION_TRANSLATE_SYSTEM},
        {"role": "user", "content": user},
    ]


BACK_TRANSLATION_GRADE_SYSTEM = (
    "你是一名严谨但鼓励式的英语写作老师。学习者刚做完回译练习：先看中文，"
    "再写出英文。你要对照英文原句批改学习者的英文，给最小修改、原因与 0/2 分。"
    "你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_back_translation_grade_messages(
    items: Sequence[tuple[str, str, str]],
    level: str,
    *,
    repair_note: str = "",
) -> list[dict[str, str]]:
    """回译批改：items = (英文原句, 中文参考, 学习者英文)，一次批量批改。

    评分只有 0 / 2 两档：只作练习反馈，不参与等级与 SRS，
    因此宁可鼓励，也不要因为风格偏好扣分。
    """
    current = (level or "").strip().upper()
    if current not in CEFR_LEVELS:
        current = "B1"
    error_list = "、".join(f"{key}（{WRITING_ERROR_LABELS[key]}）" for key in WRITING_ERROR_TYPES)
    blocks: list[str] = []
    for index, (sentence, reference, answer) in enumerate(items):
        blocks.append(
            f"【第 {index + 1} 句】\n"
            f"英文原句：{sentence}\n"
            f"中文参考（出题时给学习者的）：{reference}\n"
            f"学习者写的英文：{answer}"
        )
    repair_rule = ""
    if repair_note.strip():
        repair_rule = (
            f"上一次批改未通过校验：{repair_note.strip()[:300]}。"
            "请针对这个问题修正后重新输出完整 JSON。\n"
        )
    user = (
        f"学习者等级：CEFR {current}。下面是 {len(items)} 句回译练习，"
        "请逐句对照英文原句批改学习者的英文。\n\n"
        + "\n\n".join(blocks)
        + "\n\n评分口径（严格遵守）：\n"
        "- 2 分：意思与原文一致，语法基本正确；小的拼写 / 标点问题不扣分；\n"
        "- 0 分：漏译、译反、关键信息缺失，或出现影响理解的语法错误；\n"
        "- 只给 0 或 2，不要给 1 分；\n"
        "- edited 给“最小修改后的完整英文句子”：只改必要的地方，"
        "保留学习者原本的表达；完全正确时填原句；\n"
        "- reason 用中文说明最主要的问题（1~2 句）；0 分必须指出关键差异，"
        "2 分可以给一句肯定或小建议；\n"
        "- index 从 0 开始，与上面句子的顺序一一对应；score 必须是 0 或 2；\n"
        f"- error_types 只能从这些值里选：{error_list}；完全正确就给空数组；\n"
        "- 不要因为风格偏好或“不够地道但正确”扣分；\n"
        f"{repair_rule}"
        "\n只输出 JSON：\n"
        "{\n"
        '  "items": [\n'
        '    {"index": 0, "score": 2, "edited": "最小修改后的英文句子", '
        '"reason": "中文原因", "error_types": ["tense"]}\n'
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": BACK_TRANSLATION_GRADE_SYSTEM},
        {"role": "user", "content": user},
    ]
