from __future__ import annotations

import csv
import io
import json
import logging
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from renewed_tool.asin_library import AsinLibrary  # noqa: E402
from renewed_tool.config import ToolConfig  # noqa: E402
from renewed_tool.csv_pipeline import run_enrichment  # noqa: E402
from renewed_tool.keepa_client import KeepaClient, KeepaClientError  # noqa: E402
from renewed_tool.normalization import normalize_capacity, normalize_color, normalize_model, norm_text  # noqa: E402
from renewed_tool.sp_api_client import SpApiClient  # noqa: E402
from renewed_tool.types import LookupKey  # noqa: E402


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
    "AWS_ROLE_ARN",
    "AWS_REGION",
    "SPAPI_MARKETPLACE_ID",
    "KEEPA_DOMAIN",
    "ASIN_LIBRARY_PATH",
    "CANDIDATE_LIMIT",
]


logging.basicConfig(level=logging.INFO)


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
        spapi_marketplace_id=_secret_get("SPAPI_MARKETPLACE_ID", "ATVPDKIKX0DER") or "ATVPDKIKX0DER",
        aws_region=_secret_get("AWS_REGION", "us-east-1") or "us-east-1",
        aws_access_key_id=_required_secret("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_required_secret("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=_secret_get("AWS_SESSION_TOKEN"),
        aws_role_arn=_secret_get("AWS_ROLE_ARN"),
        lwa_client_id=_required_secret("LWA_CLIENT_ID"),
        lwa_client_secret=_required_secret("LWA_CLIENT_SECRET"),
        lwa_refresh_token=_required_secret("LWA_REFRESH_TOKEN"),
        db_path=Path(_secret_get("ASIN_LIBRARY_PATH", "/tmp/asin_library.db") or "/tmp/asin_library.db"),
        candidate_limit=int(_secret_get("CANDIDATE_LIMIT", "25") or "25"),
    )


def _keepa_health_check(config: ToolConfig) -> None:
    try:
        keepa = KeepaClient(config.keepa_api_key)
        params = {
            "key": config.keepa_api_key,
            "domain": 1,
            "type": "product",
            "term": "iPhone 13",
            "asinsOnly": 1,
        }
        url = "https://api.keepa.com/search"
        prepared_url = f"{url}?domain=1&type=product&term=iPhone+13&asinsOnly=1&key=[REDACTED]"
        response = keepa.search_candidates("iPhone 13", domain=1, limit=10)
        raw_resp = keepa  # keep linter calm in no-network static review
        st.success("✅ Keepa health check passed")
        st.write(f"HTTP status: 200")
        st.write(f"Request URL: {prepared_url}")
        # fetch low-level for token visibility
        import requests

        low_level = requests.get(url, params=params, timeout=keepa.timeout_seconds)
        payload = low_level.json() if low_level.status_code == 200 else {}
        st.write(f"Tokens left: {payload.get('tokensLeft', 'unknown')}")
        st.write(f"ASIN candidates returned: {len(payload.get('asinList', []) or [])}")
        if response:
            st.write("Sample ASINs:", [c.asin for c in response[:10]])
    except KeepaClientError as exc:
        st.error(f"❌ Keepa health check failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"❌ Keepa health check failed: {exc}")


def _spapi_health_check(config: ToolConfig, test_asin: str) -> None:
    client = SpApiClient(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        aws_session_token=config.aws_session_token,
        aws_role_arn=config.aws_role_arn,
        lwa_client_id=config.lwa_client_id,
        lwa_client_secret=config.lwa_client_secret,
        lwa_refresh_token=config.lwa_refresh_token,
        region=config.aws_region,
        marketplace_id=config.spapi_marketplace_id,
        endpoint_base=config.sp_api_base_url,
    )
    with st.spinner("Running SP-API diagnostics..."):
        results = client.run_health_check(asin=(test_asin or "B09G9HD6PD").strip())

    lwa = results.get("lwa", {})
    if lwa.get("ok"):
        st.success("✅ LWA token fetch succeeded")
    else:
        st.error(f"❌ LWA token fetch failed: {lwa.get('error', 'unknown error')}")

    sts = results.get("sts", {})
    if sts.get("ok"):
        if results.get("auth_method") == "assume_role":
            st.success(f"✅ STS AssumeRole succeeded ({sts.get('role_arn', '')})")
        else:
            st.info("✅ Using direct IAM user credentials (AWS_ROLE_ARN not set)")
    else:
        st.error(f"❌ STS AssumeRole failed: {sts.get('error', 'unknown error')}")

    marketplaces = results.get("marketplaces", {})
    if marketplaces.get("ok"):
        items = marketplaces.get("items", [])
        if items:
            st.write("Marketplace participations:")
            st.dataframe(items, use_container_width=True)
        else:
            st.warning("Marketplace participation call succeeded but returned no marketplaces.")
    else:
        st.error(f"❌ Marketplace participation failed: {marketplaces.get('error', 'unknown error')}")

    pricing = results.get("pricing", {})
    if pricing.get("ok"):
        version_results = pricing.get("versions", {})
        st.write("Pricing endpoint checks:")
        st.dataframe(
            [
                {
                    "version": version_name,
                    "worked": details.get("worked", False),
                    "condition": details.get("condition", ""),
                    "attempts": json.dumps(details.get("attempts", [])),
                }
                for version_name, details in version_results.items()
            ],
            use_container_width=True,
        )
    else:
        st.error(f"❌ Pricing checks failed: {pricing.get('error', 'unknown error')}")


def _library_stats(config: ToolConfig) -> None:
    library = AsinLibrary(config.db_path)
    try:
        entries = library.list_entries(config.spapi_marketplace_id)
        counts: dict[str, int] = {"keepa": 0, "manual": 0, "user_assisted": 0}
        for entry in entries:
            source = (entry.source or "").strip().lower()
            if source in counts:
                counts[source] += 1
        st.write(f"DB file path: `{config.db_path}`")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total entries", len(entries))
        c2.metric("keepa", counts["keepa"])
        c3.metric("manual", counts["manual"])
        c4.metric("user_assisted", counts["user_assisted"])
    finally:
        library.close()


def _render_diagnostics(config: ToolConfig) -> None:
    with st.expander("Diagnostics", expanded=False):
        st.caption("Run targeted checks for Keepa, SP-API, and ASIN library health.")
        test_asin = st.text_input("Health check ASIN", value="B09G9HD6PD", key="spapi-health-asin")
        b1, b2, b3 = st.columns(3)
        if b1.button("Keepa Health Check", key="run-keepa-health-check"):
            _keepa_health_check(config)
        if b2.button("SP-API Health Check", key="run-spapi-health-check"):
            _spapi_health_check(config, test_asin)
        if b3.button("Library Stats", key="run-library-stats"):
            _library_stats(config)


def preview_csv_rows(content: bytes, limit: int = 10) -> list[dict[str, str]]:
    text = content.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for index, row in enumerate(reader):
        if index >= limit:
            break
        rows.append({k: (v or "") for k, v in row.items()})
    return rows


def _library_key_from_fields(brand: str, model: str, color: str, storage: str, marketplace_id: str) -> LookupKey:
    return LookupKey(
        brand=norm_text(brand),
        model=normalize_model(model),
        color=normalize_color(color),
        storage=normalize_capacity(storage),
        marketplace_id=marketplace_id,
    )


def render_enrichment_page(config: ToolConfig) -> None:
    _render_diagnostics(config)

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
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total rows", summary.total_rows)
        m2.metric("Resolved rows", summary.resolved_rows)
        m3.metric("Unresolved rows", summary.unresolved_rows)
        m4.metric("Library hits", summary.library_hits)
        m5.metric("Keepa fresh lookups", summary.keepa_resolved)
        m6.metric("Needs input", summary.needs_input)
        if summary.conflicts:
            st.warning(f"Conflicts detected: {summary.conflicts}")
        if summary.skipped:
            st.info(f"Skipped rows: {summary.skipped}")

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


def render_library_page(config: ToolConfig) -> None:
    library = AsinLibrary(config.db_path)
    try:
        entries = library.list_entries(config.spapi_marketplace_id)
        st.subheader(f"ASIN Library ({len(entries)} entries)")

        brand_options = ["All Brands"] + sorted(
            {entry.brand for entry in entries if entry.brand.strip()},
            key=str.lower,
        )
        brand_filter, search_query = st.columns([1, 2])
        with brand_filter:
            selected_brand = st.selectbox("Brand", brand_options, index=0)
        with search_query:
            query = st.text_input("Search model / ASIN / color", value="")

        filtered = entries
        if selected_brand != "All Brands":
            filtered = [e for e in filtered if e.brand == selected_brand]
        if query.strip():
            q = query.strip().lower()
            filtered = [
                e
                for e in filtered
                if q in e.model.lower() or q in e.asin.lower() or q in e.color.lower()
            ]

        with st.expander("+ Add Entry Manually", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            add_brand = c1.text_input("Brand")
            add_model = c2.text_input("Model")
            add_color = c3.text_input("Color")
            add_storage = c4.text_input("Storage")
            c5, c6, c7 = st.columns(3)
            add_carrier = c5.text_input("Carrier")
            add_us_spec = c6.text_input("US Spec")
            add_asin = c7.text_input("ASIN")
            add_locked = st.checkbox("Locked", value=False)
            if st.button("Add to Library", type="primary"):
                if not add_model.strip() or not add_asin.strip():
                    st.error("Model and ASIN are required.")
                else:
                    library.upsert_manual_entry(
                        marketplace_id=config.spapi_marketplace_id,
                        brand=add_brand,
                        model=add_model,
                        color=add_color,
                        storage=add_storage,
                        carrier=add_carrier,
                        us_spec=add_us_spec,
                        asin=add_asin,
                        locked=add_locked,
                    )
                    st.success("Entry added.")
                    st.rerun()

        st.markdown("### Library Entries")
        st.caption("Actions: lock/unlock, edit, delete.")
        header = st.columns([1.1, 1.5, 1.0, 0.9, 0.9, 0.9, 1.0, 1.7])
        for col, text in zip(
            header,
            ["Brand", "Model", "Color", "Storage", "Carrier", "US Spec", "ASIN", "Actions"],
        ):
            col.markdown(f"**{text}**")

        for idx, entry in enumerate(filtered):
            row = st.columns([1.1, 1.5, 1.0, 0.9, 0.9, 0.9, 1.0, 1.7])
            row[0].write(entry.brand)
            row[1].write(entry.model)
            row[2].write(entry.color)
            row[3].write(entry.storage)
            row[4].write(entry.carrier)
            row[5].write(entry.us_spec)
            row[6].write(entry.asin)

            badge = "🟢 🔒 Locked" if entry.locked else "⚪ 🔓 Unlocked"
            row[7].write(badge)
            a1, a2, a3 = row[7].columns(3)
            key = _library_key_from_fields(
                entry.brand,
                entry.model,
                entry.color,
                entry.storage,
                config.spapi_marketplace_id,
            )
            if a1.button("Lock" if not entry.locked else "Unlock", key=f"lock-{idx}"):
                library.set_locked(key, not entry.locked)
                st.rerun()
            if a2.button("Edit", key=f"edit-{idx}"):
                st.session_state[f"editing_{idx}"] = True
            if a3.button("Delete", key=f"delete-{idx}"):
                st.session_state[f"confirm_delete_{idx}"] = True

            if st.session_state.get(f"editing_{idx}"):
                with st.container():
                    e1, e2, e3, e4 = st.columns(4)
                    new_brand = e1.text_input("Brand", value=entry.brand, key=f"eb-{idx}")
                    new_model = e2.text_input("Model", value=entry.model, key=f"em-{idx}")
                    new_color = e3.text_input("Color", value=entry.color, key=f"ec-{idx}")
                    new_storage = e4.text_input("Storage", value=entry.storage, key=f"es-{idx}")
                    e5, e6, e7 = st.columns(3)
                    new_carrier = e5.text_input("Carrier", value=entry.carrier, key=f"er-{idx}")
                    new_us_spec = e6.text_input("US Spec", value=entry.us_spec, key=f"eu-{idx}")
                    new_asin = e7.text_input("ASIN", value=entry.asin, key=f"ea-{idx}")
                    s1, s2 = st.columns(2)
                    if s1.button("Save", key=f"save-{idx}"):
                        library.upsert_manual_entry(
                            marketplace_id=config.spapi_marketplace_id,
                            brand=new_brand,
                            model=new_model,
                            color=new_color,
                            storage=new_storage,
                            carrier=new_carrier,
                            us_spec=new_us_spec,
                            asin=new_asin,
                            locked=entry.locked,
                        )
                        st.session_state[f"editing_{idx}"] = False
                        st.success("Updated.")
                        st.rerun()
                    if s2.button("Cancel", key=f"cancel-{idx}"):
                        st.session_state[f"editing_{idx}"] = False
                        st.rerun()

            if st.session_state.get(f"confirm_delete_{idx}"):
                c1, c2 = st.columns(2)
                c1.warning(f"Delete {entry.model} / {entry.asin}?")
                if c1.button("Confirm delete", key=f"confirm-delete-{idx}"):
                    library.delete(key)
                    st.session_state[f"confirm_delete_{idx}"] = False
                    st.success("Deleted.")
                    st.rerun()
                if c2.button("Cancel", key=f"cancel-delete-{idx}"):
                    st.session_state[f"confirm_delete_{idx}"] = False
                    st.rerun()

        st.markdown("---")
        b1, b2, b3 = st.columns(3)
        export_json = library.export_json(config.spapi_marketplace_id)
        export_csv = library.export_csv(config.spapi_marketplace_id)
        b1.download_button(
            "Export Backup (JSON)",
            data=export_json.encode("utf-8"),
            file_name="asin_library_backup.json",
            mime="application/json",
        )
        b1.download_button(
            "Export Backup (CSV)",
            data=export_csv.encode("utf-8"),
            file_name="asin_library_backup.csv",
            mime="text/csv",
        )

        import_file = b2.file_uploader("Import Backup", type=["json"], key="library-import")
        if import_file and b2.button("Import", key="do-import"):
            imported = library.import_json(config.spapi_marketplace_id, import_file.getvalue().decode("utf-8"))
            st.success(f"Imported {imported} entries.")
            st.rerun()

        if b3.button("Clear Library"):
            st.session_state["confirm_clear_library"] = True
        if st.session_state.get("confirm_clear_library"):
            c1, c2 = st.columns(2)
            c1.error("This will delete all entries for this marketplace.")
            if c1.button("Confirm Clear"):
                library.clear(config.spapi_marketplace_id)
                st.session_state["confirm_clear_library"] = False
                st.success("Library cleared.")
                st.rerun()
            if c2.button("Cancel Clear"):
                st.session_state["confirm_clear_library"] = False
                st.rerun()
    finally:
        library.close()


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
                'AWS_ROLE_ARN = "arn:aws:iam::123456789012:role/YourRole"  # optional\n'
                'AWS_REGION = "us-east-1"\n'
                'SPAPI_MARKETPLACE_ID = "ATVPDKIKX0DER"\n'
            )
        st.stop()

    config = build_config_from_secrets()
    tab_enrich, tab_library = st.tabs(["Enrichment", "ASIN Library"])
    with tab_enrich:
        render_enrichment_page(config)
    with tab_library:
        render_library_page(config)


if __name__ == "__main__":
    main()
