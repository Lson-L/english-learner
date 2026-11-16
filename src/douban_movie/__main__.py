"""支持 `python -m douban_movie` 直接运行整个包。

Java 对照：
- 相当于 jar 的 Main-Class。
- 包目录下有 __main__.py，才能用 `python -m 包名` 执行。
- `python -m douban_movie 2023` 等价于安装后的命令 `douban-movies 2023`。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
