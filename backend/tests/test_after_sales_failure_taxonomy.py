"""Failure taxonomy and decision-policy tests for AfterFlow.

The single source of truth for: which failure classes exist, how each maps to
an actionable recovery decision, and which classes must NEVER be silently
swallowed. These tests guard the contract that tool/exception boundaries
advertise their failure class — agents and HTTP clients must be able to
distinguish "retry me" from "ask the human" from "fail closed, do not retry".
"""

from __future__ import annotations

import pytest

from app.after_sales.failure_taxonomy import (
    Decision,
    FailureClass,
    classify_error,
    decision_for,
    is_safe_to_retry,
)


class TestFailureClass:
    def test_failure_class_is_closed_enum(self) -> None:
        # If someone adds a new class they must consciously decide its recovery
        # decision — the taxonomy is closed on purpose (see ADR docs).
        assert {c.value for c in FailureClass} == {
            "user_error",
            "business_rule_rejection",
            "permission_error",
            "invalid_state",
            "version_conflict",
            "model_error",
            "tool_error_transient",
            "tool_error_permanent",
            "timeout",
            "external_dependency_unavailable",
            "internal_bug",
        }

    @pytest.mark.parametrize(
        "retryable",
        [
            FailureClass.TIMEOUT,
            FailureClass.TOOL_ERROR_TRANSIENT,
            FailureClass.EXTERNAL_DEPENDENCY_UNAVAILABLE,
        ],
    )
    def test_retryable_classes(self, retryable: FailureClass) -> None:
        assert is_safe_to_retry(retryable) is True

    @pytest.mark.parametrize(
        "non_retryable",
        [
            FailureClass.USER_ERROR,
            FailureClass.BUSINESS_RULE_REJECTION,
            FailureClass.PERMISSION_ERROR,
            FailureClass.INVALID_STATE,
            FailureClass.VERSION_CONFLICT,
            FailureClass.MODEL_ERROR,
            FailureClass.TOOL_ERROR_PERMANENT,
            FailureClass.INTERNAL_BUG,
        ],
    )
    def test_non_retryable_classes(self, non_retryable: FailureClass) -> None:
        assert is_safe_to_retry(non_retryable) is False


class TestDecisionMapping:
    def test_user_error_asks_user(self) -> None:
        assert decision_for(FailureClass.USER_ERROR) == Decision.ASK_USER

    def test_business_rule_rejection_rejects(self) -> None:
        assert decision_for(FailureClass.BUSINESS_RULE_REJECTION) == Decision.REJECT

    def test_permission_error_fails_closed(self) -> None:
        # Permission denials must NEVER retry — that would be a privilege probe.
        assert decision_for(FailureClass.PERMISSION_ERROR) == Decision.FAIL_CLOSED

    def test_version_conflict_fails(self) -> None:
        # Stale-version retries cause double-action; must fail loudly.
        assert decision_for(FailureClass.VERSION_CONFLICT) == Decision.FAIL

    def test_invalid_state_fails(self) -> None:
        assert decision_for(FailureClass.INVALID_STATE) == Decision.FAIL

    def test_model_error_needs_human_review(self) -> None:
        # The model gave us garbage — do not blindly retry, do not auto-fallback.
        assert decision_for(FailureClass.MODEL_ERROR) == Decision.HUMAN_REVIEW

    def test_transient_tool_error_retries(self) -> None:
        assert decision_for(FailureClass.TOOL_ERROR_TRANSIENT) == Decision.RETRY

    def test_permanent_tool_error_rejects(self) -> None:
        assert decision_for(FailureClass.TOOL_ERROR_PERMANENT) == Decision.REJECT

    def test_timeout_retries_with_backoff(self) -> None:
        assert decision_for(FailureClass.TIMEOUT) == Decision.RETRY_WITH_BACKOFF

    def test_external_dependency_unavailable_falls_back(self) -> None:
        # The INFORM layer (RAG/MCP) being down must NOT block money decisions.
        assert decision_for(FailureClass.EXTERNAL_DEPENDENCY_UNAVAILABLE) == Decision.FALLBACK

    def test_internal_bug_needs_human_review(self) -> None:
        assert decision_for(FailureClass.INTERNAL_BUG) == Decision.HUMAN_REVIEW


class TestClassifyError:
    def test_classify_permission_error(self) -> None:
        assert classify_error(PermissionError("nope")) is FailureClass.PERMISSION_ERROR

    def test_classify_value_error(self) -> None:
        # ValueError is "bad input from caller / bad invariant" → user/business.
        assert classify_error(ValueError("bad amount")) is FailureClass.BUSINESS_RULE_REJECTION

    def test_classify_timeout(self) -> None:
        assert classify_error(TimeoutError("bank slow")) is FailureClass.TIMEOUT

    def test_classify_concurrent_error_to_version_conflict(self) -> None:
        from app.after_sales.repository import ConcurrentActionError

        assert classify_error(ConcurrentActionError("stale version")) is FailureClass.VERSION_CONFLICT

    def test_classify_action_conflict_to_invalid_state(self) -> None:
        from app.after_sales.actions import ActionConflict

        assert classify_error(ActionConflict("not pending")) is FailureClass.INVALID_STATE

    def test_classify_unknown_to_internal_bug(self) -> None:
        # An uncaught exception is an internal bug until proven otherwise —
        # we surface it for human review rather than swallow it.
        assert classify_error(RuntimeError("wtf")) is FailureClass.INTERNAL_BUG
