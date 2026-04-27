from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
import time
from typing import Any
import xml.etree.ElementTree as ET

import requests
from requests_aws4auth import AWS4Auth

from .types import CatalogSnapshot, PriceSnapshot

LOGGER = logging.getLogger(__name__)


class SpApiClientError(RuntimeError):
    pass


class SpApiClient:
    def __init__(
        self,
        *,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        aws_session_token: str | None,
        aws_role_arn: str | None,
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
        self._aws_role_arn = aws_role_arn
        self._lwa_client_id = lwa_client_id
        self._lwa_client_secret = lwa_client_secret
        self._lwa_refresh_token = lwa_refresh_token
        self._region = region
        self._marketplace_id = marketplace_id
        self._endpoint_base = endpoint_base.rstrip("/")
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._lwa_token_cache: str | None = None
        self._lwa_token_expiry: datetime | None = None
        self._sts_cache: dict[str, str] | None = None
        self._sts_cache_expiry: datetime | None = None

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _get_lwa_access_token(self) -> str:
        if self._lwa_token_cache and self._lwa_token_expiry:
            if self._lwa_token_expiry > self._utc_now() + timedelta(seconds=60):
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
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise SpApiClientError("LWA token missing in response.")
        expires_in = int(payload.get("expires_in", 3600))
        self._lwa_token_cache = str(token)
        self._lwa_token_expiry = self._utc_now() + timedelta(seconds=max(120, expires_in - 60))
        return self._lwa_token_cache

    def _assume_role_credentials(self) -> dict[str, str]:
        if self._sts_cache and self._sts_cache_expiry:
            if self._sts_cache_expiry > self._utc_now() + timedelta(minutes=5):
                return self._sts_cache
        if not self._aws_role_arn:
            raise SpApiClientError("STS AssumeRole requested but AWS_ROLE_ARN is not configured.")

        params = {
            "Action": "AssumeRole",
            "Version": "2011-06-15",
            "RoleArn": self._aws_role_arn,
            "RoleSessionName": "renewed-tool-spapi",
            "DurationSeconds": "3600",
        }
        resp = self._session.get(
            "https://sts.amazonaws.com/",
            params=params,
            auth=AWS4Auth(
                self._aws_access_key_id,
                self._aws_secret_access_key,
                "us-east-1",
                "sts",
                session_token=self._aws_session_token,
            ),
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise SpApiClientError(f"STS AssumeRole failed ({resp.status_code}): {resp.text[:250]}")
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise SpApiClientError("STS AssumeRole returned invalid XML.") from exc

        access_key = self._xml_text(root, ".//{*}Credentials/{*}AccessKeyId")
        secret_key = self._xml_text(root, ".//{*}Credentials/{*}SecretAccessKey")
        session_token = self._xml_text(root, ".//{*}Credentials/{*}SessionToken")
        expiry_raw = self._xml_text(root, ".//{*}Credentials/{*}Expiration")
        if not access_key or not secret_key or not session_token or not expiry_raw:
            raise SpApiClientError("STS AssumeRole response missing temporary credentials.")

        expires_at = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
        self._sts_cache = {
            "access_key_id": access_key,
            "secret_access_key": secret_key,
            "session_token": session_token,
        }
        self._sts_cache_expiry = expires_at
        return self._sts_cache

    @staticmethod
    def _xml_text(root: ET.Element, path: str) -> str | None:
        node = root.find(path)
        if node is None:
            return None
        text = (node.text or "").strip()
        return text or None

    def _resolve_signing_credentials(self) -> tuple[str, str, str | None]:
        if self._aws_role_arn:
            creds = self._assume_role_credentials()
            return creds["access_key_id"], creds["secret_access_key"], creds["session_token"]
        return self._aws_access_key_id, self._aws_secret_access_key, self._aws_session_token

    def _aws_auth(self, service: str = "execute-api") -> AWS4Auth:
        access_key, secret_key, session_token = self._resolve_signing_credentials()
        return AWS4Auth(
            access_key,
            secret_key,
            self._region,
            service,
            session_token=session_token,
        )

    def _sp_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = f"{self._endpoint_base}{path}"
        for attempt in range(4):
            token = self._get_lwa_access_token()
            headers = {"x-amz-access-token": token}
            if body is not None:
                headers["Content-Type"] = "application/json"
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=body,
                headers=headers,
                auth=self._aws_auth(),
                timeout=self._timeout,
            )
            if response.status_code == 429 and attempt < 3:
                delay = 2 * (attempt + 1)
                LOGGER.warning("SP-API 429 on %s %s. Retrying in %ss.", method, path, delay)
                time.sleep(delay)
                continue
            return response
        raise SpApiClientError(f"SP-API request exhausted retries: {method} {path}")

    def get_catalog_item(self, asin: str) -> CatalogSnapshot:
        resp = self._sp_request(
            "GET",
            f"/catalog/2022-04-01/items/{asin}",
            params={
                "marketplaceIds": self._marketplace_id,
                "includedData": "summaries,attributes",
            },
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
        versions = (
            ("v2022_single_get", self._pricing_v2022_single_get),
            ("v2022_batch_post", self._pricing_v2022_batch_post),
            ("v0_single_get", self._pricing_v0_single_get),
        )
        last_error: str | None = None
        for version_name, version_call in versions:
            for condition in ("used", "new", "collectible"):
                resp = version_call(asin, condition)
                status = resp.status_code
                if status == 200:
                    payload = resp.json()
                    LOGGER.info("SP-API pricing success via %s condition=%s", version_name, condition)
                    return self._parse_pricing_payload(asin, payload)
                if status in (400, 404):
                    last_error = f"{version_name}/{condition} returned {status}"
                    continue
                if 400 <= status < 500:
                    # Continue fallback chain for client-side endpoint/version mismatches.
                    last_error = f"{version_name}/{condition} returned {status}: {resp.text[:120]}"
                    continue
                raise SpApiClientError(
                    f"SP-API pricing request failed ({status}) via {version_name}/{condition}: {resp.text[:250]}"
                )
        raise SpApiClientError(last_error or "SP-API pricing request failed across all versions/conditions.")

    def diagnose_pricing_versions(self, asin: str) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        versions = (
            ("v2022_single_get", self._pricing_v2022_single_get),
            ("v2022_batch_post", self._pricing_v2022_batch_post),
            ("v0_single_get", self._pricing_v0_single_get),
        )
        for version_name, version_call in versions:
            attempts: list[dict[str, Any]] = []
            worked = False
            chosen_condition = ""
            for condition in ("used", "new", "collectible"):
                resp = version_call(asin, condition)
                status = resp.status_code
                attempts.append({"condition": condition, "status_code": status})
                if status == 200:
                    worked = True
                    chosen_condition = condition
                    break
                if status in (400, 404):
                    continue
                if 400 <= status < 500:
                    break
                attempts[-1]["error"] = resp.text[:250]
                break
            checks[version_name] = {
                "worked": worked,
                "condition": chosen_condition,
                "attempts": attempts,
            }
        return checks

    def get_marketplace_participations(self) -> list[dict[str, str]]:
        resp = self._sp_request("GET", "/sellers/v1/marketplaceParticipations")
        if resp.status_code != 200:
            raise SpApiClientError(
                f"SP-API marketplace participations failed ({resp.status_code}): {resp.text[:250]}"
            )
        payload = resp.json()
        data = self._get_case(payload, "payload")
        rows = self._as_list(self._get_case(data, "MarketplaceParticipations", "marketplaceParticipations"))
        result: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            marketplace = self._get_case(row, "Marketplace", "marketplace")
            if not isinstance(marketplace, dict):
                continue
            result.append(
                {
                    "id": str(self._get_case(marketplace, "Id", "id") or ""),
                    "name": str(self._get_case(marketplace, "Name", "name") or ""),
                    "country": str(self._get_case(marketplace, "CountryCode", "countryCode") or ""),
                }
            )
        return result

    def run_health_check(self, asin: str = "B09G9HD6PD") -> dict[str, Any]:
        results: dict[str, Any] = {
            "auth_method": "assume_role" if self._aws_role_arn else "iam_user",
        }
        try:
            self._get_lwa_access_token()
            results["lwa"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            results["lwa"] = {"ok": False, "error": str(exc)}

        if self._aws_role_arn:
            try:
                self._assume_role_credentials()
                results["sts"] = {"ok": True, "role_arn": self._aws_role_arn}
            except Exception as exc:  # noqa: BLE001
                results["sts"] = {"ok": False, "role_arn": self._aws_role_arn, "error": str(exc)}
        else:
            results["sts"] = {"ok": True, "method": "direct_iam_user_credentials"}

        try:
            marketplaces = self.get_marketplace_participations()
            results["marketplaces"] = {"ok": True, "items": marketplaces}
        except Exception as exc:  # noqa: BLE001
            results["marketplaces"] = {"ok": False, "error": str(exc)}

        try:
            results["pricing"] = {"ok": True, "versions": self.diagnose_pricing_versions(asin)}
        except Exception as exc:  # noqa: BLE001
            results["pricing"] = {"ok": False, "error": str(exc)}
        return results

    def _pricing_v2022_single_get(self, asin: str, condition: str) -> requests.Response:
        return self._sp_request(
            "GET",
            f"/products/pricing/2022-05-01/items/{asin}/offers",
            params={
                "marketplaceId": self._marketplace_id,
                "itemCondition": condition,
                "customerType": "consumer",
            },
        )

    def _pricing_v2022_batch_post(self, asin: str, condition: str) -> requests.Response:
        request_uri = (
            f"/products/pricing/2022-05-01/items/{asin}/offers"
            f"?marketplaceId={self._marketplace_id}&itemCondition={condition}&customerType=consumer"
        )
        return self._sp_request(
            "POST",
            "/batches/products/pricing/2022-05-01/items/offers",
            body={
                "requests": [
                    {
                        "method": "GET",
                        "uri": request_uri,
                    }
                ]
            },
        )

    def _pricing_v0_single_get(self, asin: str, condition: str) -> requests.Response:
        mapping = {"used": "Used", "new": "New", "collectible": "Collectible"}
        return self._sp_request(
            "GET",
            f"/products/pricing/v0/items/{asin}/offers",
            params={
                "MarketplaceId": self._marketplace_id,
                "ItemCondition": mapping[condition],
                "CustomerType": "Consumer",
            },
        )

    def _parse_pricing_payload(self, asin: str, payload: dict[str, Any]) -> PriceSnapshot:
        offer_root = payload
        if isinstance(payload.get("responses"), list):
            responses = self._as_list(payload.get("responses"))
            first = responses[0] if responses else {}
            if isinstance(first, dict):
                body = self._get_case(first, "body", "Body")
                if isinstance(body, dict):
                    offer_root = body
                else:
                    offer_root = {}

        data = self._get_case(offer_root, "payload")
        if not isinstance(data, dict):
            data = offer_root

        offers = self._as_list(self._get_case(data, "Offers", "offers"))
        if not offers and isinstance(data.get("payload"), dict):
            nested = data["payload"]
            offers = self._as_list(self._get_case(nested, "Offers", "offers"))

        excellent_price: float | None = None
        good_price: float | None = None
        acceptable_price: float | None = None
        lowest_fba: float | None = None
        lowest_fbm: float | None = None
        buy_box: float | None = None
        currency: str | None = None
        condition: str | None = None
        total_offers = 0
        min_price: float | None = None

        for offer in offers:
            if not isinstance(offer, dict):
                continue
            total_offers += 1
            listing_price = self._get_case(offer, "ListingPrice", "listingPrice")
            amount = self._extract_amount(listing_price)
            if amount is None:
                continue
            currency = currency or self._extract_currency(listing_price)
            if min_price is None or amount < min_price:
                min_price = amount

            sub_condition = self._get_case(offer, "SubCondition", "subCondition", "Condition", "condition")
            if condition is None and sub_condition:
                condition = str(sub_condition)

            notes = str(self._get_case(offer, "ConditionNotes", "conditionNotes") or "")
            grade = self._grade_from_notes(notes)
            if grade == "excellent":
                excellent_price = amount if excellent_price is None else min(excellent_price, amount)
            elif grade == "good":
                good_price = amount if good_price is None else min(good_price, amount)
            elif grade == "acceptable":
                acceptable_price = amount if acceptable_price is None else min(acceptable_price, amount)

            is_fba = self._is_fba_offer(offer)
            if is_fba:
                lowest_fba = amount if lowest_fba is None else min(lowest_fba, amount)
            else:
                lowest_fbm = amount if lowest_fbm is None else min(lowest_fbm, amount)

            is_buy_box = bool(self._get_case(offer, "IsBuyBoxWinner", "isBuyBoxWinner"))
            if is_buy_box:
                buy_box = amount if buy_box is None else min(buy_box, amount)

        if buy_box is None:
            summary = self._get_case(data, "Summary", "summary")
            if isinstance(summary, dict):
                bb_prices = self._as_list(self._get_case(summary, "BuyBoxPrices", "buyBoxPrices"))
                for entry in bb_prices:
                    if not isinstance(entry, dict):
                        continue
                    bb_listing = self._get_case(entry, "ListingPrice", "listingPrice")
                    amount = self._extract_amount(bb_listing)
                    if amount is None:
                        continue
                    buy_box = amount if buy_box is None else min(buy_box, amount)

        return PriceSnapshot(
            asin=asin,
            excellent_price=excellent_price,
            good_price=good_price,
            acceptable_price=acceptable_price,
            lowest_fba=lowest_fba,
            lowest_fbm=lowest_fbm,
            buy_box=buy_box,
            total_offers=total_offers,
            currency=currency,
            condition=condition,
            raw=payload,
        )

    @staticmethod
    def _extract_title(payload: dict[str, Any]) -> str | None:
        summaries = payload.get("summaries")
        if isinstance(summaries, list) and summaries:
            first = summaries[0]
            if isinstance(first, dict):
                value = first.get("itemName")
                if value:
                    return str(value)
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
                    for inner_key in ("value", "displayValue", "text", "name"):
                        if first.get(inner_key):
                            return str(first[inner_key])
                return str(first)
            if isinstance(raw, dict):
                for inner_key in ("value", "displayValue", "text", "name"):
                    if raw.get(inner_key):
                        return str(raw[inner_key])
            return str(raw)

        return {
            "model": first_text("model_name") or first_text("model_number") or first_text("part_number"),
            "color": first_text("color"),
            "capacity": first_text("memory_storage_capacity") or first_text("hard_disk") or first_text("item_volume"),
        }

    @staticmethod
    def _get_case(mapping: dict[str, Any] | None, *keys: str) -> Any:
        if not isinstance(mapping, dict):
            return None
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        return []

    @staticmethod
    def _extract_amount(listing_price: Any) -> float | None:
        if not isinstance(listing_price, dict):
            return None
        amount = listing_price.get("Amount")
        if amount is None:
            amount = listing_price.get("amount")
        if amount is None:
            return None
        try:
            return float(amount)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_currency(listing_price: Any) -> str | None:
        if not isinstance(listing_price, dict):
            return None
        currency = listing_price.get("CurrencyCode")
        if currency is None:
            currency = listing_price.get("currencyCode")
        if not currency:
            return None
        return str(currency)

    @staticmethod
    def _is_fba_offer(offer: dict[str, Any]) -> bool:
        if "IsFulfilledByAmazon" in offer:
            return bool(offer["IsFulfilledByAmazon"])
        if "isFulfilledByAmazon" in offer:
            return bool(offer["isFulfilledByAmazon"])
        channel = offer.get("FulfillmentChannel")
        if channel is None:
            channel = offer.get("fulfillmentChannel")
        return str(channel).upper() in {"AFN", "AMAZON_NA"}

    @staticmethod
    def _grade_from_notes(notes: str) -> str | None:
        normalized = notes.lower()
        if re.search(r"excellent|like.?new|premium", normalized) or re.search(r"very.?good", normalized):
            return "excellent"
        if re.search(r"\bgood\b", normalized):
            return "good"
        if re.search(r"acceptable|fair", normalized):
            return "acceptable"
        return None
