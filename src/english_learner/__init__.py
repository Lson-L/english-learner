"""每日英语（Daily English）——本地 AI 英语学习桌面应用。

产品闭环：
    首次配置 AI → 水平测评 → 每天一篇 i+1 文章 → 划词入生词本 → 间隔重复复习。

分层约定：
- gui/       只放 PySide6 视图与交互，不写业务逻辑；
- services/  业务逻辑，纯 Python，不 import PySide6，方便单测；
- storage/   SQLite 读写（M1 加入）；
- models/    领域对象（dataclass）。

作者：lson
"""

__version__ = "0.1.0"
