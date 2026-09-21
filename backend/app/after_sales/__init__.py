"""AfterFlow after-sales domain module.

The ``*_orm`` imports at the bottom of the import block are eager on purpose
and must stay that way. They register three table definitions with
``Base.metadata``, and ``deerflow.persistence`` cannot do that itself: the
runtime package may not import ``app.*`` (see ``backend/AGENTS.md``
dependency direction), so the rows live here and the product package is what
publishes them.

Ordering matters. ``bootstrap_schema`` decides the empty-DB branch by running
``Base.metadata.create_all`` and then stamping head -- so a table absent from
``Base.metadata`` at that moment is never created and its migration never
runs, leaving a fresh database without it. Importing this package therefore
has to happen before the persistence engine bootstraps. It does, because
``app.gateway.routers`` is imported at ``app/gateway/app.py`` module level and
the lifespan bootstrap only runs later. Importing the ORM modules lazily
inside the store methods (where the rows are actually used) does not
substitute for this -- by then head is already stamped.

``tests/test_after_sales_lifespan_wiring.py`` pre-registers these same three
modules itself; that pre-registration is now redundant but harmless.
"""

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
from .idempotency_orm import IdempotencyRecordRow  # noqa: F401  (registers after_sales_idempotency)
from .knowledge_health import InMemoryKnowledgeHealthRecorder, KnowledgeHealth, KnowledgeHealthRecorder, SqlKnowledgeHealthRecorder, record_attempt
from .knowledge_health_orm import KnowledgeHealthRow  # noqa: F401  (registers after_sales_knowledge_health)
from .schemas import DecisionInput, DecisionResult
from .tool_audit import (
    AuditDecision,
    InMemoryToolCallAuditor,
    SqlToolCallAuditor,
    ToolCallAudit,
    ToolCallAuditor,
    fingerprint_params,
)
from .tool_audit_orm import ToolCallAuditRow  # noqa: F401  (registers after_sales_tool_audit)
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
