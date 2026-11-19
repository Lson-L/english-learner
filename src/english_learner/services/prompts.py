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
from .assessment_bank import OPTION_LETTERS, AssessmentSet, Question

if TYPE_CHECKING:  # 避免与 assessment_service 循环导入
    from .assessment_service import AnswerSheet, ObjectiveScore

PROMPT_VERSION = "m3-11"

# ---------------------------------------------------------------- 摸底测评
ASSESSMENT_SYSTEM = (
    "你是一名严谨的英语水平评估专家。你会根据用户的客观题作答和翻译表现，"
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
        _format_questions("【阅读理解】", assessment_set.comprehension, sheet.comprehension),
        _format_questions("【词汇】", assessment_set.vocabulary, sheet.vocabulary),
        f"【翻译题】题目：{assessment_set.translation_prompt}",
        f"【用户译文】{sheet.translation.strip() or '（未作答）'}",
        (
            "请综合客观题正确率与翻译质量给出评级，并输出 JSON：\n"
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


def _format_questions(title: str, questions: tuple[Question, ...], answers: tuple[int, ...]) -> str:
    lines = [title]
    for index, question in enumerate(questions):
        lines.append(f"{index + 1}. {question.prompt}")
        for option_index, option in enumerate(question.options):
            lines.append(f"   {OPTION_LETTERS[option_index]}. {option}")
        chosen = answers[index] if index < len(answers) else -1
        lines.append(f"   标准答案：{question.letter()}；用户答案：{question.letter(chosen)}")
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
    soft_targets: bool = False,
    learner_feedback: str = "",
    angle: str = "",
    avoid_titles: list[str] | tuple[str, ...] = (),
    user_request: str = "",
    current_article: Article | None = None,
) -> list[dict[str, str]]:
    """每日文章生成请求。target_words 来自生词本里到期的词。

    soft_targets=True 用于“按阅读反馈重写文章”：目标是让文章更流畅、更适合
    用户当前状态，生词能自然带上最好，带不上不强制。
    learner_feedback 非空时，把上一篇的阅读感受也告诉模型，让它控制生词密度。
    angle 是随机的切入角度；avoid_titles 是已经写过的标题，用来防止“换一篇”
    时模型重复同一个选题（例如反复写丝绸之路）。
    user_request 是用户在“换一篇”弹窗里写的要求，优先于默认主题。
    current_article 只在“用户写了换一篇要求”时传入：让模型理解“这篇的风格 /
    这个话题 / 这段结尾”之类指代；没有用户要求时不传，免得模型被原文带偏。
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
        target_check = "另外：target_words 里的每个词都必须真实出现在正文中（可用不同大小写）；\n"

    feedback_rule = ""
    if learner_feedback.strip():
        feedback_rule = (
            f"补充要求：学习者对上一篇的反馈是“{learner_feedback.strip()[:200]}”。\n"
            "请据此调整：优先使用高频常见词、缩短句子、控制生词密度，"
            f"但整体等级仍保持 CEFR {level}。\n"
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
        f"{feedback_rule}"
        "最后：只输出 JSON，不要解释、不要代码围栏。\n\n"
        "JSON 结构：\n"
        "{\n"
        '  "title": "The Quiet Power of Small Habits",\n'
        f'  "level": "{level}",\n'
        f'  "topic": "{subject}",\n'
        '  "paragraphs": ["第一段", "第二段", "第三段"],\n'
        '  "target_words": ["resilient", "consistent"],\n'
        '  "glossary": [{"word": "resilient", "meaning_cn": "有韧性的"}]\n'
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
    """根据今日文章出混合难度选择题 + 1 道短答题，供“阅读完成”后自测。

    混合难度是第一阶段的核心：同档题看当前水平，低档题排查粗心，
    高档/高两档题探上限；短答题补充“产出能力”信号。
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
) -> list[dict[str, str]]:
    """第一轮：3 道当前等级选择题，不提前暴露高难度题。"""
    content = article.content.strip()
    if len(content) > 4000:
        content = content[:4000] + "…"
    current = level.strip().upper()
    if current not in CEFR_LEVELS:
        current = "B1"

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
        "8. 不要出短答题，不要出高难度题。\n\n"
        "只输出 JSON：\n"
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
) -> list[dict[str, str]]:
    """第二轮：2 道边界难度选择题 + 1 道短答 + 1 道句子改写。

    `target_level` 由第一轮正确率决定：答得好往上一档探测，答得差别往下确认。
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

    user = (
        f"学习者刚刚完成了 {QUIZ_STAGE_ONE_COUNT} 道 {current} 难度的基础题。"
        f"现在请根据这篇文章再出 {choice_count} 道边界探测题和 2 道产出题。\n\n"
        f"【标题】{article.title}\n"
        f"【主题】{article.topic or '未标注'}\n"
        f"【正文】\n{content}\n\n"
        "题目要求（严格遵守）：\n"
        f"1. 恰好 {choice_count} 道单项选择题，难度统一为 {target}，每题 4 个选项；\n"
        "2. 再加 2 道主动产出题，都用 kind=short：\n"
        "   - 第 1 道：英文短答（skill=writing），用 1~2 句概括观点或解释句子，"
        f"难度 {current}~{target}；\n"
        f"   - 第 2 道：句子改写或翻译（skill=rewrite），给出一句原文，要求改写或翻译成英文，"
        f"难度 {current}~{target}；\n"
        "3. 选择题要覆盖不同技能，干扰项合理；\n"
        "4. answer_index 用 0~3 的整数；explanation 用中文写依据/评分要点；\n"
        "5. 两道产出题都要给 reference_answer；不要出成选择题。\n\n"
        "只输出 JSON：\n"
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
        '      "prompt": "In 1-2 sentences, summarize ...",\n'
        '      "skill": "writing",\n'
        f'      "difficulty": "{target}",\n'
        '      "reference_answer": "...",\n'
        '      "explanation": "评分要点：内容 / 语法 / 词汇。"\n'
        "    },\n"
        "    {\n"
        '      "kind": "short",\n'
        '      "prompt": "Rewrite the sentence ... / Translate into English: ...",\n'
        '      "skill": "rewrite",\n'
        f'      "difficulty": "{current}",\n'
        '      "reference_answer": "...",\n'
        '      "explanation": "评分要点：意思是否保留、语法、用词。"\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": STAGE_TWO_SYSTEM},
        {"role": "user", "content": user},
    ]


QUESTION_REVIEW_SYSTEM = (
    "你是一名严格的英语试题审核员。你会检查选择题是否答案唯一、干扰项是否合理、"
    "是否必须读文章才能作答。你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_question_review_messages(
    article: Article,
    questions: Sequence[object],
) -> list[dict[str, str]]:
    """题目审核 pass：返回不合格题目的下标（0 开始）。

    审核失败时调用方会保留原题，不能因为审核模型抽风就让整个检测不可用。
    """
    lines: list[str] = []
    for index, question in enumerate(questions):
        prompt = str(getattr(question, "prompt", ""))
        options = getattr(question, "options", ()) or ()
        answer_index = int(getattr(question, "answer_index", -1))
        if options:
            option_lines = "\n".join(
                f"   {chr(ord('A') + position)}. {option}"
                for position, option in enumerate(options)
            )
            answer = chr(ord("A") + answer_index) if 0 <= answer_index < len(options) else "未提供"
            lines.append(f"[{index}] 选择题：{prompt}\n{option_lines}\n   标注答案：{answer}")
        else:
            reference = str(getattr(question, "reference_answer", "") or "（未提供）")
            lines.append(f"[{index}] 产出题：{prompt}\n   参考要点：{reference}")

    content = article.content.strip()
    if len(content) > 3000:
        content = content[:3000] + "…"
    user = (
        "请审核下面这组阅读题，只挑出**明确不合格**的题：\n"
        "- 答案不唯一、两个选项都说得通；\n"
        "- 答案与文章依据矛盾；\n"
        "- 不读文章也能凭常识答对；\n"
        "- 干扰项明显荒谬或与题干无关；\n"
        "- 题目超出文章范围或无法作答。\n\n"
        f"【文章标题】{article.title}\n"
        f"【正文】\n{content}\n\n"
        "【题目】\n" + "\n\n".join(lines) + "\n\n"
        "只输出 JSON：\n"
        '{"invalid_indices": [0], "reasons": ["第 0 题两个选项都可以"]}\n'
        "规则：下标从 0 开始；没有不合格题时返回空数组；不要因为风格偏好判不合格。"
    )
    return [
        {"role": "system", "content": QUESTION_REVIEW_SYSTEM},
        {"role": "user", "content": user},
    ]


QUIZ_GRADING_SYSTEM = (
    "你是一名英语阅读老师。你会根据学习者的答题情况讲评，并结合文章难度"
    "重新判断他当前适合的 CEFR 等级。客观题得分已经由系统算好，不要重新计算。"
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


def _format_skill_stats(stats: Sequence[object]) -> str:
    items = []
    for stat in stats:
        skill = str(getattr(stat, "skill", "") or "")
        total = int(getattr(stat, "total", 0) or 0)
        correct = int(getattr(stat, "correct", 0) or 0)
        if not skill or not total:
            continue
        items.append(f"{skill} {correct}/{total}")
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
    history: Sequence[QuizHistoryEntry] = (),
    difficulty_stats: Sequence[object] = (),
    skill_stats: Sequence[object] = (),
) -> list[dict[str, str]]:
    """答题后的讲评请求（讲评模型可以看到当前等级）。

    选择题得分由本地算好（AI 不重算）；每道主动产出题由 AI 给 0/1 分。
    等级建议不在这里做——Phase 3 起由不看当前等级的盲评模型单独给，
    本函数只负责讲评、产出题评分与薄弱点总结。
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
            kind_label = "句子改写/翻译" if skill in {"rewrite", "translation"} else "短答"
            reference = question.reference_answer or "（未提供）"
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
        choice_position += 1
        option_lines = "\n".join(
            f"   {chr(ord('A') + position)}. {option}"
            for position, option in enumerate(question.options)
        )
        lines.append(
            f"第 {index} 题（选择，难度 {difficulty}，技能 {skill}）：{question.prompt}\n"
            f"{option_lines}\n"
            f"   正确答案：{question.letter(question.answer_index)}；"
            f"学习者答案：{question.letter(chosen)}"
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

    user = (
        "学习者读完了下面这篇文章，并完成了一套阅读检测"
        "（自适应选择题 + 主动产出题）。\n\n"
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
        "请完成三件事：\n"
        "1. 逐题用中文讲评，重点解释答错的题；\n"
        "2. 给每道主动产出题打 0 或 1 分，并给一句中文反馈；\n"
        "3. 总结优势与薄弱点（不判断等级，等级由另一次盲评 + 本地后验决定）。\n\n"
        "评分规则：\n"
        "- 选择题得分已由系统算好，不要重新计算；\n"
        "- 主动产出题固定按「内容 / 语法 / 词汇」三项看：意思达意、语法基本正确给 1；\n"
        "- 未作答或完全跑题给 0；\n"
        "- 历史正确率只用于解释趋势，不要因为历史分数直接下结论。\n\n"
        "只输出 JSON：\n"
        "{\n"
        '  "vocab_estimate": 3900,\n'
        '  "strengths": ["细节理解"],\n'
        '  "weaknesses": ["推断题", "主动表达"],\n'
        '  "comment": "选择题 4/5，短答基本达意，但推断题不稳定。",\n'
        '  "explanations": ["第 1 题：……", "第 2 题：……"],\n'
        '  "error_types": ["none", "vocabulary", "..."],\n'
        f'  "production_scores": [{production_score_example}],\n'
        f'  "production_feedbacks": [{production_feedback_example}]\n'
        "}\n\n"
        "要求：\n"
        "- explanations 与题目顺序一一对应，每题一条（共 " + str(len(quiz.questions)) + " 条）；\n"
        "- error_types 与题目顺序一一对应，选择题用 "
        "none/vocabulary/inference/detail/main_idea/grammar/careless/unknown，"
        "产出题统一用 short_answer；\n"
        "- production_scores 与产出题顺序一一对应（共 "
        + str(production_total)
        + " 条，每条 0 或 1）；\n"
        "- strengths/weaknesses 各 1~3 条中文短语；comment 不超过 100 字。"
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
            lines.append(
                f"第 {index} 题（产出题）：{question.prompt}\n   学习者作答：{text or '（未作答）'}"
            )
            continue
        chosen = answers[choice_position] if choice_position < len(answers) else -1
        choice_position += 1
        lines.append(
            f"第 {index} 题（选择）：{question.prompt}\n"
            f"   正确答案：{question.letter(question.answer_index)}；"
            f"学习者答案：{question.letter(chosen)}"
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
) -> list[dict[str, str]]:
    """每周复盘：汇总一周数据，输出总结、建议等级与下周重点。"""
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
    user = (
        f"请复盘学习者 {week_start} ~ {week_end} 这一周的学习情况。\n\n"
        f"【当前等级】{current}\n"
        f"【本周数据】阅读 {articles_read} 篇；新增生词 {words_added} 个；"
        f"复习 {reviews_done} 次；完成阅读检测 {len(quizzes)} 次。\n"
        f"【本周检测】\n{_format_history(quizzes) or '（本周没有完成检测）\n'}"
        f"{_format_difficulty_stats(difficulty_stats)}"
        f"{_format_skill_stats(skill_stats)}"
        f"【生词复习】\n{band_text}\n\n"
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


def build_dictionary_messages(word: str, sentence: str, level: str) -> list[dict[str, str]]:
    """查词请求：必须带上下文句子，否则 AI 容易给出不相关的义项。"""
    user = (
        f"单词（文章中出现的形态）：{word}\n"
        f"所在句子：{sentence or '（无上下文）'}\n"
        f"学习者等级：{level}\n\n"
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
        "2. phonetic 用国际音标并带斜杠，按原形标注；meaning_cn 简洁并说明它与原形的关系；\n"
        "3. example_en 必须是一个完整英文例句，example_cn 是它的中文翻译；\n"
        "4. cefr_band 是该词在常见词表里的 CEFR 词档，只能是 A2/B1/B2/C1"
        "（比 A2 更基础的词也归到 A2）；\n"
        "5. mnemonic 用中文写 40~80 字，可结合词根词缀、联想、谐音、画面感或一个短场景，"
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
) -> list[dict[str, str]]:
    """复习时点“忘记/模糊”后的求助请求：结合用户说明给具体建议。"""
    rating_label = "完全忘记" if rating == 1 else "有点模糊、不确定"
    form_line = (
        f"（文章中出现的形态：{word.display_form}）"
        if word.display_form and word.display_form != word.lemma
        else ""
    )
    reason_text = reason.strip()[:300] or "（用户没有填写具体原因）"
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
        f"【学习者等级】{level}\n\n"
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
