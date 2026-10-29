import pytest
from app.pipeline.cap_enforcer import (
    calculate_daily_cap_allocation,
    check_mailbox_allowance,
    DAILY_TOTAL_SYSTEM_LIMIT,
    DAILY_FOLLOWUP_SYSTEM_LIMIT,
)

def test_cap_enforcer_adheres_to_system_ceilings():
    # Large pool: 800 followups due, 800 new due, target=1500, ramp=1500
    alloc = calculate_daily_cap_allocation(
        followups_due=800,
        new_due=800,
        target=1500,
        ramp_target=1500,
    )
    assert alloc.budget == DAILY_TOTAL_SYSTEM_LIMIT  # 1000
    assert alloc.followup_sends == DAILY_FOLLOWUP_SYSTEM_LIMIT  # 500 (capped at 500)
    assert alloc.new_sends == 500  # remainder: 1000 - 500 = 500
    assert alloc.total_sends == 1000


def test_cap_enforcer_prioritizes_followups():
    # Followups get priority 1, new sends take remainder
    alloc = calculate_daily_cap_allocation(
        followups_due=350,
        new_due=500,
        target=600,
        ramp_target=600,
    )
    assert alloc.budget == 600
    assert alloc.followup_sends == 350
    assert alloc.new_sends == 250  # 600 - 350
    assert alloc.total_sends == 600


def test_cap_enforcer_ramp_target_binds_first():
    # Warmup ramp is 150 sends/day
    alloc = calculate_daily_cap_allocation(
        followups_due=100,
        new_due=200,
        target=1000,
        ramp_target=150,
    )
    assert alloc.budget == 150
    assert alloc.followup_sends == 100
    assert alloc.new_sends == 50  # 150 - 100
    assert alloc.total_sends == 150


def test_cap_enforcer_zero_cases():
    alloc = calculate_daily_cap_allocation(
        followups_due=0,
        new_due=50,
        target=100,
        ramp_target=100,
    )
    assert alloc.budget == 100
    assert alloc.followup_sends == 0
    assert alloc.new_sends == 50
    assert alloc.total_sends == 50


def test_mailbox_allowance():
    assert check_mailbox_allowance(sends_today=49, allowance=50) is True
    assert check_mailbox_allowance(sends_today=50, allowance=50) is False
    assert check_mailbox_allowance(sends_today=51, allowance=50) is False
