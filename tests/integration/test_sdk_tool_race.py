# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import subprocess
import threading

MOCK_EXECUTORS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "mock_sdks", "executors"
)
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")

ID_SUCCESS = "toolu_mock_race_001"
ID_ERROR = "toolu_mock_race_err_001"
ID_PAR_A = "toolu_mock_race_par_a"
ID_PAR_B = "toolu_mock_race_par_b"
EXPECTED_IDS = {ID_SUCCESS, ID_ERROR, ID_PAR_A, ID_PAR_B}
FINAL_TEXT = "Both commands attempted."


def _spawn_claude_executor(extra_env: dict[str, str]) -> subprocess.Popen:
    script = os.path.join(MOCK_EXECUTORS_DIR, "claude-executor.js")
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
    env.update(extra_env)
    return subprocess.Popen(
        [NODE_PATH, script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _send_command(proc: subprocess.Popen, cmd: dict) -> None:
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()


def _collect_events(proc: subprocess.Popen, timeout: int = 30) -> list[dict]:
    events: list[dict] = []
    done = threading.Event()

    def reader() -> None:
        while True:
            line = proc.stdout.readline()
            if not line:
                done.set()
                return
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") in ("result", "error"):
                done.set()
                return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    completed = done.wait(timeout=timeout)
    if not completed:
        proc.terminate()
        raise AssertionError(
            f"executor did not emit a terminal result|error event within "
            f"{timeout}s; collected {len(events)} events: {events}"
        )
    return events


def _run_scenario(env_var: str) -> list[dict]:
    proc = _spawn_claude_executor({env_var: "1"})
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "trigger scenario"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})
        return events
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _persisted_messages(events: list[dict]) -> list[dict]:
    return [
        e
        for e in events
        if e.get("type") == "message" and e.get("ephemeral") is not True
    ]


def _blocks(msg: dict) -> list[dict]:
    return [b for b in (msg.get("content") or []) if isinstance(b, dict)]


def _first_index_maps(msgs: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    use_idx: dict[str, int] = {}
    result_idx: dict[str, int] = {}
    for i, m in enumerate(msgs):
        for b in _blocks(m):
            if b.get("type") == "tool_use":
                tid = b.get("id")
                if isinstance(tid, str) and tid not in use_idx:
                    use_idx[tid] = i
            elif b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if isinstance(tid, str) and tid not in result_idx:
                    result_idx[tid] = i
    return use_idx, result_idx


def test_tool_use_persisted_before_tool_result():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_TOOL_RACE")
    msgs = _persisted_messages(events)
    use_idx, result_idx = _first_index_maps(msgs)

    assert ID_SUCCESS in use_idx, f"tool_use {ID_SUCCESS} not emitted: {msgs}"
    assert ID_SUCCESS in result_idx, f"tool_result {ID_SUCCESS} not emitted: {msgs}"
    assert use_idx[ID_SUCCESS] < result_idx[ID_SUCCESS]


def test_no_inverted_tool_pairs():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_TOOL_RACE")
    msgs = _persisted_messages(events)
    use_idx, result_idx = _first_index_maps(msgs)

    assert EXPECTED_IDS <= use_idx.keys(), (
        f"missing tool_use ids: {EXPECTED_IDS - use_idx.keys()}"
    )
    assert EXPECTED_IDS <= result_idx.keys(), (
        f"missing tool_result ids: {EXPECTED_IDS - result_idx.keys()}"
    )

    common = use_idx.keys() & result_idx.keys()
    inverted = [tid for tid in common if result_idx[tid] < use_idx[tid]]
    assert inverted == [], f"inverted tool pairs found: {inverted}"
    for tid in common:
        assert use_idx[tid] < result_idx[tid]


def test_single_final_assistant_and_result_backfill():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_TOOL_RACE")
    msgs = _persisted_messages(events)

    use_count = sum(
        1
        for m in msgs
        if any(
            b.get("type") == "tool_use" and b.get("id") == ID_SUCCESS
            for b in _blocks(m)
        )
    )
    assert use_count == 1

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    result = result_events[0]
    assert result.get("subtype") == "success"
    assert result.get("result") == FINAL_TEXT


def test_error_tool_result_ordering():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_TOOL_RACE")
    msgs = _persisted_messages(events)
    use_idx, result_idx = _first_index_maps(msgs)

    assert ID_ERROR in use_idx
    assert ID_ERROR in result_idx
    assert use_idx[ID_ERROR] < result_idx[ID_ERROR]

    err_block = next(
        b
        for m in msgs
        for b in _blocks(m)
        if b.get("type") == "tool_result" and b.get("tool_use_id") == ID_ERROR
    )
    assert err_block.get("is_error") is True


def test_parallel_results_both_after_their_uses():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_TOOL_RACE")
    msgs = _persisted_messages(events)
    use_idx, result_idx = _first_index_maps(msgs)

    for tid in (ID_PAR_A, ID_PAR_B):
        assert tid in use_idx, f"missing parallel tool_use {tid}"
        assert tid in result_idx, f"missing parallel tool_result {tid}"

    assert max(use_idx[ID_PAR_A], use_idx[ID_PAR_B]) < min(
        result_idx[ID_PAR_A], result_idx[ID_PAR_B]
    )


def test_nontool_user_message_does_not_flush_early():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_USER_NOFLUSH")
    msgs = _persisted_messages(events)

    marker_idx = -1
    buffered_idx = -1
    buffered_count = 0
    for i, m in enumerate(msgs):
        blocks = _blocks(m)
        if any(b.get("type") == "compaction" for b in blocks):
            if marker_idx == -1:
                marker_idx = i
        if any(
            b.get("type") == "text" and b.get("text") == "BUFFERED_GUARD"
            for b in blocks
        ):
            buffered_count += 1
            if buffered_idx == -1:
                buffered_idx = i

    assert marker_idx != -1, f"compaction marker not emitted: {msgs}"
    assert buffered_idx != -1, f"buffered text not emitted: {msgs}"
    assert buffered_idx > marker_idx
    assert buffered_count == 1
