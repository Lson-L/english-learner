"""阅读检测：AI 输出解析、本地评分与编排服务。

分层：
- constants：阈值、额度与提示词常量；
- models：结果对象与异常；
- parsing：AI 输出解析与本地归一化；
- scoring：本地证据打分与等级策略；
- service：ReadingQuizService 编排（出题 / 判分 / 讲评 / 调级）。
"""

from .models import EvidenceSummary, InvalidQuizError, LevelDecision, QuizOutcome
from .service import ReadingQuizService

__all__ = [
    "EvidenceSummary",
    "InvalidQuizError",
    "LevelDecision",
    "QuizOutcome",
    "ReadingQuizService",
]
