"""PyCharm 右键运行入口。

为什么不直接运行 src/english_learner/app.py：
src 布局下按文件运行，Python 会把 src/english_learner 当成顶层目录，
包内的相对导入（from . import ...）会报 "attempted relative import"。
从项目根目录运行本文件时，english_learner 按已安装的包（uv sync 的 editable
install）导入，行为与 `uv run english-learner` 完全一致。

Java 对照：≈ 一个带 Main-Class 的启动类，PyCharm 里右键 Run 即可。
"""

from english_learner.app import main

if __name__ == "__main__":
    raise SystemExit(main())
