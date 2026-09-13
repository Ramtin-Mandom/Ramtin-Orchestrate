"""Load challenge contexts by default; opt into the legacy decision pipeline."""

import argparse
from datetime import date
from pathlib import Path

from challenge_pipeline import run_challenge_pipeline
from loaders import DatasetValidationError
from logging_config import LOG_LEVELS, configure_logging
from pipeline import DEFAULT_HORIZON_DAYS, PipelineError, run_pipeline


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load and validate Buy or Wait challenge contexts")
    parser.add_argument("--mode", choices=("challenge", "legacy"), default="challenge",
                        help="challenge loads joined contexts; legacy runs the synthetic decision pipeline")
    parser.add_argument("--dataset", type=Path, default=Path("dataset"),
                        help="challenge dataset directory (default: dataset)")
    parser.add_argument("--input", type=Path, default=Path("dataset/requests.csv"),
                        help="legacy-mode structured requests CSV")
    parser.add_argument("--media-root", "--media", type=Path, default=Path("dataset/media"),
                        help="associated local media root (default: dataset/media)")
    parser.add_argument("--output", type=Path, default=Path("output.csv"),
                        help="destination CSV (default: output.csv)")
    parser.add_argument("--as-of-date", type=_date,
                        help="override all row request_date values with YYYY-MM-DD")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS,
                        help="forecast days after the request date (default: 90)")
    parser.add_argument("--use-ai-extraction", action="store_true",
                        help="resolve blank amounts/lifecycle changes from messages and images "
                             "via OPENAI_API_KEY (off by default: no billable calls)")
    parser.add_argument("--log-level", type=str.upper, choices=LOG_LEVELS, default="INFO",
                        help="stderr application log level (default: INFO)")
    args = parser.parse_args(argv)
    logger = configure_logging(args.log_level)
    if args.mode == "challenge":
        try:
            result = run_challenge_pipeline(
                args.dataset, args.output, use_ai_extraction=args.use_ai_extraction,
                horizon_days=args.horizon_days,
            )
        except DatasetValidationError as exc:
            parser.error(str(exc))
        print(f"Wrote {len(result.decisions)} challenge decisions to {args.output}.")
        return result
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
