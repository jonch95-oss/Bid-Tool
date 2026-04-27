# Amazon Renewed ASIN & Pricing Enrichment Tool

CLI tool that:

1. Reads an inventory CSV
2. Resolves correct ASINs through Keepa search
3. Verifies model/color/capacity/grade against catalog data
4. Pulls renewed/used pricing through Amazon SP-API
5. Saves validated mappings in an ASIN library (SQLite) for fast re-use

## Quick Start

### 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Set environment variables

Required:

- `KEEPA_API_KEY`
- `LWA_CLIENT_ID`
- `LWA_CLIENT_SECRET`
- `LWA_REFRESH_TOKEN`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Optional:

- `AWS_SESSION_TOKEN` (if using temporary credentials)
- `AWS_REGION` (default: `us-east-1`)
- `SPAPI_MARKETPLACE_ID` (default: `ATVPDKIKX0DER`)
- `KEEPA_DOMAIN` (default: `1` for US)
- `ASIN_LIBRARY_PATH` (default: `./asin_library.db`)

### 3) Input CSV format

Headers expected:

```csv
sku,title,model,color,capacity,grade,asin
```

- `asin` is optional (hint only)
- `title` is optional but highly recommended for better matching

### 4) Run enrichment

```bash
renewed-tool enrich \
  --input ./input.csv \
  --output ./output.csv \
  --summary-json ./summary.json
```

If the `renewed-tool` command is not on your PATH, run:

```bash
python3 -m renewed_tool.cli enrich \
  --input ./input.csv \
  --output ./output.csv \
  --summary-json ./summary.json
```

The output CSV contains:

- resolved ASIN
- price + currency
- match source (`library` or `keepa+spapi`)
- match score
- status + explanation

## How matching works

- The tool normalizes model/color/capacity/grade to a canonical form.
- It checks the ASIN library first for exact normalized keys.
- If no cached mapping exists, it queries Keepa search using model + attributes.
- It validates candidates against Amazon catalog attributes from SP-API.
- It retrieves renewed/used offers from SP-API pricing.
- If successful, it stores the mapping in the library for next time.

## Notes

- Grade validation is based on condition/subcondition text from SP-API offers.
- Keepa and catalog fields can vary by category; if your category needs custom
  parsing rules, extend `normalization.py` and `resolver.py`.

## Deploy on Streamlit Community Cloud

This repository now includes a Streamlit entry file at:

- `streamlit_app.py`

### Streamlit deploy settings

- **Repository**: your GitHub repo
- **Branch**: `cursor/renewed-asin-tool-4020` (or merge to `main` first)
- **Main file path**: `streamlit_app.py`

### Required secrets in Streamlit

In app settings -> **Secrets**, define:

```toml
KEEPA_API_KEY = "..."
LWA_CLIENT_ID = "..."
LWA_CLIENT_SECRET = "..."
LWA_REFRESH_TOKEN = "..."
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."
AWS_REGION = "us-east-1"
SPAPI_MARKETPLACE_ID = "ATVPDKIKX0DER"
```

Optional:

```toml
AWS_SESSION_TOKEN = "..."
KEEPA_DOMAIN = "1"
CANDIDATE_LIMIT = "25"
ASIN_LIBRARY_PATH = "./asin_library.db"
```

Then deploy and upload your CSV in the web app UI.
