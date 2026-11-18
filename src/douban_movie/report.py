"""Markdown 报告渲染层（View / Template）。

Java 对照：
- 本文件 ≈ 一个把 List<Movie> 渲染成 HTML/PDF 的模板引擎（类似 Thymeleaf 模板）。
- 单独一层的目的是：抓取逻辑（crawler）与展示格式（markdown）解耦，
  以后想输出 HTML 或 PDF，只需要再写一个 render_xxx，不用改抓取代码。
"""

from datetime import datetime

from .models import Movie


def render_markdown(
    movies: list[Movie],
    year: int,
    filtered_count: int = 0,
    generated_at: str | None = None,
) -> str:
    """把电影列表渲染成一份可读性较高的 Markdown 文档。"""
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = [f"# {year} 年豆瓣高分电影", ""]
    lines.append(f"> 共 **{len(movies)}** 部，按评分从高到低排列。")
    lines.append(f"> 抓取时间：{generated_at}　·　数据来源：豆瓣电影公开接口，仅供个人学习使用。")
    if filtered_count:
        lines.append(f"> 已过滤 {filtered_count} 条演唱会、晚会、特别篇等非电影条目。")
    lines.append("")

    lines.extend(_render_overview(movies))
    for rank, movie in enumerate(movies, start=1):
        lines.extend(_render_movie(movie, rank))

    return "\n".join(lines).rstrip() + "\n"


def _render_overview(movies: list[Movie]) -> list[str]:
    """榜单速览表格，同时充当目录（用显式锚点跳转到详情）。"""
    lines = [
        "## 榜单速览",
        "",
        "| 排名 | 评分 | 片名 | 导演 | 评论 |",
        "| ---: | ---: | --- | --- | ---: |",
    ]
    for rank, movie in enumerate(movies, start=1):
        lines.append(
            f"| {rank} | {_format_rate(movie.rate)} "
            f"| [{_escape_cell(movie.title)}](#movie-{rank}) "
            f"| {_escape_cell(' / '.join(movie.directors[:2]) or '—')} "
            f"| {len(movie.comments) or '—'} |"
        )
    lines.append("")
    return lines


def _render_movie(movie: Movie, rank: int) -> list[str]:
    # 显式锚点让速览表格的链接在任何 Markdown 渲染器里都能稳定跳转
    # （中文标题自动生成的锚点在不同工具里规则不一致）。
    lines = [
        "---",
        "",
        f'<a id="movie-{rank}"></a>',
        f"## {rank}. {movie.title}",
        "",
        f"- **豆瓣评分**：{_format_rate(movie.rate)} / 10",
        f"- **导演**：{' / '.join(movie.directors) or '—'}",
    ]

    if movie.casts:
        casts = " / ".join(movie.casts[:5])
        if len(movie.casts) > 5:
            casts += " 等"
        lines.append(f"- **主演**：{casts}")
    if movie.url:
        lines.append(f"- **豆瓣链接**：<{movie.url}>")

    lines.extend(["", f"### 热门评论（{len(movie.comments)} 条，按点赞排序）", ""])

    if not movie.comments:
        lines.extend(["*暂无热门评论*", ""])
        return lines

    for index, comment in enumerate(movie.comments, start=1):
        lines.append(
            f"**{index}. {_format_stars(comment.rating)} · {comment.user} · "
            f"{comment.created_at or '—'} · 👍 {comment.votes}**"
        )
        lines.append("")
        # 多行评论逐行加 "> "，在 Markdown 里才会显示成一个连续的引用块。
        for text_line in (comment.content or "（无内容）").splitlines():
            lines.append(f"> {text_line}" if text_line.strip() else ">")
        lines.append("")

    return lines


def _format_rate(rate: float | None) -> str:
    return f"{rate:.1f}" if rate is not None else "暂无"


def _format_stars(rating: int | None) -> str:
    if rating is None:
        return "未评分"
    stars = max(0, min(5, int(rating)))
    return "★" * stars + "☆" * (5 - stars)


def _escape_cell(text: str) -> str:
    """转义 Markdown 表格里会破坏结构的字符。"""
    return text.replace("|", "\\|").replace("\n", " ").strip()
