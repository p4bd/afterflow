"""Score an external model's JSON predictions against the fixed AfterFlow suite."""

import argparse
import json
from pathlib import Path

from app.after_sales.evaluation import score_predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("predictions", type=Path, help="JSON object keyed by case id")
    parser.add_argument(
        "--gold",
        type=Path,
        default=Path(__file__).parents[1] / "tests" / "fixtures" / "after_sales_evaluation_100.json",
    )
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    print(json.dumps(score_predictions(gold, predictions), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
