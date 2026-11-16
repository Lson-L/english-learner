"""Storage 层单元测试。

Java 对照：≈ TaskRepositoryTest，相当于给 DAO 写测试。
"""

from pylearn.models import Task
from pylearn.storage import JsonTaskStorage


def test_load_returns_empty_when_file_missing(tmp_path):
    storage = JsonTaskStorage(tmp_path / "missing.json")

    assert storage.load() == []


def test_save_then_load_roundtrip(tmp_path):
    storage = JsonTaskStorage(tmp_path / "tasks.json")
    records = [
        Task(id=1, title="中文标题不会被转义", done=False).to_dict(),
        Task(id=2, title="已完成的任务", done=True).to_dict(),
    ]

    storage.save(records)

    assert storage.load() == records
