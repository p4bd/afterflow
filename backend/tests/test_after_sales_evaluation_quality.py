"""Quality gates for the fixed AfterFlow evaluation corpus."""

import json
from collections import Counter
from pathlib import Path

CASES = json.loads((Path(__file__).parent / "fixtures" / "after_sales_evaluation.json").read_text(encoding="utf-8"))
CURATED_SOURCE = "curated_boundary_v2"
REQUIRED_BOUNDARY_TAGS = {
    "operator_limit_below",
    "operator_limit_exact",
    "operator_limit_above",
    "high_value_below",
    "high_value_exact",
    "manual_review_below",
    "manual_review_exact",
    "balance_cap",
    "zero_balance",
    "uncovered_precedence",
    "missing_logistics",
    "missing_visual",
    "pod_high_risk",
    "claims_medium",
    "claims_high",
    "unsupported_issue",
    "missing_human_confirmation",
    "recovery_below_reverse_cost",
    "recovery_exact_reverse_cost",
    "replacement_in_stock",
    "replacement_out_of_stock",
    "minimum_orders_below",
    "minimum_orders_exact",
    "minimum_cases_below",
    "minimum_cases_exact",
    "issue_rate_below",
    "issue_rate_exact",
    "uplift_below",
    "uplift_exact",
    "loss_below",
    "loss_exact",
    "high_severity_rate_exact",
    "high_severity_loss_exact",
}


def test_corpus_expands_the_original_100_with_curated_boundary_cases():
    curated = [case for case in CASES if case.get("source") == CURATED_SOURCE]

    assert len(CASES) >= 130
    assert len(curated) >= 30


def test_curated_cases_are_explainable_and_cover_required_boundaries():
    curated = [case for case in CASES if case.get("source") == CURATED_SOURCE]
    observed_tags = {tag for case in curated for tag in case.get("tags", [])}

    assert all(case.get("rationale", "").strip() for case in curated)
    assert REQUIRED_BOUNDARY_TAGS <= observed_tags


def test_corpus_has_unique_ids_and_no_duplicate_inputs_within_a_domain():
    ids = [case["id"] for case in CASES]
    signatures = [(case["kind"], json.dumps(case["input"], sort_keys=True)) for case in CASES]

    assert len(ids) == len(set(ids))
    assert len(signatures) == len(set(signatures))


def test_each_domain_has_positive_and_negative_or_fallback_outcomes():
    refund = Counter(case["expect"]["eligibility"] for case in CASES if case["kind"] == "refund")
    reverse = Counter(case["expect"]["outcome"] for case in CASES if case["kind"] == "reverse")
    operations = Counter(case["expect"]["alert"] for case in CASES if case["kind"] == "operations")

    assert {"eligible", "eligible_with_approval", "ineligible", "needs_evidence"} <= refund.keys()
    assert {"decided", "needs_evidence", "unsupported"} <= reverse.keys()
    assert {True, False} <= operations.keys()


def test_risk_signal_cases_are_explainable_and_discriminative():
    signal_cases = [case for case in CASES if case.get("source") == "curated_risk_signals_v1"]

    assert len(signal_cases) >= 15
    assert all(case.get("rationale", "").strip() for case in signal_cases)
    assert all(case.get("tags") for case in signal_cases)
    # Every risk-signal case carries the new scorecard signals and the output tier.
    for case in signal_cases:
        assert "sign_receipt_hours" in case["input"] or "device_reuse" in case["input"] or "refund_rate" in case["input"]
        assert "risk_tier" in case["expect"]
    # They must be discriminative: both auto and elevated tiers must be present.
    tiers = {case["expect"]["risk_tier"] for case in signal_cases}
    assert tiers >= {"auto", "review", "supervisor"}


def test_boundary_tags_match_the_values_they_claim_to_cover():
    def tagged(name: str) -> dict:
        matches = [case for case in CASES if name in case.get("tags", [])]
        assert matches, f"missing boundary tag: {name}"
        return matches[0]["input"]

    assert tagged("operator_limit_below")["item"] == tagged("operator_limit_below")["operator"] - 1
    assert tagged("operator_limit_exact")["item"] == tagged("operator_limit_exact")["operator"]
    assert tagged("operator_limit_above")["item"] == tagged("operator_limit_above")["operator"] + 1
    assert tagged("high_value_below")["item"] == tagged("high_value_below")["high"] - 1
    assert tagged("high_value_exact")["item"] == tagged("high_value_exact")["high"]
    assert tagged("manual_review_below")["item"] == tagged("manual_review_below")["manual"] - 1
    assert tagged("manual_review_exact")["item"] == tagged("manual_review_exact")["manual"]

    assert tagged("minimum_orders_below")["orders"] == 49
    assert tagged("minimum_orders_exact")["orders"] == 50
    assert tagged("minimum_cases_below")["issue_cases"] == 4
    assert tagged("minimum_cases_exact")["issue_cases"] == 5
    assert tagged("loss_below")["loss"] == 499_999
    assert tagged("loss_exact")["loss"] == 500_000
    assert tagged("high_severity_loss_exact")["loss"] == 1_000_000

    rate_below = tagged("issue_rate_below")
    rate_exact = tagged("issue_rate_exact")
    uplift_below = tagged("uplift_below")
    uplift_exact = tagged("uplift_exact")
    assert rate_below["issue_cases"] / rate_below["orders"] < 0.05
    assert rate_exact["issue_cases"] / rate_exact["orders"] == 0.05
    assert uplift_below["issue_cases"] / uplift_below["orders"] < 1.5 * uplift_below["prev_cases"] / uplift_below["prev_orders"]
    assert uplift_exact["issue_cases"] / uplift_exact["orders"] == 1.5 * uplift_exact["prev_cases"] / uplift_exact["prev_orders"]
