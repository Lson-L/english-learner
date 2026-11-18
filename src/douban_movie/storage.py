"""输出层（Repository / Exporter）：把结果写成 JSON 或 CSV。

Java 对照：
- 本文件 ≈ 导出服务：把 List<Movie> 写成文件，等价于 Jackson / EasyExcel 导出。
- 用 Python 标准库 json + csv，不需要额外依赖。
"""

import csv
import json
from pathlib import Path

from .models import Movie
from .report import render_markdown

CSV_FIELDS = [
    "rank",
    "title",
    "rate",
    "directors",
    "casts",
    "url",
    "douban_id",
    "comment_count",
    "top_comment",
]


def save_json(movies: list[Movie], file_path: str | Path) -> None:
    """保存为 JSON（UTF-8，不转义中文）。"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps([movie.to_dict() for movie in movies], ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def save_csv(movies: list[Movie], file_path: str | Path) -> None:
    """保存为 CSV。

    注意这里是 utf-8-sig（带 BOM）：Windows 上的 Excel 默认按 GBK 打开 CSV，
    不加 BOM 中文会乱码。Java 里用 EasyExcel 时也常遇到同样的编码坑。
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rank, movie in enumerate(movies, start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "title": movie.title,
                    "rate": "" if movie.rate is None else f"{movie.rate:.1f}",
                    "directors": " / ".join(movie.directors),
                    "casts": " / ".join(movie.casts),
                    "url": movie.url,
                    "douban_id": movie.id,
                    "comment_count": len(movie.comments),
                    "top_comment": _one_line(movie.comments[0].content) if movie.comments else "",
                }
            )


def save_markdown(
    movies: list[Movie],
    file_path: str | Path,
    year: int,
    filtered_count: int = 0,
) -> None:
    """保存为 Markdown 报告。"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_markdown(movies, year=year, filtered_count=filtered_count)
    path.write_text(text, encoding="utf-8")


def save_movies(
    movies: list[Movie],
    file_path: str | Path,
    *,
    year: int | None = None,
    filtered_count: int = 0,
) -> None:
    """按后缀自动选择 Markdown / JSON / CSV。"""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".md":
        if year is None:
            raise ValueError("保存 Markdown 需要提供年份")
        save_markdown(movies, file_path, year=year, filtered_count=filtered_count)
    elif suffix == ".json":
        save_json(movies, file_path)
    elif suffix == ".csv":
        save_csv(movies, file_path)
    else:
        raise ValueError("输出文件后缀只支持 .md / .json / .csv")


def _one_line(text: str) -> str:
    """CSV 里不能有换行，把短评压成一行。"""
    return " ".join(text.split())
