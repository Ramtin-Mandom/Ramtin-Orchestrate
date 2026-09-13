"""Command-line entry point for the existing Buy or Wait pipeline."""

import argparse
from datetime import date
from pathlib import Path

from logging_config import LOG_LEVELS, configure_logging
from pipeline import DEFAULT_HORIZON_DAYS, PipelineError, run_pipeline


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process structured financial requests into output.csv")
    parser.add_argument("--input", type=Path, default=Path("dataset/requests.csv"),
                        help="structured requests CSV (default: dataset/requests.csv)")
    parser.add_argument("--media-root", "--media", type=Path, default=Path("dataset/media"),
                        help="associated local media root (default: dataset/media)")
    parser.add_argument("--output", type=Path, default=Path("output.csv"),
                        help="destination CSV (default: output.csv)")
    parser.add_argument("--as-of-date", type=_date,
                        help="override all row request_date values with YYYY-MM-DD")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS,
                        help="forecast days after the request date (default: 90)")
    parser.add_argument("--log-level", type=str.upper, choices=LOG_LEVELS, default="INFO",
                        help="stderr application log level (default: INFO)")
    args = parser.parse_args(argv)
    logger = configure_logging(args.log_level)
    try:
        results = run_pipeline(args.input, args.media_root, args.output,
                               as_of_date=args.as_of_date, horizon_days=args.horizon_days)
    except PipelineError as exc:
        logger.error("Pipeline failed (%s)", type(exc).__name__)
        parser.error(str(exc))
    print(f"Wrote {len(results)} decisions to {args.output}.")
    if results.failures:
        logger.error("Batch completed with failed requests=%d", len(results.failures))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
