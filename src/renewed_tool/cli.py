from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .csv_pipeline import run_enrichment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="renewed-tool",
        description="Amazon Renewed CSV checker (Keepa + SP-API + ASIN library).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    enrich_parser = subparsers.add_parser("enrich", help="Enrich a CSV file")
    enrich_parser.add_argument("--input", required=True, help="Input CSV path")
    enrich_parser.add_argument("--output", required=True, help="Output CSV path")
    enrich_parser.add_argument(
        "--summary-json",
        default=None,
        help="Optional summary JSON output path",
    )
    return parser


def _run_enrich(args: argparse.Namespace) -> None:
    config = load_config()
    summary = run_enrichment(
        config=config,
        input_csv=Path(args.input),
        output_csv=Path(args.output),
    )
    print(
        f"Processed {summary.total_rows} rows, resolved {summary.resolved_rows}, "
        f"unresolved {summary.unresolved_rows}. Output: {args.output}"
    )
    if args.summary_json:
        payload = {
            "total_rows": summary.total_rows,
            "resolved_rows": summary.resolved_rows,
            "unresolved_rows": summary.unresolved_rows,
            "output_csv": str(Path(args.output).resolve()),
        }
        Path(args.summary_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote summary JSON: {args.summary_json}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "enrich":
        _run_enrich(args)


if __name__ == "__main__":
    main()
