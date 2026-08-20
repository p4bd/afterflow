"""Tests for the transparent 0-100 risk scorecard and intervention tiering."""

from app.after_sales.risk_scoring import (
    DEFAULT_DUAL_APPROVAL_THRESHOLD,
    RiskSignals,
    RiskTier,
    assess_risk,
)
from app.after_sales.schemas import RiskLevel


def _clean() -> RiskSignals:
    return RiskSignals()


def test_clean_signals_score_zero_auto_low():
    assessment = assess_risk(_clean())

    assert assessment.score == 0
    assert assessment.level is RiskLevel.LOW
    assert assessment.tier is RiskTier.AUTO
    assert assessment.contributions == []


def test_repeat_claims_force_high_and_supervisor():
    assessment = assess_risk(RiskSignals(not_received_claims_180d=3))

    assert assessment.level is RiskLevel.HIGH
    assert assessment.tier is RiskTier.SUPERVISOR
    assert assessment.rule_override == "repeat_claims_or_pod_conflict"
    assert any(c.signal == "repeat_not_received_claims" for c in assessment.contributions)


def test_pod_conflict_is_decisive_high():
    assessment = assess_risk(
        RiskSignals(is_delivery_not_received=True, carrier_has_proof_of_delivery=True)
    )

    assert assessment.level is RiskLevel.HIGH
    assert assessment.tier is RiskTier.SUPERVISOR


def test_sign_receipt_time_both_extremes_add_points():
    immediate = assess_risk(RiskSignals(sign_receipt_hours=1))
    late = assess_risk(RiskSignals(sign_receipt_hours=30 * 24 + 1))
    normal = assess_risk(RiskSignals(sign_receipt_hours=96))

    assert immediate.score > 0
    assert late.score > 0
    assert normal.score == 0


def test_high_risk_high_amount_triggers_four_eyes():
    assessment = assess_risk(
        RiskSignals(not_received_claims_180d=3),
        refund_amount=DEFAULT_DUAL_APPROVAL_THRESHOLD + 1,
    )

    assert assessment.tier is RiskTier.FOUR_EYES


def test_medium_tier_from_elevated_claims_or_high_value():
    claims = assess_risk(RiskSignals(not_received_claims_180d=2))
    high_value = assess_risk(RiskSignals(high_value_refund=True))

    assert claims.level is RiskLevel.MEDIUM
    assert claims.tier is RiskTier.REVIEW
    assert high_value.level is RiskLevel.MEDIUM
    assert high_value.tier is RiskTier.REVIEW


def test_device_reuse_and_young_account_stack_points():
    risky = assess_risk(
        RiskSignals(account_age_days=10, historical_refund_rate=0.4, address_changes_30d=3, device_reuse=True)
    )

    assert risky.score == 8 + 15 + 10 + 20
    assert risky.level is RiskLevel.MEDIUM
    assert risky.tier is RiskTier.REVIEW
