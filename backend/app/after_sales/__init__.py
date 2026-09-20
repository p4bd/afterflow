"""AfterFlow after-sales domain module."""

from .balance_check import BalanceCheckFailed, check_balance_before_approve
from .cancellation import CancellationRegistry, Cancelled, raise_if_cancelled
from .decision import decide_resolution
from .failure_taxonomy import Decision, FailureClass, classify_error, decision_for, is_safe_to_retry
from .idempotency import (
    IdempotencyConflict,
    IdempotencyHit,
    IdempotencyMiss,
    InMemoryIdempotencyStore,
    SqlIdempotencyStore,
    apply_idempotency,
)
from .knowledge_health import InMemoryKnowledgeHealthRecorder, KnowledgeHealth, KnowledgeHealthRecorder, SqlKnowledgeHealthRecorder, record_attempt
from .schemas import DecisionInput, DecisionResult
from .tool_audit import (
    AuditDecision,
    InMemoryToolCallAuditor,
    SqlToolCallAuditor,
    ToolCallAudit,
    ToolCallAuditor,
    fingerprint_params,
)
from .tool_policy import StepBudgetExceeded, ToolCallThrottle, ToolPolicy, ToolPolicyStore, fingerprint

__all__ = [
    "AuditDecision",
    "BalanceCheckFailed",
    "Cancelled",
    "CancellationRegistry",
    "Decision",
    "DecisionInput",
    "DecisionResult",
    "FailureClass",
    "IdempotencyConflict",
    "IdempotencyHit",
    "IdempotencyMiss",
    "InMemoryIdempotencyStore",
    "InMemoryKnowledgeHealthRecorder",
    "InMemoryToolCallAuditor",
    "KnowledgeHealth",
    "KnowledgeHealthRecorder",
    "SqlIdempotencyStore",
    "SqlKnowledgeHealthRecorder",
    "SqlToolCallAuditor",
    "StepBudgetExceeded",
    "ToolCallAudit",
    "ToolCallAuditor",
    "ToolCallThrottle",
    "ToolPolicy",
    "ToolPolicyStore",
    "apply_idempotency",
    "check_balance_before_approve",
    "classify_error",
    "decide_resolution",
    "decision_for",
    "fingerprint",
    "fingerprint_params",
    "is_safe_to_retry",
    "raise_if_cancelled",
    "record_attempt",
]
