"""Standalone CSV evaluation with PYTHONPATH=code and python -m evaluation."""

import argparse
import csv
from pathlib import Path

from .metrics import evaluate_predictions, format_summary


def _read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        headers = reader.fieldnames
        if not headers or "request_id" not in headers or len(headers) != len(set(headers)):
            raise ValueError("evaluation CSV requires unique headers including request_id")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("evaluation CSV row widths must match the header")
        return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate existing prediction CSV against provided references")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate_predictions(_read(args.predictions), _read(args.expected))
    except (OSError, ValueError, TypeError, csv.Error):
        parser.error("invalid evaluation input: check CSV paths, request IDs, money and dates")
    print(format_summary(report))


if __name__ == "__main__":
    main()
