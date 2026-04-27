from __future__ import annotations

import csv
import io
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from renewed_tool.config import ToolConfig  # noqa: E402
from renewed_tool.csv_pipeline import run_enrichment  # noqa: E402


REQUIRED_KEYS = [
    "KEEPA_API_KEY",
    "LWA_CLIENT_ID",
    "LWA_CLIENT_SECRET",
    "LWA_REFRESH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
]

OPTIONAL_KEYS = [
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "SPAPI_MARKETPLACE_ID",
    "KEEPA_DOMAIN",
    "ASIN_LIBRARY_PATH",
    "CANDIDATE_LIMIT",
]


def _secret_get(key: str, default: str | None = None) -> str | None:
    if key in st.secrets and st.secrets[key]:
        return str(st.secrets[key])
    return default


def _required_secret(key: str) -> str:
    value = _secret_get(key)
    if value:
        return value
    raise ValueError(f"Missing required Streamlit secret: {key}")


def build_config_from_secrets() -> ToolConfig:
    return ToolConfig(
        keepa_api_key=_required_secret("KEEPA_API_KEY"),
        keepa_domain=int(_secret_get("KEEPA_DOMAIN", "1") or "1"),
        amazon_marketplace_id=_secret_get("SPAPI_MARKETPLACE_ID", "ATVPDKIKX0DER") or "ATVPDKIKX0DER",
        aws_region=_secret_get("AWS_REGION", "us-east-1") or "us-east-1",
        aws_access_key_id=_required_secret("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_required_secret("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=_secret_get("AWS_SESSION_TOKEN"),
        lwa_client_id=_required_secret("LWA_CLIENT_ID"),
        lwa_client_secret=_required_secret("LWA_CLIENT_SECRET"),
        lwa_refresh_token=_required_secret("LWA_REFRESH_TOKEN"),
        db_path=Path(_secret_get("ASIN_LIBRARY_PATH", "/tmp/asin_library.db") or "/tmp/asin_library.db"),
        candidate_limit=int(_secret_get("CANDIDATE_LIMIT", "25") or "25"),
    )


def preview_csv_rows(content: bytes, limit: int = 10) -> list[dict[str, str]]:
    text = content.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for index, row in enumerate(reader):
        if index >= limit:
            break
        rows.append({k: (v or "") for k, v in row.items()})
    return rows


def main() -> None:
    st.set_page_config(page_title="Amazon Renewed ASIN Tool", page_icon="🛠️", layout="wide")

    st.title("Amazon Renewed ASIN + Pricing Tool")
    st.caption(
        "Upload CSV inventory, validate model/color/capacity/grade, resolve ASINs via Keepa, "
        "and fetch pricing from Amazon SP-API."
    )

    with st.expander("Expected CSV columns", expanded=False):
        st.code(
            "sku,title,brand,model,color,capacity,grade,asin\n"
            "# alt header typo 'cpacity' is also accepted as capacity"
        )
        st.caption("This app reads API credentials from Streamlit Secrets (not environment variables).")

    missing = [key for key in REQUIRED_KEYS if not _secret_get(key)]
    if missing:
        st.error(
            "Missing required credentials. Add these keys in Streamlit app Secrets:\n\n"
            + "\n".join(f"- {key}" for key in missing)
        )
        with st.expander("Secrets template", expanded=False):
            st.code(
                'KEEPA_API_KEY = "your_keepa_key"\n'
                'LWA_CLIENT_ID = "your_lwa_client_id"\n'
                'LWA_CLIENT_SECRET = "your_lwa_client_secret"\n'
                'LWA_REFRESH_TOKEN = "your_lwa_refresh_token"\n'
                'AWS_ACCESS_KEY_ID = "your_aws_access_key"\n'
                'AWS_SECRET_ACCESS_KEY = "your_aws_secret_key"\n'
                'AWS_REGION = "us-east-1"\n'
                'SPAPI_MARKETPLACE_ID = "ATVPDKIKX0DER"\n'
            )
        st.stop()

    left, right = st.columns([2, 1])
    with left:
        uploaded_file = st.file_uploader("Upload inventory CSV", type=["csv"])
    with right:
        output_name = st.text_input("Output filename", value="enriched_output.csv")

    if not uploaded_file:
        st.info("Upload a CSV to start enrichment.")
        return

    raw_input = uploaded_file.getvalue()
    preview = preview_csv_rows(raw_input, limit=10)
    if preview:
        st.subheader("Input preview (first 10 rows)")
        st.dataframe(preview, use_container_width=True)
    else:
        st.warning("Could not parse preview rows. Make sure your file is a valid CSV with headers.")

    run = st.button("Run enrichment", type="primary")
    if not run:
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.csv"
        output_path = Path(temp_dir) / output_name
        input_path.write_bytes(raw_input)

        progress = st.progress(0, text="Preparing run...")
        with st.spinner("Resolving ASINs and fetching pricing..."):
            try:
                config = build_config_from_secrets()
                progress.progress(15, text="Configuration loaded from Streamlit Secrets")
                summary = run_enrichment(
                    config=config,
                    input_csv=input_path,
                    output_csv=output_path,
                )
                progress.progress(90, text="Enrichment completed. Preparing output...")
            except Exception as exc:  # noqa: BLE001
                st.exception(exc)
                progress.empty()
                st.stop()

        output_bytes = output_path.read_bytes()
        progress.progress(100, text="Done")
        st.success("Enrichment complete.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total rows", summary.total_rows)
        m2.metric("Resolved rows", summary.resolved_rows)
        m3.metric("Unresolved rows", summary.unresolved_rows)

        st.download_button(
            "Download enriched CSV",
            data=output_bytes,
            file_name=output_name,
            mime="text/csv",
            type="secondary",
        )

        preview_out = preview_csv_rows(output_bytes, limit=10)
        if preview_out:
            st.subheader("Output preview (first 10 rows)")
            st.dataframe(preview_out, use_container_width=True)


if __name__ == "__main__":
    main()
