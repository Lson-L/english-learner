"""命令行入口（表现层 / Controller）。

用法：
    douban-movies 2023                    # 抓 2023 年评分前 100
    douban-movies 2023 -n 50              # 抓前 50
    douban-movies 2023 -n 200 -o out.json # 抓 200 条并保存 JSON
    douban-movies 2023 -o out.csv         # 保存 CSV（Excel 可直接打开）
    douban-movies                         # 不带年份时交互输入

Java 对照：
- argparse ≈ picocli / commons-cli。
- main() 返回 int 作为退出码 ≈ System.exit(0/1)。
"""

import argparse
import logging
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from .client import DoubanClient, DoubanError
from .crawler import DEFAULT_PAGE_SIZE, MovieCrawler
from .models import Movie
from .storage import save_movies

# 控制台表格里片名、导演最多显示多少列（中文算 2 列）。
TITLE_MAX_WIDTH = 36
DIRECTOR_MAX_WIDTH = 16


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
        description="抓取指定年份的豆瓣电影，按评分从高到低输出",
        epilog="示例：douban-movies 2023 -n 100 -o movies.json",
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
        help="抓取条数，默认 100",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="结果保存路径，后缀支持 .json 或 .csv",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="两次请求之间的间隔秒数，默认 1.0（调大可降低被反爬概率）",
    )
    return parser


# ---------------------------------------------------------------------------
# 控制台表格：中文是“全角”字符，占 2 个终端列，不能直接用 len() 对齐。
# Java 对照：≈ 自己写一个按显示宽度补齐的工具类（类似 commons-lang 的 StringUtils）。
# ---------------------------------------------------------------------------
def char_width(char: str) -> int:
    """单个字符在终端里占几列。"""
    # W=宽，F=全角，A=模糊宽度（中文环境下通常也按 2 列显示）
    return 2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1


def display_width(text: str) -> int:
    """字符串的终端显示宽度。"""
    return sum(char_width(char) for char in text)


def truncate_display(text: str, max_width: int) -> str:
    """按显示宽度截断，超出时以省略号结尾。"""
    if display_width(text) <= max_width:
        return text
    kept: list[str] = []
    width = 0
    for char in text:
        width += char_width(char)
        if width > max_width - 1:
            break
        kept.append(char)
    return "".join(kept) + "…"


def pad_display(text: str, width: int) -> str:
    """按显示宽度右侧补空格。"""
    text = truncate_display(text, width)
    return text + " " * max(0, width - display_width(text))


def format_movies(movies: list[Movie]) -> str:
    """把电影列表格式化成对齐的文本表格。"""
    if not movies:
        return "（没有找到符合条件的电影）"

    titles = [truncate_display(movie.title or "—", TITLE_MAX_WIDTH) for movie in movies]
    directors = [
        truncate_display(" / ".join(movie.directors[:2]) or "—", DIRECTOR_MAX_WIDTH)
        for movie in movies
    ]

    rank_width = max(display_width("排名"), len(str(len(movies))))
    rating_width = max(display_width("评分"), display_width("暂无"), 3)
    title_width = max(display_width(title) for title in titles)
    director_width = max(display_width(director) for director in directors)

    lines = [
        f"{pad_display('排名', rank_width)}  {pad_display('评分', rating_width)}  "
        f"{pad_display('片名', title_width)}  {pad_display('导演', director_width)}",
        f"{'-' * rank_width}  {'-' * rating_width}  {'-' * title_width}  {'-' * director_width}",
    ]
    for rank, movie in enumerate(movies, start=1):
        rating = f"{movie.rate:.1f}" if movie.rate is not None else "暂无"
        lines.append(
            f"{str(rank).rjust(rank_width)}  {pad_display(rating, rating_width)}  "
            f"{pad_display(titles[rank - 1], title_width)}  {directors[rank - 1]}"
        )
    return "\n".join(lines)


def read_year() -> int:
    """交互输入年份。"""
    while True:
        text = input("请输入年份（例如 2023）: ").strip()
        if not text:
            continue
        try:
            return int(text)
        except ValueError:
            print("请输入有效的年份数字，例如 2023")


def validate_year(year: int) -> None:
    current_year = datetime.now().year
    if not 1900 <= year <= current_year:
        raise ValueError(f"年份需在 1900 ~ {current_year} 之间，你输入的是 {year}")


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

        client = DoubanClient(delay=args.delay)
        crawler = MovieCrawler(client, page_size=DEFAULT_PAGE_SIZE)

        print(f"正在抓取 {year} 年电影（目标 {args.count} 条）...")
        movies = crawler.crawl(year, count=args.count)

        print(format_movies(movies))
        print(f"\n共 {len(movies)} 部（{year} 年，按评分从高到低）")

        if args.output:
            save_movies(movies, args.output)
            print(f"已保存到：{args.output}")

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
