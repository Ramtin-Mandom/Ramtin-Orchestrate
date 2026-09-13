"""Final deterministic recommendation selection."""

from .decision_engine import decide_purchase
from .explanation import attach_decision_explanation, build_decision_explanation

__all__ = ["attach_decision_explanation", "build_decision_explanation", "decide_purchase"]
