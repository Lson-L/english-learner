"""HTTP 客户端层：只负责“发请求 + 解析 JSON”。

Java 对照：
- 本层 ≈ 用 RestTemplate / HttpClient 封装的 Feign Client。
- 之所以单独一层，是为了让 crawler 不关心 URL、请求头、重试、限速这些细节；
  以后豆瓣换接口，只改这个文件。
- 只用标准库 urllib，不额外装 requests/httpx，保持本项目“零运行时依赖”。

实测要点（2026-11）：
- year 参数单独传是无效的，年份必须拼进 tags，形如 tags=电影,2023。
- sort=S 表示按评分排序，range=0,10 表示评分区间 0~10。
- limit 可以取到 100（甚至更大），默认 20；这里统一用 100 减少请求次数。
"""

import json
import logging
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

SEARCH_URL = "https://movie.douban.com/j/new_search_subjects"

# 不带浏览器 UA 时，豆瓣很容易直接返回 403 或验证页。
# Java 对照：≈ HttpClient 里统一加的默认 Header 拦截器。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://movie.douban.com/explore",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


class DoubanError(RuntimeError):
    """豆瓣接口调用失败，≈ Java 的自定义 ServiceException。"""


class DoubanClient:
    """豆瓣搜索接口客户端。"""

    def __init__(self, timeout: float = 10.0, retries: int = 3, delay: float = 1.0) -> None:
        """
        参数说明：
        - timeout：单次请求超时秒数。
        - retries：失败重试次数（含第一次）。
        - delay：两次请求之间的最小间隔秒数，用来降低被反爬拦截的概率。
        """
        if retries < 1:
            raise ValueError("retries 必须 >= 1")
        self.timeout = timeout
        self.retries = retries
        self.delay = max(0.0, delay)
        self._last_request_at: float | None = None

    def fetch_page(self, year: int, start: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """抓取一页，返回原始 dict 列表。

        - start：从第几条开始（豆瓣分页是偏移量，不是页码）。
        - limit：本页最多几条。
        """
        params = {
            "sort": "S",  # S = 按评分排序
            "range": "0,10",
            "tags": f"电影,{year}",  # 年份必须放在 tags 里，单独传 year 无效
            "start": start,
            "limit": limit,
        }
        url = f"{SEARCH_URL}?{urlencode(params)}"

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                body = self._get(url)
                payload = json.loads(body)
                data = payload.get("data")
                if not isinstance(data, list):
                    # 常见于被反爬拦截后返回了结构不同的 JSON。
                    raise DoubanError(f"接口返回格式异常：{body[:120]!r}")
                return data
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, DoubanError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                wait = self.delay * (2 ** (attempt - 1)) + random.uniform(0, 0.3)
                logger.warning("第 %d 次请求失败（%s），%.1fs 后重试", attempt, exc, wait)
                time.sleep(wait)

        raise DoubanError(
            f"抓取失败（start={start}）：{last_error}。"
            "如果返回的是网页而不是 JSON，通常是豆瓣反爬，请调大 --delay 后重试。"
        ) from last_error

    def _get(self, url: str) -> str:
        """发一次 GET，按 UTF-8 解码响应。"""
        request = Request(url, headers=DEFAULT_HEADERS)
        # with 语句 ≈ Java 的 try-with-resources，自动关闭连接。
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
        # 必须显式指定 utf-8。Windows 上 open()/decode() 默认用 GBK，
        # 中文会直接乱码或抛 UnicodeDecodeError（这是 Java 开发者最容易踩的坑）。
        return raw.decode("utf-8")

    def _throttle(self) -> None:
        """控制请求频率：保证两次请求之间至少间隔 delay 秒。"""
        if self.delay > 0 and self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_request_at = time.monotonic()
