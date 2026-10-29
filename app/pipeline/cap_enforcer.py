from dataclasses import dataclass
from typing import Optional
from app.config import settings

DAILY_TOTAL_SYSTEM_LIMIT = 1000
DAILY_FOLLOWUP_SYSTEM_LIMIT = 500


@dataclass(frozen=True)
class DailyCapAllocation:
    target: int
    ramp_target: int
    system_cap: int
    budget: int
    followup_sends: int
    new_sends: int
    total_sends: int


def calculate_daily_cap_allocation(
    followups_due: int,
    new_due: int,
    target: Optional[int] = None,
    ramp_target: Optional[int] = None,
) -> DailyCapAllocation:
    """
    Computes daily send allocation per VISION.md §7.4d:
    budget = min(target, ramp_target, 1000)
    followup_sends = min(followups_due, 500, budget)
    new_sends = min(new_due, budget - followup_sends)

    Since A7 is deferred in v1 (§18.3), target defaults to settings.target_daily_sends.
    ramp_target defaults to settings.ramp_target (the sum over active mailboxes).
    """
    effective_target = target if target is not None else settings.target_daily_sends
    effective_ramp = ramp_target if ramp_target is not None else settings.ramp_target

    # Non-negative guarantees
    followups_due = max(0, followups_due)
    new_due = max(0, new_due)
    effective_target = max(0, effective_target)
    effective_ramp = max(0, effective_ramp)

    # Core mathematical formula from VISION.md §7.4d
    budget = min(effective_target, effective_ramp, DAILY_TOTAL_SYSTEM_LIMIT)
    followup_sends = min(followups_due, DAILY_FOLLOWUP_SYSTEM_LIMIT, budget)
    new_sends = min(new_due, budget - followup_sends)
    total = followup_sends + new_sends

    return DailyCapAllocation(
        target=effective_target,
        ramp_target=effective_ramp,
        system_cap=DAILY_TOTAL_SYSTEM_LIMIT,
        budget=budget,
        followup_sends=followup_sends,
        new_sends=new_sends,
        total_sends=total,
    )


def check_mailbox_allowance(sends_today: int, allowance: int) -> bool:
    """
    Checks per-mailbox ceiling inside claiming transaction (VISION.md §7.4d, §7.5a):
    mailbox_sends_today(mailbox_id) < mailbox_allowance(mailbox_id)
    """
    return sends_today < allowance
