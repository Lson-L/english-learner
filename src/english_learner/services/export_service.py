"""学习数据导出：JSON 全量备份 / CSV 分表。

导出的数据来自 SQLite 的六张表：
`profile` / `articles` / `words` / `highlights` / `review_logs` / `daily_stats`。

设计：
- 纯 Python，不依赖 PySide6；界面只负责选路径，导出本身可以放进 TaskWorker；
- JSON 是一次性完整快照，适合备份 / 迁移；
- CSV 每个表一个文件，`utf-8-sig` 编码（Windows Excel 直接打开中文不乱码），
  列表字段（如文章的目标词）用 `; ` 连接；
- 导出是只读操作，不会修改数据库。

Java 对照：≈ 一个 `DataExportService`，把 Repository 查询结果映射成
POJO → JSON / CSV；格式细节集中在本文件，界面不需要知道列名。
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import __version__
from ..models.entities import now_iso
from ..storage.repositories import (
    ArticleRepo,
    ProfileRepo,
    ReviewRepo,
    StatsRepo,
    WordRepo,
)

logger = logging.getLogger(__name__)

# 表名 → 界面展示名（导出结果摘要用）。
TABLE_LABELS: dict[str, str] = {
    "profile": "学习画像",
    "articles": "文章",
    "words": "生词",
    "highlights": "高亮",
    "review_logs": "复习记录",
    "daily_stats": "打卡天数",
}

# 摘要展示顺序：先看学习内容，再看历史。
SUMMARY_ORDER: tuple[str, ...] = (
    "articles",
    "words",
    "highlights",
    "review_logs",
    "daily_stats",
    "profile",
)

# CSV 列顺序固定，保证多次导出可直接 diff。
CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "profile": (
        "cefr_level",
        "vocab_estimate",
        "strengths",
        "weaknesses",
        "comment",
        "assessed_at",
        "updated_at",
    ),
    "articles": (
        "id",
        "article_date",
        "title",
        "level",
        "topic",
        "word_count",
        "target_words",
        "read_at",
        "created_at",
        "content",
    ),
    "words": (
        "id",
        "lemma",
        "display_form",
        "phonetic",
        "pos",
        "meaning_cn",
        "meaning_en",
        "example_en",
        "example_cn",
        "context_sentence",
        "article_id",
        "status",
        "ease",
        "interval_days",
        "repetitions",
        "due_date",
        "created_at",
        "updated_at",
    ),
    "highlights": (
        "id",
        "article_id",
        "word_id",
        "start_offset",
        "end_offset",
        "sentence",
    ),
    "review_logs": (
        "id",
        "word_id",
        "rating",
        "prev_interval",
        "next_interval",
        "reviewed_at",
    ),
    "daily_stats": (
        "stat_date",
        "article_read",
        "words_added",
        "reviews_done",
    ),
}


@dataclass(frozen=True)
class ExportResult:
    """导出结果：生成的文件与各表记录数。"""

    files: tuple[Path, ...]
    counts: dict[str, int]

    def summary(self) -> str:
        parts = [f"{TABLE_LABELS.get(key, key)} {self.counts.get(key, 0)}" for key in SUMMARY_ORDER]
        return " · ".join(parts)


class ExportService:
    """把数据库内容导出成 JSON / CSV。所有方法都是同步阻塞的本地 IO。"""

    def __init__(
        self,
        profiles: ProfileRepo,
        articles: ArticleRepo,
        words: WordRepo,
        reviews: ReviewRepo,
        stats: StatsRepo,
    ) -> None:
        self._profiles = profiles
        self._articles = articles
        self._words = words
        self._reviews = reviews
        self._stats = stats

    # ------------------------------------------------------------------ 快照
    def snapshot(self) -> dict[str, object]:
        """一次性读出全部表，供 JSON / CSV 共用。"""
        profile = self._profiles.get()
        return {
            "profile": asdict(profile) if profile is not None else None,
            "articles": [asdict(item) for item in self._articles.list_all()],
            "words": [asdict(item) for item in self._words.list_all()],
            "highlights": [asdict(item) for item in self._words.list_all_highlights()],
            "review_logs": [asdict(item) for item in self._reviews.list_all()],
            "daily_stats": [asdict(item) for item in self._stats.list_all()],
        }

    @staticmethod
    def counts(snapshot: dict[str, object]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key, value in snapshot.items():
            if key == "profile":
                counts[key] = 1 if value is not None else 0
            elif isinstance(value, list):
                counts[key] = len(value)
            else:
                counts[key] = 0
        return counts

    # -------------------------------------------------------------------- JSON
    def export_json(self, path: Path) -> ExportResult:
        """导出单文件 JSON 快照（原子写入，避免半截文件）。"""
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        target.parent.mkdir(parents=True, exist_ok=True)

        data = self.snapshot()
        counts = self.counts(data)
        payload = {
            "app": "Daily English",
            "version": __version__,
            "exported_at": now_iso(),
            "counts": counts,
            **data,
        }

        temp_path = target.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(target)
        logger.info("导出 JSON：%s（%s）", target, payload["counts"])
        return ExportResult(files=(target,), counts=counts)

    # --------------------------------------------------------------------- CSV
    def export_csv(self, directory: Path) -> ExportResult:
        """每张表导出一个 CSV，返回所有文件路径。"""
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)

        data = self.snapshot()
        counts = self.counts(data)
        files: list[Path] = []
        for table, columns in CSV_COLUMNS.items():
            rows = self._rows_for(table, data)
            path = target_dir / f"{table}.csv"
            self._write_csv(path, columns, rows)
            files.append(path)

        logger.info("导出 CSV：%s（%s）", target_dir, counts)
        return ExportResult(files=tuple(files), counts=counts)

    @staticmethod
    def _rows_for(table: str, data: dict[str, object]) -> list[dict[str, object]]:
        value = data.get(table)
        if table == "profile":
            return [value] if isinstance(value, dict) else []
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _write_csv(
        path: Path,
        columns: tuple[str, ...],
        rows: list[dict[str, object]],
    ) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {column: ExportService._csv_value(row.get(column)) for column in columns}
                )

    @staticmethod
    def _csv_value(value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return "; ".join(str(item) for item in value)
        return value
