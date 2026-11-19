"""所有 AI 提示词集中在这里，并带版本号。

为什么要集中：
- 调 prompt 时只改这一个文件，不用在业务代码里翻字符串；
- 版本号写进日志（未来也可以入库），方便对比“同一篇文章是哪个 prompt 生成的”；
- JSON 契约与本地校验（article_service / assessment_service）保持一致，
  改契约时两边必须一起改。

所有 builder 都返回 OpenAI messages 列表（system + user）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models.entities import Article, Profile
from .assessment_bank import OPTION_LETTERS, AssessmentSet, Question

if TYPE_CHECKING:  # 避免与 assessment_service 循环导入
    from .assessment_service import AnswerSheet, ObjectiveScore

PROMPT_VERSION = "m1-4"

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
) -> list[dict[str, str]]:
    """每日文章生成请求。target_words 来自生词本里到期的词。

    soft_targets=True 用于“按阅读反馈重写文章”：目标是让文章更流畅、更适合
    用户当前状态，生词能自然带上最好，带不上不强制。
    learner_feedback 非空时，把上一篇的阅读感受也告诉模型，让它控制生词密度。
    angle 是随机的切入角度；avoid_titles 是已经写过的标题，用来防止“换一篇”
    时模型重复同一个选题（例如反复写丝绸之路）。
    user_request 是用户在“换一篇”弹窗里写的要求，优先于默认主题。
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

    user = (
        f"{topic_line}"
        f"{angle_line}"
        f"{avoid_line}"
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


# ---------------------------------------------------------------- 划词释义
DICTIONARY_SYSTEM = (
    "你是一名英汉词典编纂者，擅长结合上下文给出语境义。你只输出 JSON 对象，不输出解释或代码围栏。"
)


def build_dictionary_messages(word: str, sentence: str, level: str) -> list[dict[str, str]]:
    """查词请求：必须带上下文句子，否则 AI 容易给出不相关的义项。"""
    user = (
        f"单词：{word}\n"
        f"所在句子：{sentence or '（无上下文）'}\n"
        f"学习者等级：{level}\n\n"
        "请给出该词在这个句子中的语境义，面向中文母语者。\n"
        "JSON 结构：\n"
        "{\n"
        '  "lemma": "resilient",\n'
        '  "display_form": "resilient",\n'
        '  "phonetic": "/rɪˈzɪliənt/",\n'
        '  "pos": "adj.",\n'
        '  "meaning_cn": "有韧性的；能迅速恢复的",\n'
        '  "meaning_en": "able to recover quickly from difficulties",\n'
        '  "example_en": "Children are often more resilient than adults expect.",\n'
        '  "example_cn": "孩子往往比大人想象的更有韧性。"\n'
        "}\n"
        "要求：phonetic 用国际音标并带斜杠；meaning_cn 简洁；"
        "example_en 必须是一个完整英文例句，example_cn 是它的中文翻译。"
    )
    return [
        {"role": "system", "content": DICTIONARY_SYSTEM},
        {"role": "user", "content": user},
    ]
