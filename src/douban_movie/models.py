"""数据模型层（entity / POJO）。

Java 对照：
- 本文件 ≈ Movie.java。
- @dataclass(frozen=True) ≈ Java 14 的 record：字段只读、自动生成
  __init__ / __repr__ / __eq__。
- API 返回的原始 JSON 是“松散的 dict”，这里统一转换成强类型的 Movie，
  相当于 Java 里先用 Jackson 反序列化成 DTO，再转成领域对象。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Movie:
    """一部电影。

    rate 用 float | None 而不是 float：
    豆瓣有些条目没有评分，用 None 表示“暂无评分”，比用 0 分更准确。
    """

    id: str
    title: str
    rate: float | None = None
    directors: list[str] = field(default_factory=list)
    casts: list[str] = field(default_factory=list)
    url: str = ""
    cover: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "Movie":
        """把豆瓣接口返回的一条 dict 转成 Movie。

        Java 对照：≈ 静态工厂方法 Movie.fromJson(Map<String, Object>)。
        接口里的 rate 是字符串（"9.4"），没有评分时是空字符串，这里统一处理。
        """
        rate_text = str(data.get("rate") or "").strip()
        try:
            rate = float(rate_text) if rate_text else None
        except ValueError:
            # 防御式编程：接口偶尔返回 "N/A" 之类的脏数据，不让整批任务失败。
            rate = None

        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")).strip(),
            rate=rate,
            directors=[str(name) for name in data.get("directors") or []],
            casts=[str(name) for name in data.get("casts") or []],
            url=str(data.get("url", "")),
            cover=str(data.get("cover", "")),
        )

    @property
    def sort_key(self) -> tuple[int, float]:
        """排序键：有评分的排前面，按评分降序；无评分的排最后。

        Python 的 sorted(key=...) 只要求 key 可比较：
        (1, 9.4) > (1, 8.8) > (0, 0.0)，配合 reverse=True 正好是想要的效果。
        Java 对照：≈ Comparator.comparing(Movie::getRate, nullsLast(reverseOrder()))。
        """
        return (1 if self.rate is not None else 0, self.rate or 0.0)

    def to_dict(self) -> dict:
        """转成 JSON 友好的 dict。"""
        return {
            "id": self.id,
            "title": self.title,
            "rate": self.rate,
            "directors": self.directors,
            "casts": self.casts,
            "url": self.url,
            "cover": self.cover,
        }
