from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

from .types import KeepaCandidate


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
        resp = requests.get(
            search_url,
            params={"key": self.api_key, "domain": domain, "term": query},
            timeout=self.timeout_seconds,
        )
        if resp.status_code != 200:
            raise KeepaClientError(f"Keepa search failed ({resp.status_code}): {resp.text[:300]}")
        payload = resp.json()
        asin_list = payload.get("asinList", [])[: max(limit, 1)]
        if not asin_list:
            return []
        return self.get_candidates(asin_list, domain=domain)

    def get_candidates(self, asins: list[str], domain: int = 1) -> list[KeepaCandidate]:
        if not asins:
            return []
        product_url = f"{self.BASE_URL}/product"
        resp = requests.get(
            product_url,
            params={
                "key": self.api_key,
                "domain": domain,
                "buybox": 1,
                "offers": 20,
                "stats": 90,
                "asin": ",".join(asins),
            },
            timeout=self.timeout_seconds,
        )
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
