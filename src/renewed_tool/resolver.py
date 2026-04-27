from __future__ import annotations

import logging

from .asin_library import AsinLibrary
from .keepa_client import KeepaClient, KeepaClientError
from .normalization import normalize_capacity, normalize_color, normalize_grade, normalize_model, norm_text
from .sp_api_client import SpApiClient, SpApiClientError
from .types import InventoryRow, LookupKey, MatchResult, PriceSnapshot, ProductSpec, ResolvedRow

LOGGER = logging.getLogger(__name__)


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
        # Avoid sending empty/noisy search queries to Keepa.
        if not any(part.strip() for part in (row.model, row.title, row.brand)):
            return ResolvedRow(
                input_row=row,
                resolved_asin="",
                price=None,
                currency=None,
                source="input-validation",
                confidence=0.0,
                status="skipped",
                notes="Skipped row: missing model/title/brand",
                library_hit=False,
                excellent_price=None,
                good_price=None,
                acceptable_price=None,
                lowest_fba=None,
                lowest_fbm=None,
                buy_box=None,
                total_offers=0,
            )

        key = self._lookup_key(row, self.marketplace_id)
        inference_notes: list[str] = []
        if row.capacity_inferred_from_title:
            inference_notes.append("capacity inferred from title")

        spec = ProductSpec(
            brand=row.brand,
            model=row.model,
            color=row.color,
            capacity=row.capacity,
            grade=row.grade,
        )

        # Library comes first for cache-hit behavior.
        cached = self.asin_library.get(key)
        if cached:
            try:
                cached_result = self._resolve_from_asin(
                    row,
                    spec,
                    cached.asin,
                    "library",
                    library_hit=True,
                    extra_notes=inference_notes,
                )
                if cached_result.status == "ok":
                    return cached_result
            except SpApiClientError:
                # Keepa fallback can still recover from stale library entries.
                pass

        if row.asin_hint:
            try:
                hinted = self._resolve_from_asin(
                    row,
                    spec,
                    row.asin_hint,
                    "csv_hint+spapi",
                    extra_notes=inference_notes,
                )
                if hinted.status == "ok":
                    self.asin_library.upsert_auto(
                        key=key,
                        brand=row.brand,
                        model=row.model,
                        color=row.color,
                        storage=row.capacity,
                        carrier=row.carrier,
                        us_spec=row.us_spec,
                        asin=hinted.resolved_asin or "",
                        confidence=hinted.confidence,
                        source=hinted.source,
                        raw_title=row.title,
                    )
                    return hinted
            except SpApiClientError:
                # Treat CSV hints as optional and continue with normal flow.
                pass

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
                library_hit=False,
                excellent_price=None,
                good_price=None,
                acceptable_price=None,
                lowest_fba=None,
                lowest_fbm=None,
                buy_box=None,
                total_offers=0,
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
                library_hit=False,
                excellent_price=None,
                good_price=None,
                acceptable_price=None,
                lowest_fba=None,
                lowest_fbm=None,
                buy_box=None,
                total_offers=0,
            )

        best: ResolvedRow | None = None
        for candidate in keepa_candidates:
            try:
                resolved = self._resolve_from_asin(
                    row,
                    spec,
                    candidate.asin,
                    "keepa+spapi",
                    extra_notes=inference_notes,
                )
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
                library_hit=False,
                excellent_price=None,
                good_price=None,
                acceptable_price=None,
                lowest_fba=None,
                lowest_fbm=None,
                buy_box=None,
                total_offers=0,
            )

        upsert_result = self.asin_library.upsert_auto(
            key=key,
            brand=row.brand,
            model=row.model,
            color=row.color,
            storage=row.capacity,
            carrier=row.carrier,
            us_spec=row.us_spec,
            asin=best.resolved_asin or "",
            confidence=best.confidence,
            source=best.source,
            raw_title=row.title,
        )
        if upsert_result.action == "conflict":
            return ResolvedRow(
                input_row=row,
                resolved_asin=best.resolved_asin,
                price=best.price,
                currency=best.currency,
                source=best.source,
                confidence=best.confidence,
                status="conflict",
                notes=(
                    f"Locked library entry has ASIN {upsert_result.conflict_asin}, "
                    f"Keepa returned ASIN {best.resolved_asin}"
                ),
                library_hit=False,
                excellent_price=best.excellent_price,
                good_price=best.good_price,
                acceptable_price=best.acceptable_price,
                lowest_fba=best.lowest_fba,
                lowest_fbm=best.lowest_fbm,
                buy_box=best.buy_box,
                total_offers=best.total_offers,
            )
        return best

    def _resolve_from_asin(
        self,
        row: InventoryRow,
        spec: ProductSpec,
        asin: str,
        source: str,
        library_hit: bool = False,
        extra_notes: list[str] | None = None,
    ) -> ResolvedRow:
        catalog = self.sp_api_client.get_catalog_item(asin)
        pricing = self.sp_api_client.get_pricing(asin)
        requested_grade_tier = self._requested_grade_tier(spec.grade)
        grade_matched_price = self._price_for_grade_tier(pricing, requested_grade_tier)
        price_choice = (
            grade_matched_price
            or pricing.buy_box
            or pricing.lowest_fba
            or pricing.lowest_fbm
            or pricing.excellent_price
            or pricing.good_price
            or pricing.acceptable_price
        )
        match = self._validate(spec, catalog.model, catalog.color, catalog.capacity, pricing)

        merged_reason = match.reason
        if extra_notes:
            merged_reason = ", ".join([merged_reason, *extra_notes]) if merged_reason else ", ".join(extra_notes)

        if requested_grade_tier and grade_matched_price is None:
            merged_reason = (
                f"{merged_reason}, no price found for requested grade tier '{requested_grade_tier}'"
                if merged_reason
                else f"no price found for requested grade tier '{requested_grade_tier}'"
            )
            return ResolvedRow(
                input_row=row,
                resolved_asin=asin,
                price=price_choice,
                currency=pricing.currency,
                source=source,
                confidence=0.0,
                status="unresolved-no-price-for-tier",
                notes=merged_reason,
                library_hit=library_hit,
                excellent_price=pricing.excellent_price,
                good_price=pricing.good_price,
                acceptable_price=pricing.acceptable_price,
                lowest_fba=pricing.lowest_fba,
                lowest_fbm=pricing.lowest_fbm,
                buy_box=pricing.buy_box,
                total_offers=pricing.total_offers,
                condition_tier_used=requested_grade_tier,
            )

        if not match.valid:
            return ResolvedRow(
                input_row=row,
                resolved_asin=asin,
                price=price_choice,
                currency=pricing.currency,
                source=source,
                confidence=0.0,
                status="unresolved",
                notes=merged_reason,
                library_hit=library_hit,
                excellent_price=pricing.excellent_price,
                good_price=pricing.good_price,
                acceptable_price=pricing.acceptable_price,
                lowest_fba=pricing.lowest_fba,
                lowest_fbm=pricing.lowest_fbm,
                buy_box=pricing.buy_box,
                total_offers=pricing.total_offers,
                condition_tier_used=requested_grade_tier,
            )

        return ResolvedRow(
            input_row=row,
            resolved_asin=asin,
            price=price_choice,
            currency=pricing.currency,
            source=source,
            confidence=match.score,
            status="ok",
            notes=merged_reason,
            library_hit=library_hit,
            excellent_price=pricing.excellent_price,
            good_price=pricing.good_price,
            acceptable_price=pricing.acceptable_price,
            lowest_fba=pricing.lowest_fba,
            lowest_fbm=pricing.lowest_fbm,
            buy_box=pricing.buy_box,
            total_offers=pricing.total_offers,
            condition_tier_used=requested_grade_tier,
        )

    @staticmethod
    def _lookup_key(row: InventoryRow, marketplace_id: str) -> LookupKey:
        return LookupKey(
            brand=norm_text(row.brand),
            model=normalize_model(row.model),
            color=normalize_color(row.color),
            storage=normalize_capacity(row.capacity),
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
        req_brand = norm_text(requested.brand)
        req_model = normalize_model(requested.model)
        req_color = normalize_color(requested.color)
        req_capacity = normalize_capacity(requested.capacity)
        req_grade = normalize_grade(requested.grade)

        # Brand can be missing from catalog payload. If provided in request we don't
        # hard fail on absence, but we score it when present in model/title context.
        cat_model = normalize_model(found_model)
        cat_color = normalize_color(found_color)
        cat_capacity = normalize_capacity(found_capacity)
        offer_grade = normalize_grade(pricing.condition)

        LOGGER.warning(
            "Validation input vs candidate | brand=%s model=%s color=%s capacity=%s grade=%s || "
            "cand_model=%s cand_color=%s cand_capacity=%s cand_grade=%s",
            req_brand or "<missing>",
            req_model or "<missing>",
            req_color or "<missing>",
            req_capacity or "<missing>",
            req_grade or "<missing>",
            cat_model or "<missing>",
            cat_color or "<missing>",
            cat_capacity or "<missing>",
            offer_grade or "<missing>",
        )

        if req_model and req_model != cat_model:
            LOGGER.warning("Validation failed field=model expected=%s actual=%s", req_model, cat_model)
            return MatchResult(False, 0.0, f"Model mismatch: expected '{req_model}', got '{cat_model}'")

        score = 0.55
        reasons = ["model matched"]

        if req_brand:
            reasons.append("brand specified")

        if req_color:
            if req_color != cat_color:
                LOGGER.warning("Validation failed field=color expected=%s actual=%s", req_color, cat_color)
                return MatchResult(False, 0.0, f"Color mismatch: expected '{req_color}', got '{cat_color}'")
            score += 0.15
            reasons.append("color matched")
        else:
            score -= 0.08
            reasons.append("color not verified")

        if req_capacity:
            if req_capacity != cat_capacity:
                LOGGER.warning("Validation failed field=capacity expected=%s actual=%s", req_capacity, cat_capacity)
                return MatchResult(False, 0.0, f"Capacity mismatch: expected '{req_capacity}', got '{cat_capacity}'")
            score += 0.2
            reasons.append("capacity matched")
        else:
            score -= 0.1
            reasons.append("capacity not verified")

        if req_grade:
            requested_tier = AsinResolver._requested_grade_tier(req_grade)
            grade_tier_price = AsinResolver._price_for_grade_tier(pricing, requested_tier)
            has_any_tier = any(
                value is not None
                for value in (
                    pricing.excellent_price,
                    pricing.good_price,
                    pricing.acceptable_price,
                )
            )

            if requested_tier and grade_tier_price is not None:
                score += 0.1
                reasons.append(f"grade matched ({requested_tier})")
            elif requested_tier and has_any_tier:
                LOGGER.warning(
                    "Validation failed field=grade expected=%s tier=%s available=(excellent=%s,good=%s,acceptable=%s)",
                    req_grade,
                    requested_tier,
                    pricing.excellent_price,
                    pricing.good_price,
                    pricing.acceptable_price,
                )
                return MatchResult(
                    False,
                    0.0,
                    (
                        "Grade mismatch: expected "
                        f"'{req_grade}' ({requested_tier}) but matching offer tier was not available"
                    ),
                )
            elif offer_grade and req_grade != offer_grade:
                LOGGER.warning("Validation failed field=grade expected=%s actual=%s", req_grade, offer_grade)
                return MatchResult(False, 0.0, f"Grade mismatch: expected '{req_grade}', got '{offer_grade}'")
            elif offer_grade:
                score += 0.1
                reasons.append("grade matched")
            else:
                reasons.append("grade unavailable from offers")
        else:
            score -= 0.05
            reasons.append("grade not verified")

        title_boost = 0.0
        if norm_text(found_model) and norm_text(requested.model) and norm_text(found_model) == norm_text(requested.model):
            title_boost = 0.05
            score += title_boost

        final = min(score, 1.0)
        reason = ", ".join(reasons)
        if title_boost:
            reason += ", exact model text boost"
        return MatchResult(True, final, reason)

    @staticmethod
    def _requested_grade_tier(raw_grade: str) -> str | None:
        grade_text = (raw_grade or "").strip().upper().replace(" ", "")
        # Requested mapping:
        # - Anything above B+ -> Excellent
        # - B+ -> Good
        # - C, C+ -> Acceptable
        if grade_text in {"A", "A+", "A-"}:
            return "excellent"
        if grade_text == "B+":
            return "good"
        if grade_text in {"C", "C+"}:
            return "acceptable"

        normalized_grade = normalize_grade(raw_grade)
        return {
            "A": "excellent",
            "B": "excellent",
            "C": "good",
            "D": "acceptable",
        }.get(normalized_grade)

    @staticmethod
    def _price_for_grade_tier(pricing: PriceSnapshot, grade_tier: str | None) -> float | None:
        if grade_tier == "excellent":
            return pricing.excellent_price
        if grade_tier == "good":
            return pricing.good_price
        if grade_tier == "acceptable":
            return pricing.acceptable_price
        return None
