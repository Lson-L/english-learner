"""业务层（Service）：负责分页、去重、排序、截断。

Java 对照：
- 本文件 ≈ MovieCrawlerService，是“业务流程”所在层。
- 它只依赖 DoubanClient 的两个方法，所以测试时可以塞一个假的 client，
  完全不用联网（≈ 用 Mockito mock 掉 FeignClient）。
"""

from .client import DoubanClient
from .models import Movie

# 每页请求多少条。实测豆瓣接口 limit=100 稳定可用。
DEFAULT_PAGE_SIZE = 100


class MovieCrawler:
    """按年份抓取电影，返回评分从高到低的列表。"""

    def __init__(self, client: DoubanClient, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        if page_size <= 0:
            raise ValueError("page_size 必须大于 0")
        self.client = client
        self.page_size = page_size

    def crawl(self, year: int, count: int = 100) -> list[Movie]:
        """抓取指定年份的 count 部电影。

        流程：
        1. 按 start 偏移量一页页拉取，直到凑够 count 条或没有更多数据；
        2. 按电影 id 去重（分页期间豆瓣排序可能有微小变动，导致重复）；
        3. 本地再按评分降序排一次——豆瓣服务端排序存在个别倒挂，不能完全相信；
        4. 截断到 count 条。

        Java 对照：≈ service 里串起“远程调用 → 转换 → 排序 → 分页”的编排逻辑。
        """
        if count <= 0:
            raise ValueError("抓取条数必须大于 0")

        movies: list[Movie] = []
        seen_ids: set[str] = set()
        start = 0

        while len(movies) < count:
            # 最后一页只请求还差的数量，避免多抓。
            limit = min(self.page_size, count - len(movies))
            page = self.client.fetch_page(year, start=start, limit=limit)
            if not page:
                break  # 该年份没有更多数据

            added = 0
            for raw in page:
                movie = Movie.from_api(raw)
                if not movie.id or movie.id in seen_ids:
                    continue
                seen_ids.add(movie.id)
                movies.append(movie)
                added += 1

            start += len(page)
            if added == 0 or len(page) < limit:
                break  # 全是重复项，或已经到最后一页，防止死循环

        movies.sort(key=lambda movie: movie.sort_key, reverse=True)
        return movies[:count]
