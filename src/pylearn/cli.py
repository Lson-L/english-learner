"""命令行入口（表现层 / Controller）。

Java 对照：
- 相当于 Spring Boot 的 Controller + main 方法，只是把 HTTP 请求换成了命令行参数。
- argparse ≈ Java 的 picocli / commons-cli：声明有哪些子命令、哪些参数，自动生成 --help。
- main() 返回 int 作为退出码，≈ Java 的 System.exit(0/1)。

用法：
    py-learn add "学习 Python"
    py-learn list
    py-learn done 1
    py-learn remove 1
"""

import argparse
from pathlib import Path

from .exceptions import TaskNotFoundError
from .models import Task
from .service import TaskService
from .storage import JsonTaskStorage

# 默认数据文件，放在“当前工作目录”下。
# Java 对照：≈ application.properties 里配置的数据文件路径。
DEFAULT_DATA_FILE = Path("tasks.json")


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器（把参数定义和 main 逻辑分开，方便测试）。"""
    parser = argparse.ArgumentParser(
        prog="py-learn",
        description="用 Java 视角学习 Python 工程结构：待办任务管理器",
    )
    # 子命令 ≈ 一个 Controller 里的多个 handler（/add、/list、/done）
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="新增任务")
    add_parser.add_argument("title", help="任务标题")

    subparsers.add_parser("list", help="查看全部任务")

    done_parser = subparsers.add_parser("done", help="标记任务完成")
    done_parser.add_argument("id", type=int, help="任务 id")

    remove_parser = subparsers.add_parser("remove", help="删除任务")
    remove_parser.add_argument("id", type=int, help="任务 id")

    return parser


def format_tasks(tasks: list[Task]) -> str:
    """把任务列表格式化成可读文本（≈ 视图层 / DTO 的 toString）。"""
    if not tasks:
        return "（暂无任务）"
    lines = ["ID   状态  标题", "---- ---- --------------------"]
    for task in tasks:
        # 条件表达式 a if cond else b ≈ Java 的三元运算符
        status = "[x]" if task.done else "[ ]"
        lines.append(f"{task.id:<4} {status} {task.title}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, data_file: str | Path = DEFAULT_DATA_FILE) -> int:
    """程序主入口。

    参数说明：
    - argv：不传时读 sys.argv（真实命令行）；测试时可以直接传列表，≈ 可注入的入参。
    - data_file：数据文件路径，默认当前目录的 tasks.json；测试时传临时路径。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # 手动装配依赖：storage → service ≈ Spring 里 new 出来再注入
    service = TaskService(JsonTaskStorage(data_file))

    try:
        if args.command == "add":
            task = service.add(args.title)
            print(f"已新增任务 #{task.id}: {task.title}")
        elif args.command == "list":
            print(format_tasks(service.list_all()))
        elif args.command == "done":
            task = service.complete(args.id)
            print(f"已完成任务 #{task.id}: {task.title}")
        elif args.command == "remove":
            service.remove(args.id)
            print(f"已删除任务 #{args.id}")
    except (ValueError, TaskNotFoundError) as exc:
        # 业务异常统一在这里转成“错误提示 + 非 0 退出码”
        # ≈ Java 的 @ControllerAdvice 统一异常处理
        print(f"错误: {exc}")
        return 1

    return 0
