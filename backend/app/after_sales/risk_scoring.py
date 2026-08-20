"""Transparent 0-100 refund risk scorecard and intervention tiering.

Designed to mirror how production e-commerce risk teams score refund claims:
a set of weighted signals sum to a 0-100 score, the score maps to an
intervention tier (auto / customer-service review / supervisor / four-eyes)
that drives how many humans must sign before money moves.

The scorecard is deterministic and explainable — every signal contributes named
points that can be shown to a reviewer or a customer (why is this refund
flagged?). HONEST LIMITATION: the weights and thresholds are deterministic
design starting points derived from industry patterns (Stripe Radar bands,
refund-spam scorecard, DCL fraud scoring) and the decision boundaries we
curate; they are NOT yet calibrated on production data. Calibration against a
labeled hold-out set, with the human-review outcomes fed back in, is the
documented next step before production use.
"""

from pydantic import BaseModel, Field

from .schemas import RiskLevel, RiskTier


class RiskSignals(BaseModel):
    """Signals available to the scorecard. Absent signals contribute 0 points."""

    not_received_claims_180d: int = 0
    refund_cases_180d: int = 0
    carrier_has_proof_of_delivery: bool = False
    is_delivery_not_received: bool = False
    high_value_refund: bool = False
    # Time (hours) between delivery and the claim. Both extremes are
    # suspicious: claimed immediately after receipt, or long after delivery.
    sign_receipt_hours: int | None = None
    account_age_days: int | None = None
    historical_refund_rate: float | None = None  # 0..1
    address_changes_30d: int = 0
    device_reuse: bool = False


class RiskContribution(BaseModel):
    signal: str
    points: int
    reason: str


class RiskAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    level: RiskLevel
    tier: RiskTier
    contributions: list[RiskContribution] = Field(default_factory=list)
    rule_override: str | None = None


# A claim reaching this score with a high amount requires two sign-offs.
DEFAULT_DUAL_APPROVAL_THRESHOLD = 500_000  # minor currency units (¥5000)
# Fewer than this many days since delivery counts as "immediately after receipt".
IMMEDIATE_SIGN_RECEIPT_HOURS = 2
# More than this many days since delivery counts as "long after delivery".
LATE_SIGN_RECEIPT_HOURS = 30 * 24


def _add(contributions: list[RiskContribution], signal: str, points: int, reason: str) -> int:
    if points:
        contributions.append(RiskContribution(signal=signal, points=points, reason=reason))
    return points


def assess_risk(
    signals: RiskSignals,
    *,
    refund_amount: int = 0,
    dual_approval_threshold: int = DEFAULT_DUAL_APPROVAL_THRESHOLD,
) -> RiskAssessment:
    """Compute a deterministic risk score, level, and intervention tier."""
    contributions: list[RiskContribution] = []
    score = 0
    decisive_high = False

    # Decisive signals: a repeat claimant or a carrier that has proof of
    # delivery for a "not received" claim forces supervisor review regardless
    # of the rest of the score.
    if signals.not_received_claims_180d >= 3:
        decisive_high = True
        score += _add(contributions, "repeat_not_received_claims", 40, "≥3 未收到货索赔，疑似重复薅羊毛")
    elif signals.not_received_claims_180d == 2:
        score += _add(contributions, "elevated_not_received_claims", 20, "2 次未收到货索赔")

    if signals.is_delivery_not_received and signals.carrier_has_proof_of_delivery:
        decisive_high = True
        score += _add(contributions, "carrier_proof_of_delivery_conflict", 30, "承运商有签收凭证但用户称未收到")

    if signals.high_value_refund:
        score += _add(contributions, "high_value_refund", 15, "退款金额达高价值线")

    if signals.refund_cases_180d >= 5:
        score += _add(contributions, "high_refund_volume", 10, "180 天退款次数偏高")
    elif signals.refund_cases_180d >= 3:
        score += _add(contributions, "elevated_refund_volume", 5, "180 天退款次数略高")

    # Temporal signal: claimed too fast or too late after delivery.
    if signals.sign_receipt_hours is not None:
        if signals.sign_receipt_hours <= IMMEDIATE_SIGN_RECEIPT_HOURS:
            score += _add(contributions, "immediate_claim_after_receipt", 15, "签收后极短时间即投诉")
        elif signals.sign_receipt_hours >= LATE_SIGN_RECEIPT_HOURS:
            score += _add(contributions, "late_claim_after_delivery", 15, "签收后很久才投诉")

    # Account and network signals.
    if signals.account_age_days is not None and signals.account_age_days < 30:
        score += _add(contributions, "young_account", 8, "新账号")

    if signals.historical_refund_rate is not None:
        if signals.historical_refund_rate >= 0.3:
            score += _add(contributions, "high_refund_rate", 15, "历史退款率≥30%")
        elif signals.historical_refund_rate >= 0.15:
            score += _add(contributions, "elevated_refund_rate", 8, "历史退款率偏高")

    if signals.address_changes_30d >= 2:
        score += _add(contributions, "frequent_address_change", 10, "近 30 天地址变更频繁")

    if signals.device_reuse:
        score += _add(contributions, "device_reuse", 20, "设备关联多个账号")

    score = min(100, score)

    # Risk level: decisive signals and high scores are high; medium floor for
    # elevated claims and high value (keeps the coarse label stable).
    if decisive_high or score >= 60:
        level = RiskLevel.HIGH
    elif signals.not_received_claims_180d == 2 or signals.high_value_refund or score >= 30:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    # Intervention tier: score + amount drive how many humans must sign.
    if level is RiskLevel.HIGH and refund_amount >= dual_approval_threshold:
        tier = RiskTier.FOUR_EYES
    elif level is RiskLevel.HIGH:
        tier = RiskTier.SUPERVISOR
    elif level is RiskLevel.MEDIUM:
        tier = RiskTier.REVIEW
    else:
        tier = RiskTier.AUTO

    rule_override = "repeat_claims_or_pod_conflict" if decisive_high else None
    return RiskAssessment(
        score=score,
        level=level,
        tier=tier,
        contributions=contributions,
        rule_override=rule_override,
    )
