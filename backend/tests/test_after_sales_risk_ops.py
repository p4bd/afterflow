"""Tests for deterministic after-sales operations anomaly detection."""

from app.after_sales.risk_ops import MetricBucket, detect_after_sales_anomalies


def test_detects_rate_and_loss_spikes_with_traceable_baseline():
    alerts = detect_after_sales_anomalies(
        [
            MetricBucket(
                dimension="sku",
                value="KETTLE-SMART",
                issue_type="damaged_item",
                current_orders=100,
                current_issue_cases=12,
                previous_orders=100,
                previous_issue_cases=3,
                current_loss_amount=700_000,
            )
        ]
    )

    assert len(alerts) == 1
    assert alerts[0].severity == "high"
    assert alerts[0].current_rate == 0.12
    assert alerts[0].previous_rate == 0.03
    assert {"issue_rate_spike", "loss_threshold_exceeded"} <= set(alerts[0].signals)


def test_low_volume_noise_is_suppressed():
    alerts = detect_after_sales_anomalies(
        [
            MetricBucket(
                dimension="carrier",
                value="TINY-CARRIER",
                issue_type="delivery_not_received",
                current_orders=8,
                current_issue_cases=4,
                previous_orders=8,
                previous_issue_cases=0,
                current_loss_amount=10_000,
            )
        ],
        minimum_orders=50,
    )

    assert alerts == []


def test_high_rate_without_material_uplift_does_not_alert():
    alerts = detect_after_sales_anomalies(
        [
            MetricBucket(
                dimension="warehouse",
                value="WH-01",
                issue_type="wrong_item",
                current_orders=200,
                current_issue_cases=12,
                previous_orders=200,
                previous_issue_cases=11,
                current_loss_amount=100_000,
            )
        ]
    )

    assert alerts == []
