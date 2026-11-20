"""摸底测评的固定题库（纯 Python，便于类型检查与单测）。

产品决策：测评不让 AI 自由出题，而是用固定题库 + AI 只负责主观题评分，
这样结果可复现、可比较（见 PRODUCT.md 决策 2）。

三套题分别对应 A2 / B1 / B2 的起点。首次测评固定用 B1 套，
先保证流程稳定；以后要做自适应，再按上次结果挑下一套。
"""

from __future__ import annotations

from dataclasses import dataclass

OPTION_LETTERS = "ABCD"

# 用户在选择题里主动表示“不会”时使用的作答下标。
# 用 -2 而不是 -1：-1 仍保留给“未作答”，两者在 Prompt 里要区分开
# （“不知道”是有效作答，按错误计分；“未作答”说明用户跳过了题）。
UNKNOWN_ANSWER = -2
UNKNOWN_LABEL = "不知道"

DEFAULT_SET_KEY = "b"


@dataclass(frozen=True)
class Question:
    """一道四选一客观题。"""

    prompt: str
    options: tuple[str, ...]
    answer_index: int

    def letter(self, index: int | None = None) -> str:
        """把选项下标转成 A/B/C/D；-1（未作答）返回 “-”。"""
        value = self.answer_index if index is None else index
        if 0 <= value < len(OPTION_LETTERS):
            return OPTION_LETTERS[value]
        return "-"

    def answer_text(self, index: int) -> str:
        """把作答下标转成给 AI 看的描述。

        A/B/C/D 之外还有两种非选项作答：`UNKNOWN_ANSWER` 表示用户主动
        选了“不知道”，其余负数表示未作答。两者对评分的含义不同，不能被
        统一显示成 “-”。
        """
        if index == UNKNOWN_ANSWER:
            return UNKNOWN_LABEL
        if index < 0:
            return "未作答"
        return self.letter(index)


@dataclass(frozen=True)
class AssessmentSet:
    """一整套测评：短文 + 5 道理解题 + 5 道词汇题 + 1 道英译中题。"""

    key: str
    level_hint: str
    passage: str
    comprehension: tuple[Question, ...]
    vocabulary: tuple[Question, ...]
    translation_prompt: str

    @property
    def total_objective(self) -> int:
        return len(self.comprehension) + len(self.vocabulary)


SET_A = AssessmentSet(
    key="a",
    level_hint="A2",
    passage=(
        "Anna works in a small bookshop near the river. Every morning she takes the bus at "
        "seven thirty. On Tuesday it rained very hard. When Anna got off the bus, she found she "
        "had left her umbrella at home. She stood under a shop door and waited. A man with a big "
        "black umbrella walked past, then stopped. 'Do you need help?' he asked. Anna laughed and "
        "said yes. They walked together to the bookshop and talked about books. The man's name "
        "was Peter, and he loved old maps. Before he left, he gave Anna his umbrella. 'Bring it "
        "back when the sun comes out,' he said. Anna smiled all day. The next week, she went to "
        "Peter's map shop with the umbrella and a thank-you card. Peter showed her a map of the "
        "river from 1890. Anna stayed for an hour. Now they meet every Tuesday for coffee. She "
        "likes Tuesdays more than any other day of the week."
    ),
    comprehension=(
        Question(
            prompt="Where does Anna work?",
            options=(
                "In a map shop",
                "In a bookshop near the river",
                "At a bus station",
                "In a coffee shop",
            ),
            answer_index=1,
        ),
        Question(
            prompt="Why did Anna wait under a shop door?",
            options=(
                "Because she was early for work",
                "Because she wanted to buy a card",
                "Because it was raining and she had no umbrella",
                "Because she missed the bus",
            ),
            answer_index=2,
        ),
        Question(
            prompt="What is Peter interested in?",
            options=("Old maps", "Modern art", "Cooking", "Football"),
            answer_index=0,
        ),
        Question(
            prompt="What did Peter give Anna?",
            options=(
                "A book about the river",
                "A map of the city",
                "A cup of coffee",
                "His umbrella",
            ),
            answer_index=3,
        ),
        Question(
            prompt="What do Anna and Peter do every Tuesday now?",
            options=(
                "Take the bus together",
                "Meet for coffee",
                "Visit the river",
                "Read books in the shop",
            ),
            answer_index=1,
        ),
    ),
    vocabulary=(
        Question(
            prompt='An "umbrella" keeps you dry when it is ___.',
            options=("sunny", "raining", "cold", "windy"),
            answer_index=1,
        ),
        Question(
            prompt='"Got off" the bus means ___ the bus.',
            options=("entered", "waited for", "left", "missed"),
            answer_index=2,
        ),
        Question(
            prompt='A "map" shows you ___.',
            options=("the time", "the weather", "how to cook", "where places are"),
            answer_index=3,
        ),
        Question(
            prompt='A "thank-you card" is a card that ___.',
            options=("shows thanks", "sells books", "asks for help", "tells the time"),
            answer_index=0,
        ),
        Question(
            prompt='Anna "laughed" because she felt ___.',
            options=("angry", "tired", "happy", "afraid"),
            answer_index=2,
        ),
    ),
    translation_prompt=(
        "请把下面这句话翻译成中文：When Anna got off the bus, she found she had left "
        "her umbrella at home."
    ),
)

SET_B = AssessmentSet(
    key="b",
    level_hint="B1",
    passage=(
        "Most people believe that big changes require big actions. They wait for the perfect "
        "moment to start a new life: a new job, a new city, a new plan. But research on habits "
        "suggests the opposite. Small, repeated actions are far more powerful than sudden bursts "
        "of effort. Ten minutes of reading every night will not feel impressive, but after a "
        "year it becomes more than sixty hours of practice. A short walk after lunch seems "
        "unimportant, yet it adds up to a healthier body over time. The key is consistency, not "
        "intensity. Small habits are easy to start, easy to repeat, and hard to notice until "
        "they have already changed your life. The secret is to make the habit so small that it "
        "feels almost too easy, and then to protect that moment every day. People who succeed "
        "are not always more talented; they are simply more patient with small steps. In a world "
        "that celebrates speed, patience can feel old-fashioned. But it is still the quiet "
        "engine behind real change."
    ),
    comprehension=(
        Question(
            prompt="What is the main idea of the passage?",
            options=(
                "Big actions are the only way to change your life",
                "Small repeated actions are more powerful than sudden effort",
                "Reading is the best habit for everyone",
                "People should change jobs more often",
            ),
            answer_index=1,
        ),
        Question(
            prompt="According to the passage, ten minutes of reading every night becomes ___ "
            "after a year.",
            options=(
                "less than ten hours",
                "almost no time at all",
                "more than sixty hours",
                "a new job",
            ),
            answer_index=2,
        ),
        Question(
            prompt="The author says the key is consistency, not ___.",
            options=("intensity", "talent", "money", "speed"),
            answer_index=0,
        ),
        Question(
            prompt="What does the author suggest about starting a habit?",
            options=(
                "Make it hard enough to matter",
                "Do it only when you feel motivated",
                "Change it every week",
                "Make it so small that it feels almost too easy",
            ),
            answer_index=3,
        ),
        Question(
            prompt="Why can patience feel old-fashioned today?",
            options=(
                "Because nobody likes habits",
                "Because the world celebrates speed",
                "Because it costs too much money",
                "Because science has replaced it",
            ),
            answer_index=1,
        ),
    ),
    vocabulary=(
        Question(
            prompt='"Consistency" means doing something ___.',
            options=(
                "once, very intensely",
                "regularly over time",
                "only when you feel like it",
                "as fast as possible",
            ),
            answer_index=1,
        ),
        Question(
            prompt='"Adds up to" means ___.',
            options=(
                "becomes smaller",
                "disappears suddenly",
                "reaches a total",
                "stops completely",
            ),
            answer_index=2,
        ),
        Question(
            prompt='"Intensity" refers to ___.',
            options=(
                "how long something lasts",
                "how often something happens",
                "how much something costs",
                "how strong or hard something is",
            ),
            answer_index=3,
        ),
        Question(
            prompt='"Old-fashioned" means ___.',
            options=("no longer modern", "new and exciting", "very expensive", "very dangerous"),
            answer_index=0,
        ),
        Question(
            prompt='In "the quiet engine behind real change", "engine" means ___.',
            options=(
                "a machine inside a car",
                "a loud noise",
                "the force that drives something",
                "a type of habit",
            ),
            answer_index=2,
        ),
    ),
    translation_prompt=(
        "请把下面这句话翻译成中文：Small habits are easy to start, easy to repeat, and hard "
        "to notice until they have already changed your life."
    ),
)

SET_C = AssessmentSet(
    key="c",
    level_hint="B2",
    passage=(
        "In crowded cities, space is never free. Yet a growing number of urban planners are "
        "finding room for small pockets of woodland, often no larger than a tennis court. These "
        "so-called mini forests are planted densely with native trees and shrubs, sometimes "
        "three hundred seedlings in an area the size of a parking space. The idea comes from the "
        "Japanese botanist Akira Miyawaki, who argued that native species, planted close "
        "together, grow up to ten times faster than conventional plantations and quickly form a "
        "self-sustaining ecosystem. Supporters point to several benefits. The dense roots absorb "
        "rainwater and reduce flooding; the shade cools streets during heatwaves; and the mixture "
        "of trees provides shelter for insects and birds. Critics note that the method is not a "
        "substitute for large forests, and that careful planting and long-term care are "
        "essential. Still, in cities where parks are rare, even a tiny patch of green can bring "
        "nature within walking distance of thousands of residents. For many planners, these mini "
        "forests represent a practical, affordable way to make urban life healthier."
    ),
    comprehension=(
        Question(
            prompt='What is a "mini forest" as described in the passage?',
            options=(
                "A very large national park",
                "A farm outside the city",
                "A small, densely planted area of native trees",
                "A building covered in grass",
            ),
            answer_index=2,
        ),
        Question(
            prompt="Who was Akira Miyawaki?",
            options=(
                "An urban planner in Europe",
                "A Japanese botanist",
                "A critic of planting trees",
                "A city mayor",
            ),
            answer_index=1,
        ),
        Question(
            prompt="According to supporters, one benefit of mini forests is ___.",
            options=("faster traffic", "fewer birds", "more parking spaces", "reduced flooding"),
            answer_index=3,
        ),
        Question(
            prompt="What do critics point out?",
            options=(
                "The method is not a substitute for large forests",
                "Mini forests are always harmful",
                "Native trees grow too slowly",
                "Cities have no space at all",
            ),
            answer_index=0,
        ),
        Question(
            prompt="What is the author's overall attitude toward mini forests?",
            options=("Strongly negative", "Completely neutral", "Cautiously positive", "Mocking"),
            answer_index=2,
        ),
    ),
    vocabulary=(
        Question(
            prompt='"Densely" means ___.',
            options=(
                "with large spaces between things",
                "closely packed together",
                "very slowly",
                "with very little water",
            ),
            answer_index=1,
        ),
        Question(
            prompt='"Self-sustaining" means ___.',
            options=(
                "needing constant help from people",
                "made entirely by machines",
                "very expensive to build",
                "able to continue without outside support",
            ),
            answer_index=3,
        ),
        Question(
            prompt='"Benefits" are ___.',
            options=("problems", "advantages", "costs", "rules"),
            answer_index=1,
        ),
        Question(
            prompt='"Substitute for" means ___.',
            options=(
                "a perfect copy of",
                "a reason to stop",
                "something that can replace",
                "a type of forest",
            ),
            answer_index=2,
        ),
        Question(
            prompt='"A patch of green" refers to ___.',
            options=(
                "a painted wall",
                "a small area of plants",
                "a clothing store",
                "a kind of wood",
            ),
            answer_index=1,
        ),
    ),
    translation_prompt=(
        "请把下面这句话翻译成中文：In cities where parks are rare, even a tiny patch of green "
        "can bring nature within walking distance of thousands of residents."
    ),
)

ASSESSMENT_SETS: dict[str, AssessmentSet] = {item.key: item for item in (SET_A, SET_B, SET_C)}


def get_set(key: str = DEFAULT_SET_KEY) -> AssessmentSet:
    """按 key 取题；未知 key 回退到默认的 B1 套。"""
    return ASSESSMENT_SETS.get(key, ASSESSMENT_SETS[DEFAULT_SET_KEY])
