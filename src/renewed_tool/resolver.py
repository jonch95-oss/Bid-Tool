from __future__ import annotations

from .asin_library import AsinLibrary
from .keepa_client import KeepaClient, KeepaClientError
from .normalization import normalize_capacity, normalize_color, normalize_grade, normalize_model, norm_text
from .sp_api_client import SpApiClient, SpApiClientError
from .types import AsinLibraryRecord, InventoryRow, LookupKey, MatchResult, PriceSnapshot, ProductSpec, ResolvedRow


class AsinResolver:
    def __init__(
        self,
        library: AsinLibrary,
        keepa: KeepaClient,
        sp_api: SpApiClient,
        marketplace_id: str,
        keepa_domain: int = 1,
        candidate_limit: int = 25,
    ) -> None:
        self.asin_library = library
        self.keepa_client = keepa
        self.sp_api_client = sp_api
        self.marketplace_id = marketplace_id
        self.keepa_domain = keepa_domain
        self.candidate_limit = candidate_limit

    def enrich_row(self, row: InventoryRow) -> ResolvedRow:
        key = self._lookup_key(row, self.marketplace_id)
        spec = ProductSpec(
            model=row.model,
            color=row.color,
            capacity=row.capacity,
            grade=row.grade,
        )

        if row.asin_hint:
            try:
                hinted = self._resolve_from_asin(row, spec, row.asin_hint, "csv_hint+spapi")
                if hinted.status == "ok":
                    self.asin_library.upsert(
                        AsinLibraryRecord(
                            key_model=key.model,
                            key_color=key.color,
                            key_capacity=key.capacity,
                            key_grade=key.grade,
                            marketplace_id=key.marketplace_id,
                            asin=hinted.resolved_asin or "",
                            confidence=hinted.confidence,
                            source=hinted.source,
                            raw_title=row.title,
                        )
                    )
                    return hinted
            except SpApiClientError:
                # Treat CSV hints as optional and continue with normal flow.
                pass

        cached = self.asin_library.get(key)
        if cached:
            try:
                cached_result = self._resolve_from_asin(row, spec, cached.asin, "library")
                if cached_result.status == "ok":
                    return cached_result
            except SpApiClientError as exc:
                # Keepa fallback can still recover from stale cache entries.
                _ = exc

        try:
            query = " ".join(
                part for part in (row.model, row.capacity, row.color, row.title, row.brand) if part
            ).strip()
            keepa_candidates = self.keepa_client.search_candidates(
                query,
                domain=self.keepa_domain,
                limit=self.candidate_limit,
            )
        except KeepaClientError as exc:
            return ResolvedRow(
                input_row=row,
                resolved_asin="",
                price=None,
                currency=None,
                source="keepa",
                confidence=0.0,
                status="error",
                notes=f"Keepa request failed: {exc}",
            )

        if not keepa_candidates:
            return ResolvedRow(
                input_row=row,
                resolved_asin="",
                price=None,
                currency=None,
                source="keepa",
                confidence=0.0,
                status="unresolved",
                notes="No Keepa candidates found",
            )

        best: ResolvedRow | None = None
        for candidate in keepa_candidates:
            try:
                resolved = self._resolve_from_asin(row, spec, candidate.asin, "keepa+spapi")
            except SpApiClientError:
                continue

            if resolved.status != "ok":
                continue
            if best is None or resolved.confidence > best.confidence:
                best = resolved

        if best is None:
            return ResolvedRow(
                input_row=row,
                resolved_asin=None,
                price=None,
                currency=None,
                source="keepa+spapi",
                confidence=0.0,
                status="unresolved",
                notes="No candidate passed model/color/capacity/grade validation",
            )

        self.asin_library.upsert(
            AsinLibraryRecord(
                key_model=key.model,
                key_color=key.color,
                key_capacity=key.capacity,
                key_grade=key.grade,
                marketplace_id=key.marketplace_id,
                asin=best.resolved_asin or "",
                confidence=best.confidence,
                source=best.source,
                raw_title=row.title,
            )
        )
        return best

    def _resolve_from_asin(
        self,
        row: InventoryRow,
        spec: ProductSpec,
        asin: str,
        source: str,
    ) -> ResolvedRow:
        catalog = self.sp_api_client.get_catalog_item(asin)
        pricing = self.sp_api_client.get_pricing(asin)
        match = self._validate(spec, catalog.model, catalog.color, catalog.capacity, pricing)

        if not match.valid:
            return ResolvedRow(
                input_row=row,
                resolved_asin=asin,
                price=pricing.amount,
                currency=pricing.currency,
                source=source,
                confidence=0.0,
                status="unresolved",
                notes=match.reason,
            )

        return ResolvedRow(
            input_row=row,
            resolved_asin=asin,
            price=pricing.amount,
            currency=pricing.currency,
            source=source,
            confidence=match.score,
            status="ok",
            notes=match.reason,
        )

    @staticmethod
    def _lookup_key(row: InventoryRow, marketplace_id: str) -> LookupKey:
        return LookupKey(
            model=normalize_model(row.model),
            color=normalize_color(row.color),
            capacity=normalize_capacity(row.capacity),
            grade=normalize_grade(row.grade),
            marketplace_id=marketplace_id,
        )

    @staticmethod
    def _validate(
        requested: ProductSpec,
        found_model: str | None,
        found_color: str | None,
        found_capacity: str | None,
        pricing: PriceSnapshot,
    ) -> MatchResult:
        req_model = normalize_model(requested.model)
        req_color = normalize_color(requested.color)
        req_capacity = normalize_capacity(requested.capacity)
        req_grade = normalize_grade(requested.grade)

        cat_model = normalize_model(found_model)
        cat_color = normalize_color(found_color)
        cat_capacity = normalize_capacity(found_capacity)
        offer_grade = normalize_grade(pricing.condition)

        if req_model and req_model != cat_model:
            return MatchResult(False, 0.0, f"Model mismatch: expected '{req_model}', got '{cat_model}'")

        score = 0.55
        reasons = ["model matched"]

        if req_color:
            if req_color != cat_color:
                return MatchResult(False, 0.0, f"Color mismatch: expected '{req_color}', got '{cat_color}'")
            score += 0.15
            reasons.append("color matched")

        if req_capacity:
            if req_capacity != cat_capacity:
                return MatchResult(False, 0.0, f"Capacity mismatch: expected '{req_capacity}', got '{cat_capacity}'")
            score += 0.2
            reasons.append("capacity matched")

        if req_grade:
            if offer_grade and req_grade != offer_grade:
                return MatchResult(False, 0.0, f"Grade mismatch: expected '{req_grade}', got '{offer_grade}'")
            if offer_grade:
                score += 0.1
                reasons.append("grade matched")
            else:
                reasons.append("grade unavailable from offers")

        title_boost = 0.0
        if norm_text(found_model) and norm_text(requested.model) and norm_text(found_model) == norm_text(requested.model):
            title_boost = 0.05
            score += title_boost

        final = min(score, 1.0)
        reason = ", ".join(reasons)
        if title_boost:
            reason += ", exact model text boost"
        return MatchResult(True, final, reason)
