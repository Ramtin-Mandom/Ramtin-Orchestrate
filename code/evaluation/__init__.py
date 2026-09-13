"""Independent reference-output evaluation; never imported by the runner."""

from .metrics import (
    EvaluationReport,
    FieldMetrics,
    Mismatch,
    evaluate_predictions,
    format_summary,
)

__all__ = ["EvaluationReport", "FieldMetrics", "Mismatch", "evaluate_predictions", "format_summary"]
