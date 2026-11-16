"""自定义异常。

Java 对照：
- 相当于 class TaskNotFoundException extends RuntimeException。
- Python 的异常体系就是普通的类继承；没有 checked / unchecked 之分，
  所以不用在方法签名上写 throws，调用方想捕获就捕获。
- 自定义异常也让上层能精确捕获“业务异常”，而不是把所有错误都当成 Exception。
"""


class TaskNotFoundError(Exception):
    """任务不存在时抛出。"""
