from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from renewed_tool.cli import build_parser  # noqa: E402
from renewed_tool.config import load_config  # noqa: E402
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


def sync_streamlit_secrets_to_env() -> None:
    for key in REQUIRED_KEYS + OPTIONAL_KEYS:
        if key in st.secrets and st.secrets[key]:
            os.environ[key] = str(st.secrets[key])
    os.environ.setdefault("ASIN_LIBRARY_PATH", "/tmp/asin_library.db")


def missing_required_env() -> list[str]:
    return [key for key in REQUIRED_KEYS if not os.getenv(key)]


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
    sync_streamlit_secrets_to_env()

    st.title("Amazon Renewed ASIN + Pricing Tool")
    st.caption(
        "Upload CSV inventory, validate model/color/capacity/grade, resolve ASINs via Keepa, "
        "and fetch pricing from Amazon SP-API."
    )

    with st.expander("Expected CSV columns", expanded=False):
        parser = build_parser()
        st.code(
            "sku,title,brand,model,color,capacity,grade,asin\n"
            "ALT: cpacity is also accepted as a capacity fallback typo."
        )
        st.caption(f"CLI command available too: `{parser.prog} enrich --input ... --output ...`")

    missing = missing_required_env()
    if missing:
        st.error(
            "Missing required credentials. Add these in Streamlit app Secrets or environment variables:\n\n"
            + "\n".join(f"- {key}" for key in missing)
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
        st.write(preview)
    else:
        st.warning("Could not parse preview rows. Make sure your file is a valid CSV with headers.")

    run = st.button("Run enrichment", type="primary")
    if not run:
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.csv"
        output_path = Path(temp_dir) / output_name
        input_path.write_bytes(raw_input)

        with st.spinner("Resolving ASINs and fetching pricing..."):
            try:
                summary = run_enrichment(
                    config=load_config(),
                    input_csv=input_path,
                    output_csv=output_path,
                )
            except Exception as exc:  # noqa: BLE001
                st.exception(exc)
                st.stop()

        output_bytes = output_path.read_bytes()
        st.success("Enrichment complete.")
        st.json(
            {
                "total_rows": summary.total_rows,
                "resolved_rows": summary.resolved_rows,
                "unresolved_rows": summary.unresolved_rows,
            }
        )

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
            st.write(preview_out)


if __name__ == "__main__":
    main()
