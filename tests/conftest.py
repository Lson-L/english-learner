"""pytest 的公共夹具（fixture）配置。

Java 对照：
- conftest.py ≈ JUnit 的测试基类 / @BeforeEach 集中定义的地方。
- 这里定义的 fixture 会被同目录及子目录的测试自动发现，不需要 import。
- fixture 相当于“由框架负责创建并在测试方法上注入的对象”。

注意：测试目录里没有 __init__.py，pytest 靠这一点把它当普通目录处理，
不会和 src 下的包混在一起。
"""

import pytest

from pylearn.service import TaskService
from pylearn.storage import JsonTaskStorage


@pytest.fixture
def service(tmp_path):
    """给每个测试一个全新的 TaskService。

    - tmp_path 是 pytest 内置 fixture，每个测试拿到独立的临时目录，
      等价于 JUnit 5 的 @TempDir。
    - 因为数据文件在临时目录里，测试之间互不污染，也不需要手动清理。
    """
    storage = JsonTaskStorage(tmp_path / "tasks.json")
    return TaskService(storage)
