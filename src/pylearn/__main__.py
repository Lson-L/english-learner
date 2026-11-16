"""支持 `python -m pylearn` 直接运行整个包。

Java 对照：
- 相当于 jar 的 Main-Class，或者 Spring Boot 的启动类。
- Python 的约定是：包目录下有 __main__.py，才能用 `python -m 包名` 执行。
  没有它，`python -m pylearn` 会报错。
- 有了它，`python -m pylearn list` 就等价于 `py-learn list`（后者是安装后生成的命令）。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
