"""仓储层（Repository / DAO）：只负责读写持久化文件。

Java 对照：
- 本文件 ≈ TaskRepository / TaskDao，是“数据访问层”。
- 这里没有数据库，用 JSON 文件代替；但分层思想和 Java 完全一样：
  业务层不关心数据存在哪，只调用 storage.load() / storage.save()。
- 想换成 MySQL，只需要再写一个实现同样方法的类，上层代码不用改
  （Python 没有 interface + Spring 注入那么重的机制，靠“鸭子类型”+ 构造器传参）。
"""

import json
from pathlib import Path


class JsonTaskStorage:
    """把任务列表保存到 JSON 文件。"""

    def __init__(self, file_path: str | Path) -> None:
        # pathlib.Path ≈ java.nio.file.Path，比用字符串拼路径安全、可读。
        # `str | Path` 是类型注解，表示“字符串或 Path”，≈ Java 的方法重载合二为一。
        self.file_path = Path(file_path)

    def load(self) -> list[dict]:
        """读取全部记录；文件不存在时返回空列表。

        Java 对照：≈ repository.findAll()，查不到返回空 List 而不是 null。
        """
        if not self.file_path.exists():
            return []
        text = self.file_path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        # json.loads ≈ Jackson 的 readTree / 反序列化成 List<Map<String, Object>>
        return json.loads(text)

    def save(self, records: list[dict]) -> None:
        """整体覆盖写入所有记录。

        Java 对照：≈ repository.saveAll(...) 的全量覆盖版本。
        ensure_ascii=False 保证中文按原样写入，而不是变成 \\uXXXX 这样的转义。
        """
        # parents=True ≈ mkdirs()；exist_ok=True ≈ 目录已存在时不报错
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(records, ensure_ascii=False, indent=2)
        self.file_path.write_text(text, encoding="utf-8")
