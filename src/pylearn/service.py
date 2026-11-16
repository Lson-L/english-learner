"""业务逻辑层（Service）。

Java 对照：
- 本文件 ≈ TaskService，是“业务层”。
- Python 没有 Spring 容器自动注入，但依赖注入的本质就是“构造器传入依赖”：
  TaskService(storage) 和 Java 里 new TaskService(repository) 一模一样。
- 方法里没有 public/private 关键字，用下划线前缀表达“内部使用”：
  _find / _persist ≈ private 方法。这是命名约定，不是语法强制。
"""

from .exceptions import TaskNotFoundError
from .models import Task
from .storage import JsonTaskStorage


class TaskService:
    """待办任务的业务逻辑（增删改查）。"""

    def __init__(self, storage: JsonTaskStorage) -> None:
        self._storage = storage
        # 应用启动时把文件里的记录载入内存 ≈ Java 里 @PostConstruct 初始化缓存
        self._tasks: list[Task] = [Task.from_dict(item) for item in storage.load()]

    def add(self, title: str) -> Task:
        """新增任务并立即持久化。"""
        clean_title = title.strip()
        if not clean_title:
            # Python 里校验失败常用内置异常，≈ Java 的 IllegalArgumentException
            raise ValueError("任务标题不能为空")

        # 自增 id ≈ 数据库自增主键。default=0 处理“列表为空”的情况。
        next_id = max((task.id for task in self._tasks), default=0) + 1
        task = Task(id=next_id, title=clean_title)
        self._tasks.append(task)
        self._persist()
        return task

    def list_all(self) -> list[Task]:
        """查询全部任务。

        返回的是副本（list(...)），防止调用方直接改动内部集合。
        Java 对照：≈ 返回 Collections.unmodifiableList(...) 的习惯。
        """
        return list(self._tasks)

    def complete(self, task_id: int) -> Task:
        """把任务标记为已完成。"""
        task = self._find(task_id)
        task.done = True
        self._persist()
        return task

    def remove(self, task_id: int) -> None:
        """删除任务。"""
        task = self._find(task_id)
        self._tasks.remove(task)
        self._persist()

    # ------------------- 以下为 private 方法（下划线前缀） -------------------

    def _find(self, task_id: int) -> Task:
        """按 id 查找，找不到抛业务异常。"""
        for task in self._tasks:
            if task.id == task_id:
                return task
        raise TaskNotFoundError(f"找不到 id={task_id} 的任务")

    def _persist(self) -> None:
        """把内存中的任务写回文件。

        Java 对照：≈ 在事务提交时 flush 到数据库。
        这里每次改动都全量落盘，简单但足够。
        """
        self._storage.save([task.to_dict() for task in self._tasks])
