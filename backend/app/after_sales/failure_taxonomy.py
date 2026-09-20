"""Failure taxonomy and recovery-policy decision table.

AfterFlow closes the loop that production agents often leave open: when a tool
call or domain operation fails, the agent and the UI need to *decide* what to
do next. There are only so many reasonable answers — retry, retry with
backoff, ask the user, ask a human, fall back to a deterministic alternative,
reject the request, or fail closed. Every distinct failure class is mapped
to exactly one of those answers by `decision_for()`.

The taxonomy is deliberately closed. New failure classes must be added by
hand and must declare a recovery decision at the same time. This is the
guard rail that prevents two failure modes that are common in agent code:

1. Silently swallowing unknown exceptions ("try/except Exception: continue")
2. Retrying things that must never be retried (e.g. a permission denial,
   which would otherwise become a privilege-escalation probe)

The mapping here is the single source of truth for both: HTTP error
classifiers, tool wrappers, and the agent loop all consult `decision_for()`.
"""

from __future__ import annotations

from enum import StrEnum


class FailureClass(StrEnum):
    """Closed set of failure classes AfterFlow recognises.

    Adding a new class is a deliberate action — you must also decide its
    recovery policy via `decision_for()`. See `tests/test_after_sales_failure_
    taxonomy.py::test_failure_class_is_closed_enum` for the contract test.
    """

    # Caller-supplied input was invalid (missing order_id, malformed payload).
    USER_ERROR = "user_error"
    # Domain rule rejected the request (inactive policy, exhausted balance).
    BUSINESS_RULE_REJECTION = "business_rule_rejection"
    # Authn / authz / role denied.
    PERMISSION_ERROR = "permission_error"
    # State machine / status forbids the operation.
    INVALID_STATE = "invalid_state"
    # Optimistic-lock / TOCTOU conflict.
    VERSION_CONFLICT = "version_conflict"
    # Model returned garbage (bad schema, refused, hallucinated, empty).
    MODEL_ERROR = "model_error"
    # External tool returned a 5xx or refused temporarily; retry may help.
    TOOL_ERROR_TRANSIENT = "tool_error_transient"
    # External tool returned 4xx / validation; retrying will not change it.
    TOOL_ERROR_PERMANENT = "tool_error_permanent"
    # Caller / external took longer than budget.
    TIMEOUT = "timeout"
    # Inform-layer dependency (RAG / MCP / search) is unavailable. The
    # deterministic engine must still decide money — fall back to a mock.
    EXTERNAL_DEPENDENCY_UNAVAILABLE = "external_dependency_unavailable"
    # Unclassified: an internal bug until proven otherwise.
    INTERNAL_BUG = "internal_bug"


class Decision(StrEnum):
    """Recovery action the caller / UI / agent should take."""

    RETRY = "retry"  # try again immediately, no delay
    RETRY_WITH_BACKOFF = "retry_with_backoff"  # exponential backoff with jitter
    ASK_USER = "ask_user"  # request more / corrected input
    HUMAN_REVIEW = "human_review"  # escalate to the approval desk
    FALLBACK = "fallback"  # use a deterministic mock / degraded mode
    REJECT = "reject"  # refuse the request; do not run any side effect
    FAIL = "fail"  # surface the error; no retry, no fallback
    FAIL_CLOSED = "fail_closed"  # deny any side effect; do not even retry read


# The recovery-policy table. The ONLY place where a failure class is bound
# to a recovery decision. If you add a class above, you must add a row here.
_DECISION_TABLE: dict[FailureClass, Decision] = {
    FailureClass.USER_ERROR: Decision.ASK_USER,
    FailureClass.BUSINESS_RULE_REJECTION: Decision.REJECT,
    FailureClass.PERMISSION_ERROR: Decision.FAIL_CLOSED,
    FailureClass.INVALID_STATE: Decision.FAIL,
    FailureClass.VERSION_CONFLICT: Decision.FAIL,
    FailureClass.MODEL_ERROR: Decision.HUMAN_REVIEW,
    FailureClass.TOOL_ERROR_TRANSIENT: Decision.RETRY,
    FailureClass.TOOL_ERROR_PERMANENT: Decision.REJECT,
    FailureClass.TIMEOUT: Decision.RETRY_WITH_BACKOFF,
    FailureClass.EXTERNAL_DEPENDENCY_UNAVAILABLE: Decision.FALLBACK,
    FailureClass.INTERNAL_BUG: Decision.HUMAN_REVIEW,
}


def decision_for(failure_class: FailureClass) -> Decision:
    """Return the recovery decision for a failure class."""
    return _DECISION_TABLE[failure_class]


# Failure classes for which a retry is a safe, idempotency-respecting action.
# Everything else must NOT be retried automatically by a wrapper, an HTTP
# middleware, or the agent loop.
_RETRYABLE: frozenset[FailureClass] = frozenset(
    {
        FailureClass.TIMEOUT,
        FailureClass.TOOL_ERROR_TRANSIENT,
        FailureClass.EXTERNAL_DEPENDENCY_UNAVAILABLE,
    }
)


def is_safe_to_retry(failure_class: FailureClass) -> bool:
    """Whether a wrapper / retry decorator may auto-retry this class.

    Retrying anything outside this set is either futile (permanent errors) or
    dangerous (permission probes, version conflicts, business rejections).
    """
    return failure_class in _RETRYABLE


# --- Error → class classifier ------------------------------------------------
#
# The classifier deliberately errs on the conservative side: anything we do
# not recognise is treated as INTERNAL_BUG (not silently swallowed). Internal
# exceptions that we *do* recognise are mapped via isinstance() below.


def classify_error(error: BaseException) -> FailureClass:
    """Map a Python exception to a FailureClass. Conservative by default."""
    # Imported here to avoid a circular dependency at module load.
    from .actions import ActionConflict, ActionForbidden
    from .repository import ConcurrentActionError

    if isinstance(error, ActionForbidden):
        return FailureClass.PERMISSION_ERROR
    if isinstance(error, ConcurrentActionError):
        return FailureClass.VERSION_CONFLICT
    if isinstance(error, ActionConflict):
        return FailureClass.INVALID_STATE
    if isinstance(error, PermissionError):
        return FailureClass.PERMISSION_ERROR
    if isinstance(error, TimeoutError):
        return FailureClass.TIMEOUT
    if isinstance(error, ValueError):
        return FailureClass.BUSINESS_RULE_REJECTION
    if isinstance(error, KeyError):
        # A missing record is a caller error — they referenced something that
        # doesn't exist. Not a transient failure; not retryable.
        return FailureClass.USER_ERROR
    if isinstance(error, ConnectionError):
        return FailureClass.EXTERNAL_DEPENDENCY_UNAVAILABLE
    if isinstance(error, NotImplementedError):
        return FailureClass.INTERNAL_BUG
    # Anything else: internal bug. Do NOT silently swallow — surface it.
    return FailureClass.INTERNAL_BUG
