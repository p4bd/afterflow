"""Model-agnostic metrics for comparing AfterFlow with external baselines."""


def score_predictions(gold_cases: list[dict], predictions: dict[str, dict]) -> dict:
    covered = 0
    exact = 0
    matched_fields = 0
    total_fields = 0
    missing_ids = []
    by_kind: dict[str, dict[str, int]] = {}
    by_field: dict[str, dict[str, int | float]] = {}
    by_tag: dict[str, dict[str, int | float]] = {}
    for case in gold_cases:
        case_id = case["id"]
        expected = case["expect"]
        prediction = predictions.get(case_id)
        kind_stats = by_kind.setdefault(case["kind"], {"total": 0, "covered": 0, "exact": 0})
        kind_stats["total"] += 1
        for field in expected:
            field_stats = by_field.setdefault(field, {"total": 0, "matched": 0})
            field_stats["total"] += 1
            total_fields += 1
        for tag in case.get("tags", []):
            tag_stats = by_tag.setdefault(tag, {"total": 0, "covered": 0, "exact": 0})
            tag_stats["total"] += 1
        if prediction is None:
            missing_ids.append(case_id)
            continue
        covered += 1
        kind_stats["covered"] += 1
        field_matches = 0
        for field, value in expected.items():
            if prediction.get(field) == value:
                field_matches += 1
                by_field[field]["matched"] += 1
        matched_fields += field_matches
        case_exact = field_matches == len(expected)
        for tag in case.get("tags", []):
            by_tag[tag]["covered"] += 1
            if case_exact:
                by_tag[tag]["exact"] += 1
        if case_exact:
            exact += 1
            kind_stats["exact"] += 1
    total = len(gold_cases)
    for stats in by_field.values():
        stats["accuracy"] = round(stats["matched"] / stats["total"], 6)
    for stats in by_tag.values():
        stats["exact_accuracy"] = round(stats["exact"] / stats["total"], 6)
    return {
        "total": total,
        "coverage": round(covered / total, 6) if total else 0.0,
        "exact_case_accuracy": round(exact / total, 6) if total else 0.0,
        "field_accuracy": round(matched_fields / total_fields, 6) if total_fields else 0.0,
        "by_kind": by_kind,
        "by_field": dict(sorted(by_field.items())),
        "by_tag": dict(sorted(by_tag.items())),
        "missing_ids": missing_ids,
    }
