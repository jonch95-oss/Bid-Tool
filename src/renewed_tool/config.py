from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ToolConfig:
    keepa_api_key: str
    keepa_domain: int
    spapi_marketplace_id: str
    aws_region: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str | None
    aws_role_arn: str | None
    lwa_client_id: str
    lwa_client_secret: str
    lwa_refresh_token: str
    db_path: Path
    candidate_limit: int

    @property
    def sp_api_host(self) -> str:
        region_hosts = {
            "us-east-1": "sellingpartnerapi-na.amazon.com",
            "us-west-2": "sellingpartnerapi-na.amazon.com",
            "eu-west-1": "sellingpartnerapi-eu.amazon.com",
            "eu-central-1": "sellingpartnerapi-eu.amazon.com",
            "ap-southeast-1": "sellingpartnerapi-fe.amazon.com",
            "ap-northeast-1": "sellingpartnerapi-fe.amazon.com",
        }
        return region_hosts.get(self.aws_region, "sellingpartnerapi-na.amazon.com")

    @property
    def sp_api_base_url(self) -> str:
        return f"https://{self.sp_api_host}"


def _required(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"Missing required environment variable: {name}")


def load_config() -> ToolConfig:
    return ToolConfig(
        keepa_api_key=_required("KEEPA_API_KEY"),
        keepa_domain=int(os.getenv("KEEPA_DOMAIN", "1")),
        spapi_marketplace_id=os.getenv("SPAPI_MARKETPLACE_ID", "ATVPDKIKX0DER"),
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=_required("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_required("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
        aws_role_arn=os.getenv("AWS_ROLE_ARN"),
        lwa_client_id=_required("LWA_CLIENT_ID"),
        lwa_client_secret=_required("LWA_CLIENT_SECRET"),
        lwa_refresh_token=_required("LWA_REFRESH_TOKEN"),
        db_path=Path(os.getenv("ASIN_LIBRARY_PATH", "./asin_library.db")),
        candidate_limit=int(os.getenv("CANDIDATE_LIMIT", "25")),
    )
