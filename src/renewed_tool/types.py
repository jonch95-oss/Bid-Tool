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
    grade: str
    asin_hint: str
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LookupKey:
    model: str
    color: str
    capacity: str
    grade: str
    marketplace_id: str


@dataclass(slots=True)
class ProductSpec:
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
    key_model: str
    key_color: str
    key_capacity: str
    key_grade: str
    marketplace_id: str
    asin: str
    confidence: float
    source: str
    raw_title: str


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


@dataclass(slots=True)
class ProcessSummary:
    total_rows: int
    resolved_rows: int
    unresolved_rows: int
