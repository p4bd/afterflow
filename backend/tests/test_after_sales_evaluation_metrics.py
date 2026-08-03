from app.after_sales.evaluation import score_predictions


def test_scores_coverage_exact_and_field_accuracy_without_inventing_missing_predictions():
    gold = [
        {"id": "A", "kind": "refund", "expect": {"eligibility": "eligible", "refund_amount": 100}},
        {"id": "B", "kind": "reverse", "expect": {"action": "return_and_refund"}},
    ]
    predictions = {"A": {"eligibility": "eligible", "refund_amount": 99}}

    result = score_predictions(gold, predictions)

    assert result["coverage"] == 0.5
    assert result["exact_case_accuracy"] == 0.0
    assert result["field_accuracy"] == 0.5
    assert result["missing_ids"] == ["B"]
