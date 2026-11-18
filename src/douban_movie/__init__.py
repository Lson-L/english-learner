"""豆瓣电影爬虫（按年份抓取，按评分从高到低排序）。

Java 对照：
- 这个包 ≈ 一个独立的 Maven 模块，包内的 client / crawler / storage / cli
  分别对应 HTTP 客户端、Service、Repository、Controller。
"""

from .client import DoubanClient, DoubanError
from .crawler import MovieCrawler
from .filters import TitleFilter
from .models import Comment, Movie
from .report import render_markdown

__all__ = [
    "Comment",
    "DoubanClient",
    "DoubanError",
    "Movie",
    "MovieCrawler",
    "TitleFilter",
    "render_markdown",
]
