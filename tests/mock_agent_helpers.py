# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List

import httpx

import uuid

from tests.testhelpers import (
    PortManager,
    _ensure_driver,
    db_conn,
)


def seed_codex_test_agent(
    db_url: str,
    name: str = "codex-mock",
    agent_id: str = None,
    config_extra: Dict = None,
) -> str:
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    config_dict = {
        "command": "uv",
        "args": [
            "run",
            "python",
            "-m",
            "agentbeacon.mock_agent",
            "--mode",
            "codex",
        ],
        "timeout": 30,
    }
    if config_extra:
        config_dict.update(config_extra)
    config = json.dumps(config_dict)

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, "codex_sdk")
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled) "
            "VALUES (?, ?, 'codex_sdk', ?, ?, ?)",
            (agent_id, name, driver_id, config, True),
        )
        conn.commit()

    return agent_id


def get_session_row(db_url: str, session_id: str) -> dict:
    with db_conn(db_url) as conn:
        cur = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        assert row is not None, f"session {session_id} not found"
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))


def post_worker_sync(
    url: str,
    worker_id: str,
    executor_report: dict = None,
    turn_result: dict = None,
    command_ack: str = None,
    timeout: int = 10,
) -> dict:
    import httpx

    body = {"worker_id": worker_id}
    if executor_report is not None:
        body["executor_report"] = executor_report
    if turn_result is not None:
        body["turn_result"] = turn_result
    if command_ack is not None:
        body["command_ack"] = command_ack

    resp = httpx.post(f"{url}/api/worker/sync", json=body, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def create_child_session_raw(
    db_url: str,
    parent_id: str,
    exec_id: str,
    agent_id: str,
    desired: str = "run",
    executor_state: str = "unassigned",
    outcome: str = None,
    worker_id: str = None,
) -> str:
    import uuid

    child_id = str(uuid.uuid4())
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, desired, executor_state, outcome, worker_id, sandbox_policy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                child_id,
                exec_id,
                parent_id,
                agent_id,
                desired,
                executor_state,
                outcome,
                worker_id,
                '{"fs_level":"unrestricted"}',
            ),
        )
        conn.commit()
    return child_id


def make_executor_report(
    session_id: str,
    executor_state: str,
    agent_session_id: str = None,
) -> dict:
    report = {"session_id": session_id, "executor_state": executor_state}
    if agent_session_id is not None:
        report["agent_session_id"] = agent_session_id
    return report


def make_agent_message(text: str) -> dict:
    return {"role": "ROLE_AGENT", "parts": [{"text": text}]}


def make_turn_result(
    session_id: str,
    messages: list = None,
    error: str = None,
    error_kind: str = None,
) -> dict:
    result = {
        "session_id": session_id,
        "messages": messages if messages is not None else [],
    }
    if error is not None:
        result["error"] = error
    if error_kind is not None:
        result["error_kind"] = error_kind
    return result


def set_session_fields(db_url: str, session_id: str, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [session_id]
    with db_conn(db_url) as conn:
        conn.execute(
            f"UPDATE sessions SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()


def set_execution_fields(db_url: str, execution_id: str, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [execution_id]
    with db_conn(db_url) as conn:
        conn.execute(
            f"UPDATE executions SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()


def set_config(db_url: str, name: str, value: str) -> None:
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO config (name, value) VALUES (?, ?) "
            "ON CONFLICT (name) DO UPDATE SET value = excluded.value",
            (name, value),
        )
        conn.commit()


def get_events(
    db_url: str, session_id: str = None, event_type: str = None
) -> list[dict]:
    clauses = []
    params = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with db_conn(db_url) as conn:
        cur = conn.execute(f"SELECT * FROM events{where} ORDER BY id ASC", params)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_tasks(db_url: str, session_id: str) -> list[dict]:
    with db_conn(db_url) as conn:
        cur = conn.execute(
            "SELECT * FROM task_queue WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_task_queue_count(db_url: str, session_id: str) -> int:
    with db_conn(db_url) as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        )
        return cur.fetchone()[0]


def get_task_queue_payload(db_url: str, session_id: str) -> dict | None:
    with db_conn(db_url) as conn:
        cur = conn.execute(
            "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id ASC LIMIT 1",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])


def get_task_queue_entry(db_url: str, session_id: str) -> tuple | None:
    with db_conn(db_url) as conn:
        cur = conn.execute(
            "SELECT task_payload, source FROM task_queue WHERE session_id = ? ORDER BY id ASC LIMIT 1",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]


def insert_task(
    db_url: str,
    execution_id: str,
    session_id: str,
    text: str,
    source: str = None,
) -> None:
    message = {
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    payload = json.dumps({"message": message})
    with db_conn(db_url) as conn:
        if source is not None:
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
                (execution_id, session_id, payload, source),
            )
        else:
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
                (execution_id, session_id, payload),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_session_state(db_url: str, session_id: str, **expected) -> None:
    row = get_session_row(db_url, session_id)
    for key, value in expected.items():
        assert row[key] == value, (
            f"session {session_id}: expected {key}={value!r}, got {row[key]!r}"
        )


def assert_execution_state(db_url: str, execution_id: str, **expected) -> None:
    row = get_execution_row(db_url, execution_id)
    for key, value in expected.items():
        assert row[key] == value, (
            f"execution {execution_id}: expected {key}={value!r}, got {row[key]!r}"
        )


def assert_command_pending(
    db_url: str,
    session_id: str,
    command_type: str,
    has_payload: bool = None,
) -> None:
    row = get_session_row(db_url, session_id)
    assert row["command_token"] is not None, (
        f"session {session_id}: expected command_token to be set, got NULL"
    )
    assert row["command_type"] == command_type, (
        f"session {session_id}: expected command_type={command_type!r}, got {row['command_type']!r}"
    )
    if has_payload is not None:
        assert row["command_has_payload"] == has_payload, (
            f"session {session_id}: expected command_has_payload={has_payload!r}, got {row['command_has_payload']!r}"
        )


def assert_no_command(db_url: str, session_id: str) -> None:
    row = get_session_row(db_url, session_id)
    assert row["command_token"] is None, (
        f"session {session_id}: expected command_token to be NULL, got {row['command_token']!r}"
    )


def start_mock_agent_a2a(
    port: int = None, base_dir: Path = None
) -> tuple[subprocess.Popen, int]:
    if port is None:
        port = PortManager().allocate_port()
    if base_dir is None:
        base_dir = Path.cwd()

    agent_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "mock-agent",
            "--mode",
            "a2a",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
    )
    return agent_proc, port


def start_mock_scheduler(port: int, base_dir: Path = None) -> subprocess.Popen:
    if base_dir is None:
        base_dir = Path.cwd()

    mock_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "tests.simple_mock_scheduler:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
    )
    return mock_proc


def parse_agent_log(test_name: str) -> List[Dict]:
    from agentbeacon.mock_agent.file_logger import parse_agent_entry

    log_file = Path(f"logs/{test_name}.log")

    if not log_file.exists():
        return []

    try:
        content = log_file.read_text()
        if not content.strip():
            return []

        entries = []
        for line in content.strip().split("\n"):
            parsed = parse_agent_entry(line.strip())
            entries.append(parsed)

        return entries

    except Exception:
        return []


def seed_acp_mock_agent(
    db_url: str,
    name: str = "acp-mock",
    agent_id: str = None,
) -> str:
    import uuid

    if agent_id is None:
        agent_id = str(uuid.uuid4())

    config = json.dumps(
        {
            "command": "uv",
            "args": ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", "acp"],
            "timeout": 30,
        }
    )

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, "acp")
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled) VALUES (?, ?, 'acp', ?, ?, ?)",
            (agent_id, name, driver_id, config, True),
        )
        conn.commit()

    return agent_id


def seed_acp_scenario_agent(
    db_url: str,
    name: str,
    scenario: str,
    delegate_to: str = None,
    delegate_count: int = None,
    agent_id: str = None,
) -> str:
    import uuid

    if agent_id is None:
        agent_id = str(uuid.uuid4())

    args = [
        "run",
        "python",
        "-m",
        "agentbeacon.mock_agent",
        "--mode",
        "acp",
        "--scenario",
        scenario,
    ]
    if delegate_to:
        args.extend(["--delegate-to", delegate_to])
    if delegate_count is not None:
        args.extend(["--delegate-count", str(delegate_count)])

    config = json.dumps({"command": "uv", "args": args, "timeout": 60})

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, "acp")
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled) VALUES (?, ?, 'acp', ?, ?, ?)",
            (agent_id, name, driver_id, config, True),
        )
        conn.commit()

    return agent_id


def get_execution_row(db_url: str, execution_id: str) -> dict:
    with db_conn(db_url) as conn:
        cur = conn.execute("SELECT * FROM executions WHERE id = ?", (execution_id,))
        row = cur.fetchone()
        assert row is not None, f"execution {execution_id} not found"
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))


def create_child_session(
    db_url: str,
    parent_id: str,
    exec_id: str,
    agent_id: str,
    status: str = "submitted",
) -> str:
    import uuid

    status_map = {
        "submitted": ("run", "unassigned", None),
        "working": ("run", "running", None),
        "input-required": ("run", "idle", None),
        "completed": ("terminate", "idle", "completed"),
        "failed": ("terminate", "crashed", "failed"),
        "canceled": ("terminate", "idle", "canceled"),
    }
    desired, executor_state, outcome = status_map.get(
        status, ("run", "unassigned", None)
    )
    child_id = str(uuid.uuid4())
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, desired, executor_state, outcome, sandbox_policy) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                child_id,
                exec_id,
                parent_id,
                agent_id,
                desired,
                executor_state,
                outcome,
                '{"fs_level":"unrestricted"}',
            ),
        )
        conn.commit()
    return child_id


def rest_escalate(scheduler_url: str, session_id: str, body: dict) -> httpx.Response:
    return httpx.post(
        f"{scheduler_url}/api/v1/escalate",
        json=body,
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=5,
    )


def rest_escalate_ok(scheduler_url: str, session_id: str, body: dict) -> dict:
    resp = rest_escalate(scheduler_url, session_id, body)
    assert resp.status_code == 200, f"escalate failed: {resp.status_code} {resp.text}"
    return resp.json()
