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
    capacity_inferred_from_title: bool = False
    needs_input_capacity: bool = False
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
    excellent_price: float | None
    good_price: float | None
    acceptable_price: float | None
    lowest_fba: float | None
    lowest_fbm: float | None
    buy_box: float | None
    total_offers: int
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
    raw_title: str = ""
    created_at: str = ""
    updated_at: str = ""
    id: int | None = None


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
    excellent_price: float | None = None
    good_price: float | None = None
    acceptable_price: float | None = None
    lowest_fba: float | None = None
    lowest_fbm: float | None = None
    buy_box: float | None = None
    total_offers: int = 0


@dataclass(slots=True)
class ProcessSummary:
    total_rows: int
    resolved_rows: int
    unresolved_rows: int
    library_hits: int
    keepa_resolved: int
    skipped: int
    conflicts: int
    needs_input: int = 0
