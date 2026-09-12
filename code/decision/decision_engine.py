"""Select the first safe option using the existing financial evaluators."""

from copy import deepcopy
from typing import Optional

from finance import (
    BalanceForecast,
    assess_full_payment,
    calculate_amount_safe_to_pay,
    evaluate_spending_adjustments,
    find_earliest_full_payment_date,
    plan_payments,
)
from models import DecisionResult, FinancialProfile, PaymentPlan, PurchaseRequest
from models.decision_values import (
    AFFORDABLE_LATER,
    AFFORDABLE_NOW,
    AFFORDABLE_WITH_PLAN,
    DO_NOT_PROCEED,
    INSTALLMENTS,
    NOT_AFFORDABLE,
    PAY_IN_FULL,
    WAIT,
)


def _adjusted_plan(profile, forecast, reserve, candidate_plan, result):
    if candidate_plan is not None:
        adjusted = evaluate_spending_adjustments(
            profile, forecast, reserve, candidate_plan
        )
        if adjusted.feasible and adjusted.spending_changes_needed:
            result.affordability_status = AFFORDABLE_WITH_PLAN
            result.recommended_payment_method = INSTALLMENTS
            result.payment_plan = deepcopy(candidate_plan)
            result.spending_changes_needed = list(adjusted.spending_changes_needed)
            return result
    result.affordability_status = NOT_AFFORDABLE
    result.recommended_payment_method = DO_NOT_PROCEED
    return result


def decide_purchase(
    profile: FinancialProfile,
    purchase_request: PurchaseRequest,
    forecast: BalanceForecast,
    *,
    adjustment_plan: Optional[PaymentPlan] = None,
) -> DecisionResult:
    """Choose full now, baseline plan, wait, flexible adjustment, or no purchase.

    Reserve selection matches the safe-amount module: preserve the higher
    explicit minimum/preferred balance. Missing reserves fail there. Optional
    adjustment_plan is an existing proposal, evaluated only after baseline
    options fail; this engine constructs no alternative payment schedules.
    Earliest-date metadata retains the date evaluator's after-events convention,
    including today when applicable. Explanations remain empty; inputs unchanged.
    """
    safe_amount = calculate_amount_safe_to_pay(profile, forecast)
    reserve = max(
        value
        for value in (profile.minimum_balance, profile.preferred_balance)
        if value is not None
    )
    full = assess_full_payment(purchase_request, safe_amount)
    earliest = find_earliest_full_payment_date(
        forecast, purchase_request.amount, reserve
    )
    result = DecisionResult(
        amount_safe_to_pay=safe_amount, earliest_date_for_full_payment=earliest
    )
    # A zero purchase still cannot approve a timeline already below reserve.
    full_validation = evaluate_spending_adjustments(
        profile, forecast, reserve, purchase_request
    )
    if (
        full.can_pay_in_full
        and full_validation.feasible
        and not full_validation.spending_changes_needed
    ):
        result.affordability_status = full.affordability_status
        result.recommended_payment_method = full.recommended_payment_method
        return result
    plan = plan_payments(purchase_request, forecast, reserve, safe_amount)
    if plan is not None:
        result.affordability_status = AFFORDABLE_WITH_PLAN
        result.recommended_payment_method = INSTALLMENTS
        result.payment_plan = plan
        return result
    if earliest is not None:
        result.affordability_status = AFFORDABLE_LATER
        result.recommended_payment_method = WAIT
        return result
    if full_validation.feasible and full_validation.spending_changes_needed:
        result.affordability_status = AFFORDABLE_NOW
        result.recommended_payment_method = PAY_IN_FULL
        result.spending_changes_needed = list(full_validation.spending_changes_needed)
        return result
    if (
        adjustment_plan is not None
        and adjustment_plan.total_amount != purchase_request.amount
    ):
        raise ValueError(
            "adjustment_plan.total_amount must equal purchase_request.amount"
        )
    return _adjusted_plan(profile, forecast, reserve, adjustment_plan, result)
