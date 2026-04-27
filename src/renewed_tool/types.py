from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InventoryRow:
    sku: str
    title: str
    brand: str
    model: str
    color: str
    capacity: str
    carrier: str
    us_spec: str
    grade: str
    asin_hint: str
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LookupKey:
    brand: str
    model: str
    color: str
    storage: str
    marketplace_id: str


@dataclass(slots=True)
class ProductSpec:
    brand: str
    model: str
    color: str
    capacity: str
    grade: str


@dataclass(slots=True)
class KeepaCandidate:
    asin: str
    title: str
    brand: str
    model: str
    color: str
    capacity: str
    attributes_text: str
    raw: dict[str, Any]


@dataclass(slots=True)
class CatalogSnapshot:
    asin: str
    title: str
    model: str
    color: str
    capacity: str


@dataclass(slots=True)
class PriceSnapshot:
    asin: str
    amount: float | None
    currency: str | None
    condition: str | None
    raw: dict[str, Any]


@dataclass(slots=True)
class AsinLibraryRecord:
    key_brand: str
    key_model: str
    key_color: str
    key_storage: str
    marketplace_id: str
    brand: str
    model: str
    color: str
    storage: str
    carrier: str
    us_spec: str
    asin: str
    confidence: float
    source: str
    locked: bool
    raw_title: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class LibraryUpsertResult:
    action: str
    record: AsinLibraryRecord
    conflict_asin: str | None = None


@dataclass(slots=True)
class MatchResult:
    valid: bool
    score: float
    reason: str


@dataclass(slots=True)
class ResolvedRow:
    input_row: InventoryRow
    resolved_asin: str | None
    price: float | None
    currency: str | None
    source: str
    confidence: float
    status: str
    notes: str
    library_hit: bool


@dataclass(slots=True)
class ProcessSummary:
    total_rows: int
    resolved_rows: int
    unresolved_rows: int
    library_hits: int
    keepa_resolved: int
    skipped: int
    conflicts: int
