# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import tempfile
import time

import pytest

from tests.testhelpers import cleanup_processes

from tests.simple_mock_scheduler import build_assign_action
from tests.worker_test_helpers import (
    create_mock_scheduler,
    start_worker as _start_worker,
    clear_state as _clear_state,
    enqueue_session as _enqueue_session_full,
    get_raw_sync_log as _get_raw_sync_log,
    get_results as _get_results,
    poll_until as _poll_until,
)

_MOCK_SDKS_EXECUTORS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "mock_sdks", "executors")
)
_NODE_OPTIONS = "--preserve-symlinks --preserve-symlinks-main"


def _sdk_env(**scenario):
    env = {
        "AGENTBEACON_EXECUTORS_DIR": _MOCK_SDKS_EXECUTORS,
        "NODE_OPTIONS": _NODE_OPTIONS,
    }
    env.update(scenario)
    return env


def _require_executors_built():
    assert os.path.exists(os.path.join(_MOCK_SDKS_EXECUTORS, "claude-executor.js")), (
        "mock_sdks/executors not built -- run 'make mock-sdks' first"
    )


def _report_state(entry):
    rep = entry.get("executor_report")
    return rep.get("executor_state") if rep else None


def _has_result(entry):
    return entry.get("turn_result") is not None


def _normalized_tokens(raw_log):
    tokens = []
    for entry in raw_log:
        state = _report_state(entry)
        if state is None:
            continue
        token = (state, _has_result(entry))
        if token[1]:
            tokens.append(token)
        elif not tokens or tokens[-1] != token:
            tokens.append(token)
    return tokens


def _idle_to_running_count(tokens):
    states = [t[0] for t in tokens]
    return sum(1 for a, b in zip(states, states[1:]) if a == "idle" and b == "running")


def _contains_subsequence(tokens, wanted):
    it = iter(tokens)
    return all(any(t == w for t in it) for w in wanted)


def _result_text(result):
    out = []
    for msg in result.get("turnMessages") or []:
        if isinstance(msg, dict):
            for part in msg.get("parts", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    out.append(part["text"])
    return " ".join(out)


@pytest.fixture()
def mock_scheduler():
    scheduler_url, port, proc, pm = create_mock_scheduler()
    yield scheduler_url
    cleanup_processes([proc])
    pm.release_port(port)


def test_mock_scheduler_sdk_assign_shape():
    sdk_task = {
        "driver": {"platform": "claude_sdk", "config": {"fs_level": "unrestricted"}},
        "agent_config": {"model": "claude-haiku-4-5"},
        "message": {"role": "ROLE_USER", "parts": [{"text": "hi"}]},
    }
    sdk_assign = build_assign_action(
        {"sessionId": "s1", "executionId": "e1", "taskPayload": sdk_task}
    ).model_dump()
    assert sdk_assign["driver"]["platform"] == "claude_sdk"
    assert sdk_assign["driver"]["config"] == {"fs_level": "unrestricted"}
    assert sdk_assign["agent_config"] == {"model": "claude-haiku-4-5"}

    acp_task = {
        "driver": {"platform": "acp", "config": {}},
        "agent_config": {"command": "uv", "args": ["run"], "timeout": 30},
        "message": {"role": "ROLE_USER", "parts": [{"text": "hi"}]},
    }
    acp_assign = build_assign_action(
        {"sessionId": "s2", "executionId": "e1", "taskPayload": acp_task}
    ).model_dump()
    assert acp_assign["driver"]["platform"] == "acp"
    assert acp_assign["driver"]["config"] == {
        "command": "uv",
        "args": ["run"],
        "timeout": 30,
    }
    assert "agent_config" not in acp_assign


def test_worker_reports_running_on_gated_auto_resume(mock_scheduler):
    _require_executors_built()

    scheduler_url = mock_scheduler
    _clear_state(scheduler_url)

    gate_dir = tempfile.mkdtemp(prefix="autoresume-gate-")
    gate_file = os.path.join(gate_dir, "release")

    _enqueue_session_full(scheduler_url, platform="claude_sdk")
    worker = _start_worker(
        scheduler_url,
        extra_env=_sdk_env(
            AGENTBEACON_MOCK_SDK_AUTORESUME="1",
            AGENTBEACON_MOCK_SDK_AUTORESUME_GATE_FILE=gate_file,
        ),
    )
    try:
        assert _poll_until(
            lambda: any(
                t == ("idle", True)
                for t in _normalized_tokens(_get_raw_sync_log(scheduler_url))
            ),
            timeout=30,
        ), "Worker did not settle the first turn"

        def _running_after_idle():
            tokens = _normalized_tokens(_get_raw_sync_log(scheduler_url))
            return _idle_to_running_count(tokens) >= 1

        assert _poll_until(_running_after_idle, timeout=12), (
            "no idle->running transition observed while the post-init gate was held; "
            f"tokens={_normalized_tokens(_get_raw_sync_log(scheduler_url))}"
        )

        open(gate_file, "w").close()

        def _both_turns_settled():
            idle_results = sum(
                1
                for t in _normalized_tokens(_get_raw_sync_log(scheduler_url))
                if t == ("idle", True)
            )
            return idle_results >= 2 and len(_get_results(scheduler_url)) >= 2

        assert _poll_until(_both_turns_settled, timeout=30), (
            "Resume turn did not settle"
        )

        tokens = _normalized_tokens(_get_raw_sync_log(scheduler_url))
        assert [t for t in tokens if t[1]] == [("idle", True), ("idle", True)], (
            f"expected exactly two idle-with-result reports, got tokens={tokens}"
        )
        results = _get_results(scheduler_url)
        assert "initial turn output" in _result_text(results[0]), results[0]
        assert "resumed turn output" in _result_text(results[1]), results[1]
        assert _idle_to_running_count(tokens) >= 1, (
            f"expected at least one idle->running transition, got tokens={tokens}"
        )
        assert _contains_subsequence(
            tokens, [("idle", True), ("running", False), ("idle", True)]
        ), f"missing idle->running->idle subsequence: tokens={tokens}"
    finally:
        cleanup_processes([worker])


def test_worker_ungated_auto_resume_reports_both_results(mock_scheduler):
    _require_executors_built()

    scheduler_url = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session_full(scheduler_url, platform="claude_sdk")
    worker = _start_worker(
        scheduler_url,
        extra_env=_sdk_env(AGENTBEACON_MOCK_SDK_AUTORESUME="1"),
    )
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) >= 2, timeout=30), (
            "Worker did not report both turn results"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 2, f"expected 2 results, got {len(results)}"
        assert "initial turn output" in _result_text(results[0]), results[0]
        assert "resumed turn output" in _result_text(results[1]), results[1]

        tokens = _normalized_tokens(_get_raw_sync_log(scheduler_url))
        assert [t for t in tokens if t[1]] == [("idle", True), ("idle", True)], (
            f"expected exactly two idle-with-result reports, got tokens={tokens}"
        )
    finally:
        cleanup_processes([worker])


def test_worker_inactivity_watchdog_catches_stalled_auto_resume(mock_scheduler):
    _require_executors_built()

    scheduler_url = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session_full(scheduler_url, platform="claude_sdk")
    worker = _start_worker(
        scheduler_url,
        extra_env=_sdk_env(
            AGENTBEACON_MOCK_SDK_AUTORESUME="1",
            AGENTBEACON_MOCK_SDK_AUTORESUME_HANG="1",
        ),
        inactivity_timeout="3s",
    )
    try:

        def _stall_reported():
            for entry in _get_raw_sync_log(scheduler_url):
                rep = entry.get("executor_report")
                tr = entry.get("turn_result")
                if (
                    rep
                    and rep.get("executor_state") == "crashed"
                    and tr
                    and tr.get("error_kind") == "executor_failed"
                    and "executor stalled: no output for" in (tr.get("error") or "")
                ):
                    return True
            return False

        assert _poll_until(_stall_reported, timeout=20), (
            "Inactivity watchdog did not catch the stalled auto-resume turn"
        )

        tokens = _normalized_tokens(_get_raw_sync_log(scheduler_url))
        assert _contains_subsequence(tokens, [("idle", True), ("running", False)]), (
            "stall not preceded by first-turn idle + post-init running "
            f"(second init not reached): tokens={tokens}"
        )
    finally:
        cleanup_processes([worker])
        time.sleep(0.5)
