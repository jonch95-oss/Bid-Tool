from __future__ import annotations

import sqlite3
from pathlib import Path

from .types import AsinLibraryRecord, LookupKey


class AsinLibrary:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asin_library (
                key_model TEXT NOT NULL,
                key_color TEXT NOT NULL,
                key_capacity TEXT NOT NULL,
                key_grade TEXT NOT NULL,
                marketplace_id TEXT NOT NULL,
                asin TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                raw_title TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (key_model, key_color, key_capacity, key_grade, marketplace_id)
            )
            """
        )
        self._conn.commit()

    def get(self, key: LookupKey) -> AsinLibraryRecord | None:
        row = self._conn.execute(
            """
            SELECT key_model, key_color, key_capacity, key_grade, marketplace_id, asin, confidence, source, raw_title
            FROM asin_library
            WHERE key_model = ? AND key_color = ? AND key_capacity = ? AND key_grade = ? AND marketplace_id = ?
            """,
            (key.model, key.color, key.capacity, key.grade, key.marketplace_id),
        ).fetchone()
        if row is None:
            return None
        return AsinLibraryRecord(
            key_model=row["key_model"],
            key_color=row["key_color"],
            key_capacity=row["key_capacity"],
            key_grade=row["key_grade"],
            marketplace_id=row["marketplace_id"],
            asin=row["asin"],
            confidence=float(row["confidence"]),
            source=row["source"],
            raw_title=row["raw_title"],
        )

    def upsert(self, record: AsinLibraryRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO asin_library
              (key_model, key_color, key_capacity, key_grade, marketplace_id, asin, confidence, source, raw_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_model, key_color, key_capacity, key_grade, marketplace_id)
            DO UPDATE SET
              asin = excluded.asin,
              confidence = excluded.confidence,
              source = excluded.source,
              raw_title = excluded.raw_title,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                record.key_model,
                record.key_color,
                record.key_capacity,
                record.key_grade,
                record.marketplace_id,
                record.asin,
                record.confidence,
                record.source,
                record.raw_title,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

