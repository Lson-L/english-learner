"""命令行入口（表现层 / Controller）。

用法：
    douban-movies 2023                          # 抓 2023 年评分前 100，Markdown 打到终端
    douban-movies 2023 -n 50 -o report.md       # 抓前 50，保存 Markdown 报告
    douban-movies 2023 -o out.json              # 保存 JSON（含评论）
    douban-movies 2023 -o out.csv               # 保存 CSV（Excel 可直接打开）
    douban-movies 2023 --comments 0             # 不抓评论，只抓榜单
    douban-movies                               # 不带年份时交互输入

约定：Markdown 正文走 stdout（方便 `> report.md` 重定向），
进度、提示、错误全部走 stderr，保证重定向出来的文件是干净的 Markdown。

Java 对照：
- argparse ≈ picocli / commons-cli。
- main() 返回 int 作为退出码 ≈ System.exit(0/1)。
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .client import DoubanClient, DoubanError
from .crawler import DEFAULT_PAGE_SIZE, MovieCrawler
from .filters import TitleFilter
from .report import render_markdown
from .storage import save_movies

# 默认每部电影抓多少条热门评论。
DEFAULT_COMMENT_COUNT = 15


def configure_stdout() -> None:
    """把标准输出/错误统一成 UTF-8。

    Windows 上直接输出到控制台时 Python 本来就用 UTF-8；
    但输出被管道或重定向到文件时，默认编码会退回系统区域编码（中文系统是 GBK）。
    这会导致 `douban-movies 2023 | more` 或重定向到文件时中文乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # pytest 捕获输出时替换过的流可能不支持，忽略即可。
            pass


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器（与 main 分离，方便测试）。"""
    parser = argparse.ArgumentParser(
        prog="douban-movies",
        description="抓取指定年份的豆瓣电影，按评分从高到低生成 Markdown 报告"
        "（默认过滤演唱会/晚会/特别篇，并抓取每部电影的热门评论）",
        epilog="示例：douban-movies 2023 -n 50 -o report.md --comments 10",
    )
    parser.add_argument(
        "year",
        nargs="?",
        type=int,
        help="年份，例如 2023；不传则进入交互输入",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=100,
        help="抓取电影条数，默认 100",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="结果保存路径，后缀支持 .md / .json / .csv；不传则 Markdown 输出到终端",
    )
    parser.add_argument(
        "--comments",
        type=int,
        default=DEFAULT_COMMENT_COUNT,
        metavar="N",
        help=f"每部电影抓多少条热门评论，默认 {DEFAULT_COMMENT_COUNT}；填 0 表示不抓",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="两次请求之间的间隔秒数，默认 1.0（抓评论时请求较多，调大可降低被反爬概率）",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="关闭默认过滤（保留演唱会、晚会、特别篇等条目）",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="关键字",
        help="额外排除的标题关键字，可重复指定，例如 --exclude 纪录片 --exclude 短片",
    )
    return parser


def read_year() -> int:
    """交互输入年份（提示写到 stderr，避免污染 stdout 里的 Markdown）。"""
    while True:
        print("请输入年份（例如 2023）: ", end="", file=sys.stderr)
        text = input().strip()
        if not text:
            continue
        try:
            return int(text)
        except ValueError:
            print("请输入有效的年份数字，例如 2023", file=sys.stderr)


def validate_year(year: int) -> None:
    current_year = datetime.now().year
    if not 1900 <= year <= current_year:
        raise ValueError(f"年份需在 1900 ~ {current_year} 之间，你输入的是 {year}")


def log(message: str) -> None:
    """状态提示统一走 stderr。"""
    print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """程序主入口。

    argv 不传时读 sys.argv；测试时可以直接传列表（≈ 可注入的入参）。
    """
    configure_stdout()

    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        year = args.year if args.year is not None else read_year()
        validate_year(year)
        if args.count <= 0:
            raise ValueError("抓取条数必须大于 0")
        if args.comments < 0:
            raise ValueError("评论条数不能为负数")

        title_filter = TitleFilter(extra_keywords=args.exclude, enabled=not args.no_filter)
        client = DoubanClient(delay=args.delay)
        crawler = MovieCrawler(client, page_size=DEFAULT_PAGE_SIZE, title_filter=title_filter)

        log(f"正在抓取 {year} 年电影（目标 {args.count} 条）...")
        movies = crawler.crawl(year, count=args.count)

        if args.comments > 0:
            log(f"正在抓取热门评论（每部 {args.comments} 条，共 {len(movies)} 部）...")
            movies = crawler.attach_comments(movies, count=args.comments)

        filtered_note = (
            f"，已过滤 {crawler.filtered_count} 条非电影条目" if crawler.filtered_count else ""
        )
        log(f"共 {len(movies)} 部（{year} 年，按评分从高到低{filtered_note}）")
        if len(movies) < args.count:
            log(f"注意：符合条件的电影不足 {args.count} 条。")
        if crawler.comments_failed:
            log(f"注意：有 {crawler.comments_failed} 部电影的评论抓取失败。")

        if args.output:
            save_movies(
                movies,
                args.output,
                year=year,
                filtered_count=crawler.filtered_count,
            )
            log(f"已保存到：{args.output}")
        else:
            print(render_markdown(movies, year=year, filtered_count=crawler.filtered_count))

    except (ValueError, DoubanError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("错误: 没有提供年份，当前环境也无法交互输入。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130

    return 0
