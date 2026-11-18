"""业务层（Service）：负责分页、去重、排序、截断。

Java 对照：
- 本文件 ≈ MovieCrawlerService，是“业务流程”所在层。
- 它只依赖 DoubanClient 的两个方法，所以测试时可以塞一个假的 client，
  完全不用联网（≈ 用 Mockito mock 掉 FeignClient）。
"""

import logging
from dataclasses import replace

from .client import DoubanClient, DoubanError
from .filters import TitleFilter
from .models import Comment, Movie

logger = logging.getLogger(__name__)

# 每页请求多少条。实测豆瓣接口 limit=100 稳定可用。
DEFAULT_PAGE_SIZE = 100

# 最多扫描目标条数的几倍：过滤掉非电影后需要继续往后翻，
# 但也不能无限翻（冷门年份可能大部分都是演唱会）。
MAX_SCAN_MULTIPLIER = 3


class MovieCrawler:
    """按年份抓取电影，返回评分从高到低的列表。"""

    def __init__(
        self,
        client: DoubanClient,
        page_size: int = DEFAULT_PAGE_SIZE,
        title_filter: TitleFilter | None = None,
    ) -> None:
        if page_size <= 0:
            raise ValueError("page_size 必须大于 0")
        self.client = client
        self.page_size = page_size
        # 不传就使用默认的标题过滤规则（过滤演唱会/晚会/特别篇等）。
        self.title_filter = title_filter or TitleFilter()
        # 本次抓取被过滤掉多少条，供 CLI 提示用户。
        self.filtered_count = 0
        # 评论抓取失败的部数，供 CLI 提示用户。
        self.comments_failed = 0

    def crawl(self, year: int, count: int = 100) -> list[Movie]:
        """抓取指定年份的 count 部电影。

        流程：
        1. 按 start 偏移量一页页拉取，直到“过滤后”凑够 count 条或没有更多数据；
        2. 按电影 id 去重（分页期间豆瓣排序可能有微小变动，导致重复）；
        3. 过滤演唱会、晚会、特别篇等非电影条目；
        4. 本地再按评分降序排一次——豆瓣服务端排序存在个别倒挂，不能完全相信；
        5. 截断到 count 条。

        注意：被过滤的条目会占掉排名名额，所以不能只抓一页就结束，
        要继续往后翻，直到合格的电影够数为止。

        Java 对照：≈ service 里串起“远程调用 → 转换 → 过滤 → 排序 → 分页”的编排逻辑。
        """
        if count <= 0:
            raise ValueError("抓取条数必须大于 0")

        movies: list[Movie] = []
        seen_ids: set[str] = set()
        start = 0
        scanned = 0
        self.filtered_count = 0

        # 安全上限：最多扫描目标数量的 3 倍，避免在冷门年份翻太多页。
        max_scan = max(count * MAX_SCAN_MULTIPLIER, self.page_size)

        while len(movies) < count and scanned < max_scan:
            # 过滤会减少合格数量，所以每次都按整页请求，而不是只补差额。
            limit = min(self.page_size, max_scan - scanned)
            page = self.client.fetch_page(year, start=start, limit=limit)
            if not page:
                break  # 该年份没有更多数据

            for raw in page:
                movie = Movie.from_api(raw)
                if not movie.id or movie.id in seen_ids:
                    continue
                seen_ids.add(movie.id)

                if not self.title_filter.accept(movie):
                    self.filtered_count += 1
                    continue

                movies.append(movie)

            scanned += len(page)
            start += len(page)
            # 只有“这一页不满”才说明真的到底了。
            # 不能因为 added == 0 就退出：整页都可能是演唱会，后面还有电影。
            if len(page) < limit:
                break

        movies.sort(key=lambda movie: movie.sort_key, reverse=True)
        return movies[:count]

    def attach_comments(self, movies: list[Movie], count: int = 15) -> list[Movie]:
        """给每部电影补充热门短评，返回新的列表。

        - 单部电影抓取失败不会中断整批任务，只记录下来继续抓下一部。
        - Movie 是 frozen dataclass，所以用 dataclasses.replace 生成带评论的新对象
          （≈ Java record 里用 with 风格生成一个新实例）。
        """
        if count <= 0:
            return movies

        self.comments_failed = 0
        enriched: list[Movie] = []
        total = len(movies)

        for index, movie in enumerate(movies, start=1):
            try:
                raw_comments = self.client.fetch_comments(movie.id, count=count)
                comments = [Comment.from_api(raw) for raw in raw_comments][:count]
            except DoubanError as exc:
                self.comments_failed += 1
                logger.warning("评论抓取失败（%s）：%s", movie.title, exc)
                comments = []

            enriched.append(replace(movie, comments=comments))

            if index % 10 == 0 or index == total:
                logger.info("评论进度：%d/%d", index, total)

        return enriched
