from app.after_sales.evaluation import score_predictions


def test_scores_coverage_exact_and_field_accuracy_without_inventing_missing_predictions():
    gold = [
        {"id": "A", "kind": "refund", "tags": ["money", "boundary"], "expect": {"eligibility": "eligible", "refund_amount": 100}},
        {"id": "B", "kind": "reverse", "tags": ["boundary"], "expect": {"action": "return_and_refund"}},
    ]
    predictions = {"A": {"eligibility": "eligible", "refund_amount": 99}}

    result = score_predictions(gold, predictions)

    assert result["coverage"] == 0.5
    assert result["exact_case_accuracy"] == 0.0
    assert result["field_accuracy"] == 0.333333
    assert result["missing_ids"] == ["B"]
    assert result["by_field"] == {
        "action": {"total": 1, "matched": 0, "accuracy": 0.0},
        "eligibility": {"total": 1, "matched": 1, "accuracy": 1.0},
        "refund_amount": {"total": 1, "matched": 0, "accuracy": 0.0},
    }
    assert result["by_tag"] == {
        "boundary": {"total": 2, "covered": 1, "exact": 0, "exact_accuracy": 0.0},
        "money": {"total": 1, "covered": 1, "exact": 0, "exact_accuracy": 0.0},
    }
