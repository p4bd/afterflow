"""Model-agnostic metrics for comparing AfterFlow with external baselines."""


def score_predictions(gold_cases: list[dict], predictions: dict[str, dict]) -> dict:
    covered = 0
    exact = 0
    matched_fields = 0
    predicted_fields = 0
    missing_ids = []
    by_kind: dict[str, dict[str, int]] = {}
    for case in gold_cases:
        case_id = case["id"]
        expected = case["expect"]
        prediction = predictions.get(case_id)
        kind_stats = by_kind.setdefault(case["kind"], {"total": 0, "covered": 0, "exact": 0})
        kind_stats["total"] += 1
        if prediction is None:
            missing_ids.append(case_id)
            continue
        covered += 1
        kind_stats["covered"] += 1
        field_matches = sum(prediction.get(field) == value for field, value in expected.items())
        matched_fields += field_matches
        predicted_fields += len(expected)
        if field_matches == len(expected):
            exact += 1
            kind_stats["exact"] += 1
    total = len(gold_cases)
    return {
        "total": total,
        "coverage": round(covered / total, 6) if total else 0.0,
        "exact_case_accuracy": round(exact / total, 6) if total else 0.0,
        "field_accuracy": round(matched_fields / predicted_fields, 6) if predicted_fields else 0.0,
        "by_kind": by_kind,
        "missing_ids": missing_ids,
    }
