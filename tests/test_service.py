"""Service 层单元测试。

Java 对照：≈ TaskServiceTest（JUnit + AssertJ）。
运行方式：uv run pytest
"""

import pytest

from pylearn.exceptions import TaskNotFoundError
from pylearn.service import TaskService
from pylearn.storage import JsonTaskStorage


def test_add_task(service):
    task = service.add("学习 Python 工程结构")

    # assert 语句 ≈ AssertJ 的 assertThat(...).isEqualTo(...)
    assert task.id == 1
    assert task.title == "学习 Python 工程结构"
    assert task.done is False


def test_add_assigns_incrementing_ids(service):
    first = service.add("任务一")
    second = service.add("任务二")

    assert [first.id, second.id] == [1, 2]


def test_add_rejects_blank_title(service):
    # pytest.raises ≈ JUnit 的 assertThrows(ValueError.class, ...)
    with pytest.raises(ValueError):
        service.add("   ")


def test_complete_task(service):
    task = service.add("要完成的任务")

    completed = service.complete(task.id)

    assert completed.done is True


def test_complete_missing_task_raises(service):
    with pytest.raises(TaskNotFoundError):
        service.complete(999)


def test_remove_task(service):
    task = service.add("待删除的任务")

    service.remove(task.id)

    assert service.list_all() == []


def test_tasks_survive_service_restart(tmp_path):
    """模拟“应用重启”：新建一个 service，验证数据已落盘。"""
    data_file = tmp_path / "tasks.json"

    first_service = TaskService(JsonTaskStorage(data_file))
    first_service.add("重启后应该还在")

    second_service = TaskService(JsonTaskStorage(data_file))

    assert [task.title for task in second_service.list_all()] == ["重启后应该还在"]
