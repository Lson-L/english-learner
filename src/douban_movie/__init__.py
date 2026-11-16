"""豆瓣电影爬虫（按年份抓取，按评分从高到低排序）。

Java 对照：
- 这个包 ≈ 一个独立的 Maven 模块（比如 py-learn 项目下的 douban-movie 子模块）。
- 和 pylearn 包互不依赖：待办是待办，爬虫是爬虫，各自分层、各自入口。
"""

from .client import DoubanClient, DoubanError
from .crawler import MovieCrawler
from .models import Movie

__all__ = ["DoubanClient", "DoubanError", "Movie", "MovieCrawler"]
