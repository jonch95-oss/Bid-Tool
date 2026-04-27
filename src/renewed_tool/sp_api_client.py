from __future__ import annotations

from typing import Any

import requests
from requests_aws4auth import AWS4Auth

from .types import CatalogSnapshot, PriceSnapshot


class SpApiClientError(RuntimeError):
    pass


class SpApiClient:
    def __init__(
        self,
        *,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        aws_session_token: str | None,
        lwa_client_id: str,
        lwa_client_secret: str,
        lwa_refresh_token: str,
        region: str,
        marketplace_id: str,
        endpoint_base: str,
        timeout_seconds: int = 30,
    ) -> None:
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._aws_session_token = aws_session_token
        self._lwa_client_id = lwa_client_id
        self._lwa_client_secret = lwa_client_secret
        self._lwa_refresh_token = lwa_refresh_token
        self._region = region
        self._marketplace_id = marketplace_id
        self._endpoint_base = endpoint_base.rstrip("/")
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._lwa_token_cache: str | None = None

    def _get_lwa_access_token(self) -> str:
        # Keep simple cache to avoid refreshing token per request.
        if self._lwa_token_cache:
            return self._lwa_token_cache
        resp = self._session.post(
            "https://api.amazon.com/auth/o2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._lwa_refresh_token,
                "client_id": self._lwa_client_id,
                "client_secret": self._lwa_client_secret,
            },
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise SpApiClientError(f"LWA token request failed ({resp.status_code}): {resp.text[:250]}")
        token = resp.json().get("access_token")
        if not token:
            raise SpApiClientError("LWA token missing in response.")
        self._lwa_token_cache = str(token)
        return self._lwa_token_cache

    def _aws_auth(self) -> AWS4Auth:
        return AWS4Auth(
            self._aws_access_key_id,
            self._aws_secret_access_key,
            self._region,
            "execute-api",
            session_token=self._aws_session_token,
        )

    def get_catalog_item(self, asin: str) -> CatalogSnapshot:
        token = self._get_lwa_access_token()
        resp = self._session.get(
            f"{self._endpoint_base}/catalog/2022-04-01/items/{asin}",
            params={
                "marketplaceIds": self._marketplace_id,
                "includedData": "summaries,attributes",
            },
            headers={"x-amz-access-token": token},
            auth=self._aws_auth(),
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise SpApiClientError(f"SP-API catalog fetch failed ({resp.status_code}): {resp.text[:250]}")
        payload = resp.json()
        title = self._extract_title(payload) or asin
        attrs = self._extract_attributes(payload)
        return CatalogSnapshot(
            asin=asin,
            title=title,
            model=attrs.get("model", ""),
            color=attrs.get("color", ""),
            capacity=attrs.get("capacity", ""),
        )

    def get_pricing(self, asin: str) -> PriceSnapshot:
        token = self._get_lwa_access_token()
        resp = self._session.get(
            f"{self._endpoint_base}/products/pricing/v0/items/{asin}/offers",
            params={
                "MarketplaceId": self._marketplace_id,
                "ItemCondition": "Used",
                "CustomerType": "Consumer",
            },
            headers={"x-amz-access-token": token},
            auth=self._aws_auth(),
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise SpApiClientError(f"SP-API pricing fetch failed ({resp.status_code}): {resp.text[:250]}")
        payload = resp.json()
        offers = payload.get("payload", {}).get("Offers", [])
        best = offers[0] if offers else {}
        listing = best.get("ListingPrice") or {}
        amount = listing.get("Amount")
        currency = listing.get("CurrencyCode")
        condition = best.get("SubCondition") or best.get("Condition")
        return PriceSnapshot(
            asin=asin,
            amount=float(amount) if amount is not None else None,
            currency=str(currency) if currency else None,
            condition=str(condition) if condition else None,
            raw=payload,
        )

    @staticmethod
    def _extract_title(payload: dict[str, Any]) -> str | None:
        summaries = payload.get("summaries")
        if isinstance(summaries, list) and summaries:
            first = summaries[0]
            if isinstance(first, dict) and first.get("itemName"):
                return str(first["itemName"])
        return None

    @staticmethod
    def _extract_attributes(payload: dict[str, Any]) -> dict[str, str]:
        attrs = payload.get("attributes")
        if not isinstance(attrs, dict):
            return {}

        def first_text(key: str) -> str:
            raw = attrs.get(key)
            if not raw:
                return ""
            if isinstance(raw, list) and raw:
                first = raw[0]
                if isinstance(first, dict):
                    for k in ("value", "displayValue", "text", "name"):
                        if first.get(k):
                            return str(first[k])
                return str(first)
            if isinstance(raw, dict):
                for k in ("value", "displayValue", "text", "name"):
                    if raw.get(k):
                        return str(raw[k])
            return str(raw)

        return {
            "model": first_text("model_name") or first_text("model_number") or first_text("part_number"),
            "color": first_text("color"),
            "capacity": first_text("memory_storage_capacity") or first_text("hard_disk") or first_text("item_volume"),
        }
