"""Small, inspectable demo dataset for local AfterFlow development."""

ORDERS = {
    "ORDER-1001": {
        "order_id": "ORDER-1001",
        "customer_id": "CUSTOMER-001",
        "region": "CN",
        "sku": "PHONE-X-256",
        "item_paid": 89_900,
        "shipping_paid": 1_000,
        "status": "shipped",
    },
    "ORDER-1002": {
        "order_id": "ORDER-1002",
        "customer_id": "CUSTOMER-002",
        "region": "CN",
        "sku": "HEADSET-LITE",
        "item_paid": 9_900,
        "shipping_paid": 0,
        "status": "shipped",
    },
    "ORDER-1003": {
        "order_id": "ORDER-1003",
        "customer_id": "CUSTOMER-003",
        "region": "CN",
        "sku": "KETTLE-SMART",
        "item_paid": 32_800,
        "shipping_paid": 800,
        "status": "delivered",
    },
}

PAYMENTS = {
    "ORDER-1001": {"refundable_balance": 90_900, "currency": "CNY", "payment_status": "paid"},
    "ORDER-1002": {"refundable_balance": 9_900, "currency": "CNY", "payment_status": "paid"},
    "ORDER-1003": {"refundable_balance": 33_600, "currency": "CNY", "payment_status": "paid"},
}

LOGISTICS = {
    "ORDER-1001": {"status": "in_transit", "proof_of_delivery": False, "carrier": "DEMO-EXPRESS"},
    "ORDER-1002": {"status": "delivered", "proof_of_delivery": True, "carrier": "DEMO-EXPRESS"},
    "ORDER-1003": {"status": "delivered", "proof_of_delivery": True, "carrier": "DEMO-EXPRESS"},
}

CUSTOMER_RISK = {
    "CUSTOMER-001": {"not_received_claims_180d": 0, "refund_cases_180d": 0},
    "CUSTOMER-002": {"not_received_claims_180d": 3, "refund_cases_180d": 4},
    "CUSTOMER-003": {"not_received_claims_180d": 0, "refund_cases_180d": 1},
}

POLICIES = {
    "CN": {
        "policy_id": "AFTER-SALES-CN",
        "version": "2026.07",
        "covered_issues": [
            "delivery_not_received",
            "damaged_item",
            "wrong_item",
            "quality_issue",
            "refund_amount_dispute",
        ],
        "refund_shipping_issues": ["delivery_not_received"],
        "return_required_issues": ["damaged_item", "wrong_item"],
        "manual_review_amount": 100_000,
        "high_value_amount": 50_000,
    }
}

INVENTORY = {
    "PHONE-X-256": {"available": 3, "replacement_unit_cost": 61_000},
    "HEADSET-LITE": {"available": 0, "replacement_unit_cost": 5_200},
    "KETTLE-SMART": {"available": 12, "replacement_unit_cost": 18_000},
}

REVERSE_COSTS = {
    "PHONE-X-256": {"return_shipping_cost": 1_800, "handling_cost": 800, "expected_recovery_value": 45_000, "replacement_shipping_cost": 1_200},
    "HEADSET-LITE": {"return_shipping_cost": 900, "handling_cost": 400, "expected_recovery_value": 2_000, "replacement_shipping_cost": 600},
    "KETTLE-SMART": {"return_shipping_cost": 1_200, "handling_cost": 600, "expected_recovery_value": 1_000, "replacement_shipping_cost": 800},
}

OPERATIONS_METRICS = [
    {"dimension": "sku", "value": "KETTLE-SMART", "issue_type": "damaged_item", "current_orders": 100, "current_issue_cases": 12, "previous_orders": 100, "previous_issue_cases": 3, "current_loss_amount": 700_000},
    {"dimension": "carrier", "value": "DEMO-EXPRESS", "issue_type": "delivery_not_received", "current_orders": 1_000, "current_issue_cases": 80, "previous_orders": 1_100, "previous_issue_cases": 30, "current_loss_amount": 1_400_000},
    {"dimension": "warehouse", "value": "WH-01", "issue_type": "wrong_item", "current_orders": 400, "current_issue_cases": 40, "previous_orders": 420, "previous_issue_cases": 10, "current_loss_amount": 480_000},
    {"dimension": "sku", "value": "HEADSET-LITE", "issue_type": "quality_issue", "current_orders": 500, "current_issue_cases": 8, "previous_orders": 480, "previous_issue_cases": 7, "current_loss_amount": 60_000},
]
