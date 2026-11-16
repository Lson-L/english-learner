"""CLI（Controller）层测试。

Java 对照：≈ 给 Controller 写测试。
这里不启动真实子进程，而是直接调用 main() 并传入参数列表，
再通过 capsys 捕获标准输出 —— 相当于 MockMvc 只测 Controller 的行为。
"""

from pylearn.cli import main


def test_add_then_list(tmp_path, capsys):
    data_file = tmp_path / "tasks.json"

    assert main(["add", "写第一个测试"], data_file=data_file) == 0
    assert main(["list"], data_file=data_file) == 0

    output = capsys.readouterr().out
    assert "写第一个测试" in output
    assert "[ ]" in output


def test_done_missing_task_returns_error_code(tmp_path, capsys):
    data_file = tmp_path / "tasks.json"

    exit_code = main(["done", "999"], data_file=data_file)

    assert exit_code == 1
    assert "错误" in capsys.readouterr().out
