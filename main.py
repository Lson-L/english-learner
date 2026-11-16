"""工程根目录下的启动入口（方便在 PyCharm 里右键直接运行）。

Java 对照：
- 相当于 IDE 里 Run 一个带 main 方法的类。
- 真正的业务逻辑全部在 src/pylearn/ 包里，这里只做一行转发。
  Java 项目也不会把逻辑写在启动类的 main 里，道理一样。

运行方式：
- PyCharm：右键本文件 → Run 'main'
- 命令行：uv run python main.py
"""

from pylearn.cli import main

if __name__ == "__main__":
    # raise SystemExit(...) ≈ Java 的 System.exit(code)，
    # 把 main 返回的 int 作为进程退出码（0 成功，非 0 失败）。
    raise SystemExit(main())
