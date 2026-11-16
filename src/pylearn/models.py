"""数据模型层（entity / POJO）。

Java 对照：
- 本文件 ≈ Java 的 entity 包（Task.java）。
- Python 没有 Lombok，但标准库的 @dataclass 会自动生成
  __init__ / __repr__ / __eq__，效果接近 Lombok 的 @Data。
  如果写成 frozen=True，就更像 Java 14 的 record。
- 字段类型写在变量名后面，等价于 Java 的 `private final String title;`。
  区别是 Python 的类型注解只在开发期给 IDE / mypy 看，运行时不强制。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Task:
    """一条待办任务。"""

    id: int
    title: str
    done: bool = False
    # default_factory ≈ 在 Java 构造器里给字段赋默认值。
    # 这里存 ISO 格式字符串，方便直接写进 JSON。
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        """转成 JSON 友好的 dict。

        Java 对照：≈ 用 Jackson 把对象转成 Map / JSON 之前的那一步。
        """
        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从 dict 还原对象。

        Java 对照：≈ Jackson 的 ObjectMapper.readValue(json, Task.class)。
        @classmethod ≈ Java 的静态工厂方法（Task.of(...)）。
        """
        return cls(
            id=data["id"],
            title=data["title"],
            done=data.get("done", False),
            created_at=data.get("created_at", ""),
        )
