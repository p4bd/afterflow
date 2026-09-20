"""Balance re-check at approval time.

The execute-time balance check on the reservation is correct but not early
enough: between create_refund_action and approve_action, *another* action
on the same order could have reserved or released funds. We do not want to
approve a refund we already know will fail at execution — better to fail
fast at approve time and let the agent / reviewer see the conflict.

The check is intentionally side-effect-free: it only reads the executor's
view of available + reserved. The reservation itself is still performed
*inside* approve_action, AFTER this check passes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import ActionConflict


@dataclass
class BalanceCheckFailed(ActionConflict):
    """Available + reserved balance dropped below the requested refund amount."""

    available: int
    requested: int

    def __init__(self, available: int, requested: int) -> None:
        super().__init__(f"insufficient available balance: have {available}, need {requested}")
        self.available = available
        self.requested = requested


def check_balance_before_approve(executor, *, order_id: str, amount: int) -> None:
    """Raise BalanceCheckFailed if currently-available balance < amount.

    At approve-time the action will move `amount` from available to reserved.
    If `available` is already below `amount` because other actions on the
    same order have reserved the pool, this approval will fail at execute.
    Better to fail here, with a clear reason, than to queue a doomed
    approval that confuses the reviewer.
    """
    available = executor.balance_of(order_id)
    if available < amount:
        raise BalanceCheckFailed(available=available, requested=amount)
    return None
