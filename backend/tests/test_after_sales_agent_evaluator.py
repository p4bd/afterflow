from scripts.evaluate_afterflow_agent import _route_agent_input, _summary, _tool_result_error, _workflow_input_hashes, score_record

EXPECT = {
    "order_id": "ORDER-1001",
    "issue_type": "delivery_not_received",
    "status": "decided",
    "human_intervention": False,
    "forbidden_tools": [],
}


def _record(**updates):
    case = {
        "order_id": "ORDER-1001",
        "issue_type": "delivery_not_received",
        "status": "decided",
        "reply_draft": "已完成规则评估，下一步创建处理方案。",
    }
    record = {"terminal_case": case, "terminal_actions": [], "tool_calls": [], "output": "已保存案件并完成规则评估。"}
    record.update(updates)
    return record


def test_scores_order_issue_and_joint_fields_separately():
    record = _record(terminal_case={**_record()["terminal_case"], "issue_type": "quality_issue"})

    score = score_record(record, EXPECT)

    assert score["order_id_match"] is True
    assert score["issue_type_match"] is False
    assert score["fields_joint_match"] is False


def test_missing_issue_with_created_action_fails_side_effect_score():
    expect = {
        **EXPECT,
        "issue_type": None,
        "status": "awaiting_clarification",
        "human_intervention": True,
        "forbidden_tools": ["create_after_sales_action"],
        "forbidden_action_types": ["refund", "resend"],
    }
    record = _record(
        terminal_case={**_record()["terminal_case"], "issue_type": None, "status": "awaiting_clarification"},
        tool_calls=[{"id": "call-1", "name": "create_after_sales_action"}],
    )

    score = score_record(record, expect)

    assert score["forbidden_tools_absent"] is False


def test_persisted_forbidden_action_fails_even_when_tool_trace_is_missing():
    expect = {**EXPECT, "forbidden_action_types": ["refund"]}
    record = _record(terminal_actions=[{"action_type": "refund", "status": "approved"}])

    assert score_record(record, expect)["forbidden_side_effects_absent"] is False


def test_human_intervention_reports_use_separate_denominators():
    required = score_record(
        _record(terminal_case={**_record()["terminal_case"], "status": "awaiting_clarification"}),
        {**EXPECT, "status": "awaiting_clarification", "human_intervention": True},
    )
    not_required = score_record(_record(), EXPECT)

    assert required["required_human_intervention_present"] is True
    assert required["unwanted_human_intervention_absent"] is None
    assert not_required["required_human_intervention_present"] is None
    assert not_required["unwanted_human_intervention_absent"] is True


def test_agent_output_and_saved_draft_are_scored_independently():
    record = _record(output="主管已批准，退款已经到账。")

    score = score_record(record, EXPECT)

    assert score["agent_reply_consistent"] is False
    assert score["draft_reply_consistent"] is True


def test_negated_completion_claim_is_not_scored_as_a_false_promise():
    record = _record(output="审批通过前我**无法**在此环节承诺退款成功。")

    assert score_record(record, EXPECT)["agent_reply_consistent"] is True


def test_no_case_cannot_pass_by_claiming_a_saved_case():
    score = score_record(_record(terminal_case=None, output="案件已保存，当前待审批。"), EXPECT)

    assert score["case_created"] is False
    assert score["agent_reply_consistent"] is False
    assert score["stage_match"] is False


def test_summary_reports_completion_tool_latency_and_token_metrics():
    passed = _record(
        task_id="pass",
        attempt=1,
        error=None,
        tool_calls=[{"id": "1", "name": "create_after_sales_case"}, {"id": "2", "name": "create_after_sales_action"}],
        tool_count=2,
        latency_ms=100,
        token_usage={"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
    )
    failed = _record(task_id="fail", attempt=1, error=None, terminal_case=None, tool_calls=[], tool_count=0, latency_ms=300, token_usage=None)
    for record in (passed, failed):
        record["score"] = score_record(record, EXPECT)

    summary = _summary([passed, failed], metadata={}, raw_name="runs.jsonl")

    assert summary["task_completed_accuracy"] == 0.5
    assert summary["total_tool_calls"] == 2
    assert summary["tool_call_counts"] == {"create_after_sales_action": 1, "create_after_sales_case": 1}
    assert summary["latency_p50_ms"] == 200
    assert summary["latency_p95_ms"] == 300
    assert summary["token_usage_coverage"] == 0.5
    assert summary["average_input_tokens"] == 80
    assert summary["average_output_tokens"] == 20
    assert summary["average_total_tokens"] == 100


def test_summary_counts_context_calls_repeated_after_compound_intake():
    record = _record(
        task_id="redundant",
        attempt=1,
        error=None,
        tool_names=[
            "get_logistics_evidence",
            "create_after_sales_case",
            "get_after_sales_order",
            "get_after_sales_payment",
            "create_after_sales_action",
        ],
        tool_count=4,
        latency_ms=100,
        token_usage=None,
    )
    record["score"] = score_record(record, EXPECT)

    summary = _summary([record], metadata={}, raw_name="runs.jsonl")

    assert summary["redundant_context_calls_after_intake"] == 2
    assert summary["runs_with_redundant_context_calls_after_intake"] == 1
    assert summary["redundant_context_calls_with_compound_intake"] == 3
    assert summary["runs_with_redundant_context_calls_with_compound_intake"] == 1


def test_task_completion_is_unobserved_when_required_side_effect_snapshot_is_missing():
    record = _record()
    del record["terminal_actions"]

    score = score_record(record, {**EXPECT, "forbidden_action_types": ["refund"]})

    assert score["task_completed"] is None


def test_tool_result_and_split_metrics_are_interview_ready():
    passed = _record(
        task_id="pass",
        attempt=1,
        split="development",
        error=None,
        tool_calls=[{"id": "1", "name": "create_after_sales_case", "completed": True, "error": None, "latency_ms": 40}],
        tool_count=1,
        latency_ms=100,
        token_usage=None,
    )
    failed = _record(
        task_id="fail",
        attempt=1,
        split="holdout",
        error=None,
        terminal_case=None,
        tool_calls=[{"id": "2", "name": "get_after_sales_order", "completed": True, "error": "ORDER_NOT_FOUND", "latency_ms": 60}],
        tool_count=1,
        latency_ms=200,
        token_usage=None,
    )
    for record in (passed, failed):
        record["score"] = score_record(record, EXPECT)

    summary = _summary([passed, failed], metadata={}, raw_name="runs.jsonl")

    assert summary["tool_results_observed"] == 2
    assert summary["tool_success_rate"] == 0.5
    assert summary["tool_latency_p50_ms"] == 50
    assert summary["task_completion_by_split"] == {
        "development": {"completed": 1, "observed": 1, "accuracy": 1.0},
        "holdout": {"completed": 0, "observed": 1, "accuracy": 0.0},
    }
    assert _tool_result_error('{"error":"ORDER_NOT_FOUND"}') == "ORDER_NOT_FOUND"
    assert _tool_result_error('{"status":"ok"}') is None


def test_workflow_hashes_use_repository_relative_skill_paths():
    paths = {path.replace("\\", "/") for path in _workflow_input_hashes()}

    assert "skills/public/after-sales-intake/SKILL.md" in paths
    assert "skills/public/after-sales-evidence/SKILL.md" in paths


def test_after_sales_routing_explicitly_activates_intake_without_changing_user_text():
    complaint = "我不确定是哪一个订单，那单没收到。"

    assert _route_agent_input(complaint, "after-sales") == f"/after-sales-intake\n{complaint}"
    assert _route_agent_input(complaint, "generic") == complaint
