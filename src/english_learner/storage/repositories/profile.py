"""用户画像仓库。"""

from __future__ import annotations

from ...models.entities import (
    Profile,
    now_iso,
)
from .base import _BaseRepo


class ProfileRepo(_BaseRepo):
    """profile 表只有一行（id = 1）。"""

    _SAVE_SQL = """
        INSERT INTO profile (
            id, cefr_level, vocab_estimate, strengths_json, weaknesses_json,
            comment, assessed_at, updated_at, pending_level, pending_count,
            level_confidence, level_updated_reason
        ) VALUES (
            1, :cefr_level, :vocab_estimate, :strengths_json, :weaknesses_json,
            :comment, :assessed_at, :updated_at, :pending_level, :pending_count,
            :level_confidence, :level_updated_reason
        )
        ON CONFLICT(id) DO UPDATE SET
            cefr_level      = excluded.cefr_level,
            vocab_estimate  = excluded.vocab_estimate,
            strengths_json  = excluded.strengths_json,
            weaknesses_json = excluded.weaknesses_json,
            comment         = excluded.comment,
            assessed_at     = excluded.assessed_at,
            updated_at      = excluded.updated_at,
            pending_level   = excluded.pending_level,
            pending_count   = excluded.pending_count,
            level_confidence = excluded.level_confidence,
            level_updated_reason = excluded.level_updated_reason
    """

    def get(self) -> Profile | None:
        row = self._db.query_one("SELECT * FROM profile WHERE id = 1")
        return Profile.from_row(row) if row is not None else None

    def save(self, profile: Profile) -> Profile:
        params = profile.to_params()
        if not params["assessed_at"]:
            params["assessed_at"] = now_iso()
        if not params["updated_at"]:
            params["updated_at"] = params["assessed_at"]
        self._db.execute(self._SAVE_SQL, params)
        stored = self.get()
        assert stored is not None  # 刚写完必然能读回
        return stored
