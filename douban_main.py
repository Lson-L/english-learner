"""豆瓣爬虫的根目录启动入口（方便在 PyCharm 里右键运行）。

运行方式：
- PyCharm：右键本文件 → Run 'douban_main'（不带参数时会提示输入年份）
- 命令行：uv run python douban_main.py 2023 -n 100
- 安装后的命令：uv run douban-movies 2023 -n 100
"""

from douban_movie.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["2019", "-n", "100", "-o", "out.md"]))
