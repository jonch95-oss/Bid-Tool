from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

from .types import AsinLibraryRecord, LibraryUpsertResult, LookupKey
from .normalization import normalize_capacity, normalize_color, normalize_model, norm_text


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
                key_brand TEXT NOT NULL DEFAULT '',
                key_model TEXT NOT NULL,
                key_color TEXT NOT NULL,
                key_storage TEXT NOT NULL,
                marketplace_id TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '',
                storage TEXT NOT NULL DEFAULT '',
                carrier TEXT NOT NULL DEFAULT '',
                us_spec TEXT NOT NULL DEFAULT '',
                asin TEXT NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                raw_title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (key_brand, key_model, key_color, key_storage, marketplace_id)
            )
            """
        )
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(asin_library)").fetchall()
        }
        migration_columns = [
            ("key_brand", "TEXT NOT NULL DEFAULT ''"),
            ("key_storage", "TEXT NOT NULL DEFAULT ''"),
            ("brand", "TEXT NOT NULL DEFAULT ''"),
            ("model", "TEXT NOT NULL DEFAULT ''"),
            ("color", "TEXT NOT NULL DEFAULT ''"),
            ("storage", "TEXT NOT NULL DEFAULT ''"),
            ("carrier", "TEXT NOT NULL DEFAULT ''"),
            ("us_spec", "TEXT NOT NULL DEFAULT ''"),
            ("locked", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ]
        for col_name, col_sql in migration_columns:
            if col_name not in columns:
                self._conn.execute(f"ALTER TABLE asin_library ADD COLUMN {col_name} {col_sql}")

        has_key_capacity = "key_capacity" in columns
        key_capacity_ref = "COALESCE(key_capacity, '')" if has_key_capacity else "''"
        self._conn.execute(
            f"""
            UPDATE asin_library
            SET
              key_storage = CASE WHEN COALESCE(key_storage, '') = '' THEN {key_capacity_ref} ELSE key_storage END,
              storage = CASE WHEN COALESCE(storage, '') = '' THEN {key_capacity_ref} ELSE storage END,
              model = CASE WHEN COALESCE(model, '') = '' THEN COALESCE(key_model, '') ELSE model END,
              color = CASE WHEN COALESCE(color, '') = '' THEN COALESCE(key_color, '') ELSE color END,
              key_brand = COALESCE(key_brand, ''),
              brand = COALESCE(brand, ''),
              carrier = COALESCE(carrier, ''),
              us_spec = COALESCE(us_spec, ''),
              locked = COALESCE(locked, 0)
            """
        )
        self._conn.commit()

    def get(self, key: LookupKey) -> AsinLibraryRecord | None:
        row = self._conn.execute(
            """
            SELECT
              key_brand, key_model, key_color, key_storage, marketplace_id,
              brand, model, color, storage, carrier, us_spec,
              asin, locked, confidence, source, raw_title, created_at, updated_at
            FROM asin_library
            WHERE key_brand = ? AND key_model = ? AND key_color = ? AND key_storage = ? AND marketplace_id = ?
            """,
            (key.brand, key.model, key.color, key.storage, key.marketplace_id),
        ).fetchone()
        if row is None:
            return None
        return AsinLibraryRecord(
            key_brand=row["key_brand"],
            key_model=row["key_model"],
            key_color=row["key_color"],
            key_storage=row["key_storage"],
            marketplace_id=row["marketplace_id"],
            brand=row["brand"],
            model=row["model"],
            color=row["color"],
            storage=row["storage"],
            carrier=row["carrier"],
            us_spec=row["us_spec"],
            asin=row["asin"],
            locked=bool(row["locked"]),
            confidence=float(row["confidence"]),
            source=row["source"],
            raw_title=row["raw_title"],
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def upsert(self, record: AsinLibraryRecord) -> None:
        self.upsert_auto(
            key=LookupKey(
                brand=record.key_brand,
                model=record.key_model,
                color=record.key_color,
                storage=record.key_storage,
                marketplace_id=record.marketplace_id,
            ),
            brand=record.brand,
            model=record.model,
            color=record.color,
            storage=record.storage,
            carrier=record.carrier,
            us_spec=record.us_spec,
            asin=record.asin,
            confidence=record.confidence,
            source=record.source,
            raw_title=record.raw_title,
        )

    def upsert_auto(
        self,
        *,
        key: LookupKey,
        brand: str,
        model: str,
        color: str,
        storage: str,
        carrier: str,
        us_spec: str,
        asin: str,
        confidence: float,
        source: str,
        raw_title: str,
    ) -> LibraryUpsertResult:
        existing = self.get(
            LookupKey(
                brand=key.brand,
                model=key.model,
                color=key.color,
                storage=key.storage,
                marketplace_id=key.marketplace_id,
            )
        )
        if existing and existing.locked and existing.asin != asin:
            return LibraryUpsertResult(action="conflict", record=existing, conflict_asin=existing.asin)
        locked_value = int(existing.locked) if existing else 0
        self._conn.execute(
            """
            INSERT INTO asin_library
              (
                key_brand, key_model, key_color, key_storage, marketplace_id,
                brand, model, color, storage, carrier, us_spec,
                asin, locked, confidence, source, raw_title
              )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_brand, key_model, key_color, key_storage, marketplace_id)
            DO UPDATE SET
              asin = excluded.asin,
              brand = excluded.brand,
              model = excluded.model,
              color = excluded.color,
              storage = excluded.storage,
              carrier = excluded.carrier,
              us_spec = excluded.us_spec,
              locked = asin_library.locked,
              confidence = excluded.confidence,
              source = excluded.source,
              raw_title = excluded.raw_title,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                key.brand,
                key.model,
                key.color,
                key.storage,
                key.marketplace_id,
                brand,
                model,
                color,
                storage,
                carrier,
                us_spec,
                asin,
                locked_value,
                confidence,
                source,
                raw_title,
            ),
        )
        self._conn.commit()
        updated = self.get(key)
        if not updated:
            raise RuntimeError("Failed to read updated ASIN library record.")
        return LibraryUpsertResult(action="updated" if existing else "inserted", record=updated)

    def list_entries(self, marketplace_id: str) -> list[AsinLibraryRecord]:
        rows = self._conn.execute(
            """
            SELECT
              key_brand, key_model, key_color, key_storage, marketplace_id,
              brand, model, color, storage, carrier, us_spec,
              asin, locked, confidence, source, raw_title, created_at, updated_at
            FROM asin_library
            WHERE marketplace_id = ?
            ORDER BY brand COLLATE NOCASE, model COLLATE NOCASE, color COLLATE NOCASE, storage COLLATE NOCASE
            """,
            (marketplace_id,),
        ).fetchall()
        return [
            AsinLibraryRecord(
                key_brand=row["key_brand"],
                key_model=row["key_model"],
                key_color=row["key_color"],
                key_storage=row["key_storage"],
                marketplace_id=row["marketplace_id"],
                brand=row["brand"],
                model=row["model"],
                color=row["color"],
                storage=row["storage"],
                carrier=row["carrier"],
                us_spec=row["us_spec"],
                asin=row["asin"],
                locked=bool(row["locked"]),
                confidence=float(row["confidence"]),
                source=row["source"],
                raw_title=row["raw_title"],
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
            )
            for row in rows
        ]

    def upsert_manual_entry(
        self,
        *,
        marketplace_id: str,
        brand: str,
        model: str,
        color: str,
        storage: str,
        carrier: str,
        us_spec: str,
        asin: str,
        locked: bool,
    ) -> None:
        record = AsinLibraryRecord(
            key_brand=norm_text(brand),
            key_model=normalize_model(model),
            key_color=normalize_color(color),
            key_storage=normalize_capacity(storage),
            marketplace_id=marketplace_id,
            brand=brand.strip(),
            model=model.strip(),
            color=color.strip(),
            storage=storage.strip(),
            carrier=carrier.strip(),
            us_spec=us_spec.strip(),
            asin=asin.strip(),
            locked=locked,
            confidence=1.0,
            source="manual",
            raw_title="",
        )
        self._conn.execute(
            """
            INSERT INTO asin_library
              (
                key_brand, key_model, key_color, key_storage, marketplace_id,
                brand, model, color, storage, carrier, us_spec,
                asin, locked, confidence, source, raw_title
              )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_brand, key_model, key_color, key_storage, marketplace_id)
            DO UPDATE SET
              brand = excluded.brand,
              model = excluded.model,
              color = excluded.color,
              storage = excluded.storage,
              carrier = excluded.carrier,
              us_spec = excluded.us_spec,
              asin = excluded.asin,
              locked = excluded.locked,
              confidence = excluded.confidence,
              source = excluded.source,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                record.key_brand,
                record.key_model,
                record.key_color,
                record.key_storage,
                record.marketplace_id,
                record.brand,
                record.model,
                record.color,
                record.storage,
                record.carrier,
                record.us_spec,
                record.asin,
                int(record.locked),
                record.confidence,
                record.source,
                record.raw_title,
            ),
        )
        self._conn.commit()

    def set_locked(self, key: LookupKey, locked: bool) -> None:
        self._conn.execute(
            """
            UPDATE asin_library
            SET locked = ?, updated_at = CURRENT_TIMESTAMP
            WHERE key_brand = ? AND key_model = ? AND key_color = ? AND key_storage = ? AND marketplace_id = ?
            """,
            (int(locked), key.brand, key.model, key.color, key.storage, key.marketplace_id),
        )
        self._conn.commit()

    def delete(self, key: LookupKey) -> None:
        self._conn.execute(
            """
            DELETE FROM asin_library
            WHERE key_brand = ? AND key_model = ? AND key_color = ? AND key_storage = ? AND marketplace_id = ?
            """,
            (key.brand, key.model, key.color, key.storage, key.marketplace_id),
        )
        self._conn.commit()

    def clear(self, marketplace_id: str) -> None:
        self._conn.execute("DELETE FROM asin_library WHERE marketplace_id = ?", (marketplace_id,))
        self._conn.commit()

    def export_json(self, marketplace_id: str) -> str:
        entries = self.list_entries(marketplace_id)
        payload = [
            {
                "brand": e.brand,
                "model": e.model,
                "color": e.color,
                "storage": e.storage,
                "carrier": e.carrier,
                "us_spec": e.us_spec,
                "asin": e.asin,
                "locked": e.locked,
                "created_at": e.created_at,
                "updated_at": e.updated_at,
            }
            for e in entries
        ]
        return json.dumps(payload, indent=2)

    def export_csv(self, marketplace_id: str) -> str:
        entries = self.list_entries(marketplace_id)
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "brand",
                "model",
                "color",
                "storage",
                "carrier",
                "us_spec",
                "asin",
                "locked",
                "created_at",
                "updated_at",
            ],
        )
        writer.writeheader()
        for e in entries:
            writer.writerow(
                {
                    "brand": e.brand,
                    "model": e.model,
                    "color": e.color,
                    "storage": e.storage,
                    "carrier": e.carrier,
                    "us_spec": e.us_spec,
                    "asin": e.asin,
                    "locked": "1" if e.locked else "0",
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                }
            )
        return output.getvalue()

    def import_json(self, marketplace_id: str, payload: str) -> int:
        data = json.loads(payload)
        if not isinstance(data, list):
            raise ValueError("JSON backup must be a list of entries.")
        count = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            self.upsert_manual_entry(
                marketplace_id=marketplace_id,
                brand=str(item.get("brand") or ""),
                model=str(item.get("model") or ""),
                color=str(item.get("color") or ""),
                storage=str(item.get("storage") or ""),
                carrier=str(item.get("carrier") or ""),
                us_spec=str(item.get("us_spec") or ""),
                asin=str(item.get("asin") or ""),
                locked=bool(item.get("locked")),
            )
            count += 1
        return count

    def close(self) -> None:
        self._conn.close()

