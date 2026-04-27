from __future__ import annotations

import csv
import re
from pathlib import Path

from .asin_library import AsinLibrary
from .config import ToolConfig
from .keepa_client import KeepaClient
from .normalization import normalize_color
from .resolver import AsinResolver
from .sp_api_client import SpApiClient
from .types import InventoryRow, ProcessSummary, ResolvedRow


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _pick_cell(row: dict[str, str], *aliases: str) -> str:
    for alias in aliases:
        normalized = _normalize_header(alias)
        if normalized in row and row[normalized]:
            return row[normalized].strip()
    return ""


def _extract_capacity_from_text(text: str) -> str:
    # Examples matched: "128GB", "256 gb", "1TB", "1.5 TB"
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(tb|gb)\b", text or "", flags=re.IGNORECASE)
    if not match:
        return ""
    number = match.group(1)
    unit = match.group(2).upper()
    return f"{number}{unit}"


def _append_note(raw_row: dict[str, str], note: str) -> None:
    existing = (raw_row.get("_inference_notes") or "").strip()
    if not existing:
        raw_row["_inference_notes"] = note
        return
    notes = [part.strip() for part in existing.split(";") if part.strip()]
    if note not in notes:
        notes.append(note)
    raw_row["_inference_notes"] = "; ".join(notes)


def _extract_brand_from_text(text: str) -> str:
    candidates = (
        "apple",
        "samsung",
        "google",
        "motorola",
        "oneplus",
        "nokia",
        "sony",
        "lg",
        "xiaomi",
    )
    lowered = (text or "").lower()
    for brand in candidates:
        if re.search(rf"\b{re.escape(brand)}\b", lowered):
            return brand.title()
    return ""


def _extract_color_from_text(text: str) -> str:
    lowered = (text or "").lower()
    # include full names and common short codes frequently seen in lot sheets
    candidates = (
        "black",
        "blk",
        "bk",
        "gray",
        "grey",
        "gry",
        "gr",
        "gra",
        "blue",
        "blu",
        "bl",
        "white",
        "wht",
        "wt",
        "red",
        "gold",
        "gld",
        "silver",
        "slv",
        "sil",
        "green",
        "grn",
        "purple",
        "pur",
        "pink",
        "pnk",
    )
    for color in candidates:
        if re.search(rf"\b{re.escape(color)}\b", lowered):
            return normalize_color(color)
    return ""


def load_input_rows(path: Path) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    with path.open("r", encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        for raw in reader:
            row = {_normalize_header(k or ""): (v or "") for k, v in raw.items()}
            title = _pick_cell(row, "title", "item description", "item_description", "description")
            capacity = _pick_cell(row, "capacity", "cpacity")
            capacity_inferred_from_title = False
            if not capacity:
                inferred_capacity = _extract_capacity_from_text(title)
                if inferred_capacity:
                    capacity = inferred_capacity
                    capacity_inferred_from_title = True
                    _append_note(row, "capacity inferred from title")
            brand = _pick_cell(row, "brand", "oem")
            if not brand:
                brand = _extract_brand_from_text(title)
                if brand:
                    _append_note(row, "brand inferred from title")
            color = _pick_cell(row, "color")
            if not color:
                color = _extract_color_from_text(title)
                if color:
                    _append_note(row, "color inferred from title")
            rows.append(
                InventoryRow(
                    sku=_pick_cell(row, "sku", "lot #", "lot#", "lot number", "lot"),
                    title=title,
                    brand=brand,
                    model=_pick_cell(row, "model"),
                    color=color,
                    capacity=capacity,
                    carrier=_pick_cell(row, "carrier"),
                    us_spec=_pick_cell(row, "us spec", "us_spec", "usspec"),
                    grade=_pick_cell(row, "grade"),
                    asin_hint=_pick_cell(row, "asin", "asin hint", "asinhint"),
                    capacity_inferred_from_title=capacity_inferred_from_title,
                    needs_input_capacity=not bool(capacity),
                    raw=row,
                )
            )
    return rows


def write_output_rows(path: Path, rows: list[ResolvedRow]) -> None:
    fieldnames = [
        "sku",
        "title",
        "brand",
        "model",
        "color",
        "capacity",
        "carrier",
        "us_spec",
        "grade",
        "asin_hint",
        "resolved_asin",
        "price",
        "excellent_price",
        "good_price",
        "acceptable_price",
        "lowest_fba",
        "lowest_fbm",
        "buy_box",
        "total_offers",
        "currency",
        "source",
        "confidence",
        "status",
        "notes",
        "library_hit",
    ]
    with path.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            combined_notes = row.notes
            inference_notes = (row.input_row.raw.get("_inference_notes") or "").strip()
            if inference_notes:
                combined_notes = f"{combined_notes}; {inference_notes}" if combined_notes else inference_notes
            writer.writerow(
                {
                    "sku": row.input_row.sku,
                    "title": row.input_row.title,
                    "brand": row.input_row.brand,
                    "model": row.input_row.model,
                    "color": row.input_row.color,
                    "capacity": row.input_row.capacity,
                    "carrier": row.input_row.carrier,
                    "us_spec": row.input_row.us_spec,
                    "grade": row.input_row.grade,
                    "asin_hint": row.input_row.asin_hint,
                    "resolved_asin": row.resolved_asin,
                    "price": f"{row.price:.2f}" if row.price is not None else "",
                    "excellent_price": f"{row.excellent_price:.2f}" if row.excellent_price is not None else "",
                    "good_price": f"{row.good_price:.2f}" if row.good_price is not None else "",
                    "acceptable_price": f"{row.acceptable_price:.2f}" if row.acceptable_price is not None else "",
                    "lowest_fba": f"{row.lowest_fba:.2f}" if row.lowest_fba is not None else "",
                    "lowest_fbm": f"{row.lowest_fbm:.2f}" if row.lowest_fbm is not None else "",
                    "buy_box": f"{row.buy_box:.2f}" if row.buy_box is not None else "",
                    "total_offers": row.total_offers,
                    "currency": row.currency or "",
                    "source": row.source,
                    "confidence": f"{row.confidence:.2f}",
                    "status": row.status,
                    "notes": combined_notes,
                    "library_hit": "yes" if row.library_hit else "no",
                }
            )


def run_enrichment(config: ToolConfig, input_csv: Path, output_csv: Path) -> ProcessSummary:
    library = AsinLibrary(config.db_path)
    keepa = KeepaClient(config.keepa_api_key)
    sp_api = SpApiClient(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        aws_session_token=config.aws_session_token,
        aws_role_arn=config.aws_role_arn,
        lwa_client_id=config.lwa_client_id,
        lwa_client_secret=config.lwa_client_secret,
        lwa_refresh_token=config.lwa_refresh_token,
        region=config.aws_region,
        marketplace_id=config.spapi_marketplace_id,
        endpoint_base=config.sp_api_base_url,
    )
    resolver = AsinResolver(
        library=library,
        keepa=keepa,
        sp_api=sp_api,
        marketplace_id=config.spapi_marketplace_id,
        keepa_domain=config.keepa_domain,
        candidate_limit=config.candidate_limit,
    )
    try:
        rows = load_input_rows(input_csv)
        enriched = [resolver.enrich_row(row) for row in rows]
        write_output_rows(output_csv, enriched)
    finally:
        library.close()

    total = len(enriched)
    resolved = sum(1 for row in enriched if row.status == "ok")
    unresolved = total - resolved
    library_hits = sum(1 for row in enriched if row.source == "library")
    keepa_resolved = sum(1 for row in enriched if row.source == "keepa+spapi")
    conflicts = sum(1 for row in enriched if row.status == "conflict")
    skipped = sum(1 for row in enriched if row.status == "skipped")
    needs_input = sum(1 for row in enriched if row.status == "needs_input")
    return ProcessSummary(
        total_rows=total,
        resolved_rows=resolved,
        unresolved_rows=unresolved,
        library_hits=library_hits,
        keepa_resolved=keepa_resolved,
        conflicts=conflicts,
        skipped=skipped,
        needs_input=needs_input,
    )

