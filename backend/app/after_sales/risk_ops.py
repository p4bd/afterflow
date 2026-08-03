"""Deterministic anomaly detection for after-sales operating metrics."""

from typing import Literal

from pydantic import BaseModel, Field


class MetricBucket(BaseModel):
    dimension: Literal["sku", "carrier", "warehouse"]
    value: str = Field(min_length=1)
    issue_type: str = Field(min_length=1)
    current_orders: int = Field(ge=0)
    current_issue_cases: int = Field(ge=0)
    previous_orders: int = Field(ge=0)
    previous_issue_cases: int = Field(ge=0)
    current_loss_amount: int = Field(ge=0)


class OperationsAlert(BaseModel):
    dimension: str
    value: str
    issue_type: str
    severity: Literal["medium", "high"]
    current_rate: float
    previous_rate: float
    current_loss_amount: int
    signals: list[str]


def detect_after_sales_anomalies(
    buckets: list[MetricBucket],
    *,
    minimum_orders: int = 50,
    minimum_issue_cases: int = 5,
    minimum_issue_rate: float = 0.05,
    minimum_rate_uplift: float = 1.5,
    loss_threshold: int = 500_000,
) -> list[OperationsAlert]:
    alerts: list[OperationsAlert] = []
    for bucket in buckets:
        if bucket.current_orders < minimum_orders:
            continue
        current_rate = bucket.current_issue_cases / bucket.current_orders
        previous_rate = bucket.previous_issue_cases / bucket.previous_orders if bucket.previous_orders else 0.0
        rate_spike = bucket.current_issue_cases >= minimum_issue_cases and current_rate >= minimum_issue_rate and (previous_rate == 0 or current_rate >= previous_rate * minimum_rate_uplift)
        loss_spike = bucket.current_loss_amount >= loss_threshold
        signals = []
        if rate_spike:
            signals.append("issue_rate_spike")
        if loss_spike:
            signals.append("loss_threshold_exceeded")
        if not signals:
            continue
        severity = "high" if current_rate >= minimum_issue_rate * 2 or bucket.current_loss_amount >= loss_threshold * 2 else "medium"
        alerts.append(
            OperationsAlert(
                dimension=bucket.dimension,
                value=bucket.value,
                issue_type=bucket.issue_type,
                severity=severity,
                current_rate=round(current_rate, 6),
                previous_rate=round(previous_rate, 6),
                current_loss_amount=bucket.current_loss_amount,
                signals=signals,
            )
        )
    return sorted(alerts, key=lambda alert: (alert.severity != "high", -alert.current_loss_amount, alert.value))
