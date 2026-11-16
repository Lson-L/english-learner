"""pylearn 包入口。

Java 对照：
- 这个目录 + __init__.py ≈ Java 的 package（com.example.demo）。
- Java 靠目录结构 + package 声明；Python 靠目录里有 __init__.py 来标记“这是一个包”。
- 这里的 import 相当于在这个包的入口类里对外导出公共 API：
  使用者写 `from pylearn import TaskService` 就够了，不用关心它具体在哪个文件。
  这类似 Java 里把实现类放在 impl 包、只对外暴露接口的做法。
"""

from .exceptions import TaskNotFoundError
from .models import Task
from .service import TaskService
from .storage import JsonTaskStorage

# 版本号。Java 里通常由 Maven 在打包时写进 MANIFEST.MF；
# Python 里常放在这里，方便运行时代码读取。
__version__ = "0.1.0"

# __all__ 声明 `from pylearn import *` 时导出哪些名字。
# Java 对照：≈ 一个包对外公开的 API 清单。
__all__ = [
    "Task",
    "TaskService",
    "JsonTaskStorage",
    "TaskNotFoundError",
    "__version__",
]
