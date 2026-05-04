"""Tests for workspace.devices.recovery.AutoRecover.

Drives the loop with deterministic recover_fns (queues of bool results)
and asserts on attempt count, status messages, and trigger/stop semantics.
Uses tight delays so the suite stays fast — the helper's behavior is
identical at any scale of delays.
"""

from __future__ import annotations

import threading
import time

from workspace.devices.recovery import AutoRecover


def _make(*, recover_results, **kwargs):
    """Helper: build an AutoRecover that returns each result in order then
    stays at the last value. Records every (state, msg) the helper publishes."""
    statuses: list[tuple[str, str]] = []
    results = list(recover_results)
    call_count = {"n": 0}

    def recover_fn() -> bool:
        call_count["n"] += 1
        if results:
            return results.pop(0)
        return False

    def set_status(state: str, msg: str) -> None:
        statuses.append((state, msg))

    defaults = dict(
        initial_delay=0.05,
        max_delay=0.2,
        backoff_factor=2.0,
        flag_after=2,
        log_label="test-device",
    )
    defaults.update(kwargs)
    helper = AutoRecover(
        recover_fn=recover_fn,
        set_status=set_status,
        **defaults,
    )
    return helper, statuses, call_count


def test_first_attempt_succeeds_no_loop():
    helper, statuses, calls = _make(recover_results=[True])
    helper.trigger()
    # First attempt is synchronous-ish; give the thread a tick.
    time.sleep(0.05)
    assert calls["n"] == 1
    # set_status called once with "recovering"
    assert statuses[0] == ("recovering", "recovering (attempt 1)")
    # Loop exits after success — no further status updates from the helper
    # (recover_fn is responsible for setting state=ok).
    time.sleep(0.1)
    assert calls["n"] == 1


def test_backoff_then_success():
    helper, statuses, calls = _make(recover_results=[False, False, True])
    helper.trigger()
    time.sleep(0.5)  # plenty of time for 0.05 + 0.1 + success
    assert calls["n"] == 3
    # Three "recovering (attempt N)" messages
    assert statuses[0][1] == "recovering (attempt 1)"
    assert statuses[1][1] == "recovering (attempt 2)"


def test_msg_changes_after_flag_after_threshold():
    helper, statuses, calls = _make(
        recover_results=[False] * 5 + [True],
        flag_after=2,
    )
    helper.trigger()
    time.sleep(2.0)  # enough time at capped delay 0.2 for ~6 attempts
    helper.stop()
    msgs = [m for _, m in statuses]
    # First two attempts are "recovering (attempt N)"
    assert "recovering (attempt 1)" in msgs
    assert "recovering (attempt 2)" in msgs
    # Then the flag kicks in
    assert any("attempts failed" in m for m in msgs)


def test_stop_terminates_loop():
    helper, statuses, calls = _make(recover_results=[False] * 100)
    helper.trigger()
    time.sleep(0.1)
    helper.stop()
    n_at_stop = calls["n"]
    time.sleep(0.5)
    # No more attempts after stop()
    assert calls["n"] == n_at_stop
    assert not helper.running


def test_trigger_while_running_resets_backoff():
    helper, statuses, calls = _make(
        recover_results=[False] * 100,
        initial_delay=2.0,        # long delay so backoff dominates
        max_delay=2.0,
    )
    helper.trigger()
    time.sleep(0.1)              # let attempt 1 fail and start sleeping
    n_before = calls["n"]
    helper.trigger()             # nudge — should wake the sleep early
    time.sleep(0.1)
    helper.stop()
    # Without the nudge, only 1 attempt would have happened.
    assert calls["n"] > n_before


def test_recover_fn_exception_treated_as_failure():
    statuses: list[tuple[str, str]] = []
    raised = {"n": 0}

    def bad_recover():
        raised["n"] += 1
        raise RuntimeError("boom")

    helper = AutoRecover(
        recover_fn=bad_recover,
        set_status=lambda s, m: statuses.append((s, m)),
        initial_delay=0.05,
        max_delay=0.1,
        log_label="boom-device",
    )
    helper.trigger()
    time.sleep(0.3)
    helper.stop()
    # Multiple attempts despite raises — exceptions don't kill the loop.
    assert raised["n"] >= 2


def test_set_status_exception_does_not_kill_loop():
    calls = {"n": 0}

    def recover_fn():
        calls["n"] += 1
        return calls["n"] >= 3

    def bad_set_status(state, msg):
        raise RuntimeError("ui broke")

    helper = AutoRecover(
        recover_fn=recover_fn,
        set_status=bad_set_status,
        initial_delay=0.05,
        max_delay=0.1,
        log_label="ui-broken",
    )
    helper.trigger()
    time.sleep(0.5)
    assert calls["n"] >= 3      # loop kept running through set_status raises
