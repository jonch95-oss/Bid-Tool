from __future__ import annotations

import csv
from pathlib import Path

from .asin_library import AsinLibrary
from .config import ToolConfig
from .keepa_client import KeepaClient
from .resolver import AsinResolver
from .sp_api_client import SpApiClient
from .types import InventoryRow, ProcessSummary, ResolvedRow


def load_input_rows(path: Path) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    with path.open("r", encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        for raw in reader:
            row = {k: (v or "") for k, v in raw.items()}
            rows.append(
                InventoryRow(
                    sku=row.get("sku", "").strip(),
                    title=row.get("title", "").strip(),
                    brand=row.get("brand", "").strip(),
                    model=row.get("model", "").strip(),
                    color=row.get("color", "").strip(),
                    capacity=(row.get("capacity") or row.get("cpacity") or "").strip(),
                    grade=row.get("grade", "").strip(),
                    asin_hint=row.get("asin", "").strip(),
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
        "grade",
        "asin_hint",
        "resolved_asin",
        "price",
        "currency",
        "source",
        "confidence",
        "status",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "sku": row.input_row.sku,
                    "title": row.input_row.title,
                    "brand": row.input_row.brand,
                    "model": row.input_row.model,
                    "color": row.input_row.color,
                    "capacity": row.input_row.capacity,
                    "grade": row.input_row.grade,
                    "asin_hint": row.input_row.asin_hint,
                    "resolved_asin": row.resolved_asin,
                    "price": f"{row.price:.2f}" if row.price is not None else "",
                    "currency": row.currency or "",
                    "source": row.source,
                    "confidence": f"{row.confidence:.2f}",
                    "status": row.status,
                    "notes": row.notes,
                }
            )


def run_enrichment(config: ToolConfig, input_csv: Path, output_csv: Path) -> ProcessSummary:
    library = AsinLibrary(config.db_path)
    keepa = KeepaClient(config.keepa_api_key)
    sp_api = SpApiClient(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        aws_session_token=config.aws_session_token,
        lwa_client_id=config.sp_api_client_id,
        lwa_client_secret=config.sp_api_client_secret,
        lwa_refresh_token=config.sp_api_refresh_token,
        region=config.aws_region,
        marketplace_id=config.amazon_marketplace_id,
        endpoint_base=config.sp_api_base_url,
    )
    resolver = AsinResolver(
        library=library,
        keepa=keepa,
        sp_api=sp_api,
        marketplace_id=config.amazon_marketplace_id,
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
    return ProcessSummary(total_rows=total, resolved_rows=resolved, unresolved_rows=unresolved)

