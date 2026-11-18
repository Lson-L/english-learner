"""标题过滤器：把演唱会、晚会、电视特别篇等“非电影”条目挡掉。

为什么只能按标题过滤？
- 豆瓣搜索接口返回的字段只有 id / title / rate / directors / casts / url / cover，
  没有“类型”“片长”“上映渠道”这些信息。
- 想要精确判断是不是院线电影，得逐条打开详情页解析，100 条就是 100 次额外请求，
  既慢又更容易触发反爬，所以这里用关键字黑名单做启发式过滤。
- 关键字可以追加，也可以用 --no-filter 整体关闭，误杀了还能救回来。

Java 对照：
- 本文件 ≈ Predicate<Movie> 的一个实现（TitleFilter::accept）。
- 元组 DEFAULT_EXCLUDE_KEYWORDS ≈ static final List<String>。
"""

import re

from .models import Movie

# 默认排除的关键字（中文按“包含”匹配）。
# 分组的含义：
# - 演出类：演唱会 / 音乐会 / 演奏会 / 音乐节 / 巡演 / 晚会 / 纪念演出 / 专场
# - 舞台类：舞台剧 / 话剧 / 音乐剧（这些通常是舞台录像，不是院线电影）
# - 节目类：周六夜现场 / 脱口秀 / 单口 / 相声 / 见面会
# - 电视特辑：特辑 / 特别篇 / 特别集 / 周年纪念
# - 剪辑合辑：剧场剪辑版 / 总集篇（电视剧或动画的分集剪成“电影”，不是新作）
# - 通用词：现场（能覆盖不少 Live、脱口秀专场；极少数片名含“现场”的电影会被误杀）
# - 命名特征：“版《”多见于舞台录像，如“马修·伯恩版《天鹅湖》”
DEFAULT_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "演唱会",
    "音乐会",
    "演奏会",
    "音乐节",
    "巡演",
    "巡回演出",
    "晚会",
    "纪念演出",
    "专场",
    "舞台剧",
    "话剧",
    "音乐剧",
    "周六夜现场",
    "脱口秀",
    "单口",
    "相声",
    "见面会",
    "歌迷",
    "粉丝",
    "特辑",
    "特别篇",
    "特别集",
    "周年纪念",
    "剧场剪辑版",
    "总集篇",
    "版《",
    "现场",
)

# 英文缩写/单词单独用“单词边界”匹配，
# 避免把 Nova（含 ova）、Deliver（含 live）这类正常片名误杀。
# Java 对照：≈ Pattern.compile("\\b(?:ova|oad|live|concert)\\b", CASE_INSENSITIVE)。
ASCII_EXCLUDE_PATTERN = re.compile(r"(?<![a-z0-9])(?:ova|oad|live|concert)(?![a-z0-9])", re.I)


class TitleFilter:
    """按标题关键字判断一条记录是不是“正儿八经的电影”。"""

    def __init__(
        self,
        extra_keywords: tuple[str, ...] | list[str] = (),
        enabled: bool = True,
    ) -> None:
        """
        - extra_keywords：在默认黑名单之外追加的关键字（命令行 --exclude）。
        - enabled=False：完全关闭过滤（命令行 --no-filter）。
        """
        self.enabled = enabled
        self.keywords = DEFAULT_EXCLUDE_KEYWORDS + tuple(extra_keywords)
        # 提前 casefold，避免每条记录都重复转换。
        self._folded_keywords = tuple(keyword.casefold() for keyword in self.keywords)

    def accept(self, movie: Movie) -> bool:
        """返回 True 表示保留，False 表示过滤掉。"""
        if not self.enabled:
            return True

        title = movie.title.casefold()
        if any(keyword in title for keyword in self._folded_keywords):
            return False
        # OVA / OAD / Live / Concert 这类英文词按单词边界判断，
        # 避免 Nova、Deliver 这类正常片名被误伤。
        return ASCII_EXCLUDE_PATTERN.search(movie.title) is None
