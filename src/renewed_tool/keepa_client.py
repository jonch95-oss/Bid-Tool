from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from .types import KeepaCandidate

LOGGER = logging.getLogger(__name__)


class KeepaClientError(RuntimeError):
    pass


@dataclass(slots=True)
class KeepaClient:
    api_key: str
    timeout_seconds: int = 30

    BASE_URL = "https://api.keepa.com"

    def search_candidates(self, query: str, domain: int = 1, limit: int = 30) -> list[KeepaCandidate]:
        query = query.strip()
        if not query:
            return []

        search_url = f"{self.BASE_URL}/search"
        search_params = {
            "key": self.api_key,
            "domain": domain,
            "term": query,
            "asinsOnly": "1",
        }
        self._log_request("GET", search_url, search_params)
        resp = requests.get(search_url, params=search_params, timeout=self.timeout_seconds)
        LOGGER.warning("Keepa search response status=%s", resp.status_code)
        if resp.status_code == 400 and "invalidParameter" in resp.text:
            # Fallback to product finder query for accounts/plans where /search params differ.
            LOGGER.warning("Keepa /search invalidParameter, falling back to /query product_finder path.")
            asin_list = self._product_finder_search(query=query, domain=domain, limit=limit)
            return self.get_candidates(asin_list, domain=domain)
        if resp.status_code != 200:
            raise KeepaClientError(f"Keepa search failed ({resp.status_code}): {resp.text[:300]}")
        payload = resp.json()
        asin_list = payload.get("asinList", [])
        if not asin_list:
            products = payload.get("products")
            if isinstance(products, list):
                asin_list = [str(p.get("asin", "")).strip() for p in products if isinstance(p, dict)]
        asin_list = [asin for asin in asin_list if asin][: max(limit, 1)]
        if not asin_list:
            return []
        return self.get_candidates(asin_list, domain=domain)

    def _product_finder_search(self, query: str, domain: int, limit: int) -> list[str]:
        query_url = f"{self.BASE_URL}/query"
        selection = {
            "title": query,
            "perPage": max(limit, 1),
            "page": 0,
        }
        params = {
            "key": self.api_key,
            "domain": domain,
            "selection": json.dumps(selection, separators=(",", ":")),
        }
        self._log_request("GET", query_url, params)
        resp = requests.get(query_url, params=params, timeout=self.timeout_seconds)
        LOGGER.warning("Keepa product_finder response status=%s", resp.status_code)
        if resp.status_code != 200:
            raise KeepaClientError(
                f"Keepa product_finder query failed ({resp.status_code}): {resp.text[:300]}"
            )
        payload = resp.json()
        asin_list = payload.get("asinList", [])
        return [asin for asin in asin_list if asin][: max(limit, 1)]

    def get_candidates(self, asins: list[str], domain: int = 1) -> list[KeepaCandidate]:
        if not asins:
            return []
        product_url = f"{self.BASE_URL}/product"
        params = {
            "key": self.api_key,
            "domain": domain,
            "buybox": 1,
            "offers": 20,
            "stats": 90,
            "asin": ",".join(asins),
        }
        self._log_request("GET", product_url, params)
        resp = requests.get(product_url, params=params, timeout=self.timeout_seconds)
        LOGGER.warning("Keepa product response status=%s", resp.status_code)
        if resp.status_code != 200:
            raise KeepaClientError(f"Keepa product fetch failed ({resp.status_code}): {resp.text[:300]}")
        payload = resp.json()
        products = payload.get("products", [])
        results: list[KeepaCandidate] = []
        for product in products:
            asin = product.get("asin")
            if not asin:
                continue
            title = str(product.get("title") or "")
            brand = str(product.get("brand") or product.get("manufacturer") or "")
            model = str(product.get("model") or "")
            color = str(product.get("color") or "")
            capacity = str(product.get("size") or "")
            attrs_text = self._flatten_attributes(product)
            results.append(
                KeepaCandidate(
                    asin=asin,
                    title=title,
                    brand=brand,
                    model=model,
                    color=color,
                    capacity=capacity,
                    attributes_text=attrs_text,
                    raw=product,
                )
            )
        return results

    @staticmethod
    def _flatten_attributes(product: dict[str, Any]) -> str:
        chunks: list[str] = []
        for field in (
            "title",
            "brand",
            "manufacturer",
            "model",
            "color",
            "size",
            "edition",
            "features",
            "description",
        ):
            value = product.get(field)
            if not value:
                continue
            if isinstance(value, list):
                chunks.extend(str(v) for v in value if v)
            else:
                chunks.append(str(value))
        variation = product.get("variationCSV")
        if variation:
            chunks.append(str(variation))
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()

    @staticmethod
    def _log_request(method: str, url: str, params: dict[str, Any]) -> None:
        safe_params = dict(params)
        if "key" in safe_params:
            safe_params["key"] = "[REDACTED]"
        prepared = requests.Request(method, url, params=safe_params).prepare()
        LOGGER.warning("Keepa request: %s", prepared.url)
