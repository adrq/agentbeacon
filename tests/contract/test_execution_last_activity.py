# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.mock_agent_helpers import (
    create_child_session_raw,
    get_events,
    get_execution_row,
    get_session_row,
    get_task_queue_count,
    get_task_queue_entry,
    insert_task,
    make_agent_message,
    make_executor_report,
    make_turn_result,
    post_worker_sync,
    rest_escalate_ok,
    set_execution_fields,
    set_session_fields,
)
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    post_user_message,
    scheduler_context,
    seed_test_agent,
)

LONG_TICK = {"AGENTBEACON_LIVENESS_INTERVAL_SECS": "3600"}
OLD_CREATED = "2000-01-01 00:00:00"
OLD_ACTIVITY = "2001-01-01 00:00:00"


def _execution(ctx, exec_id):
    resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()["execution"]


def _last_activity(ctx, exec_id):
    return datetime.fromisoformat(_execution(ctx, exec_id)["last_activity_at"])


def _backdate(ctx, exec_id, **extra):
    set_execution_fields(
        ctx["db_url"],
        exec_id,
        created_at=OLD_CREATED,
        last_activity_at=OLD_ACTIVITY,
        **extra,
    )
    return _last_activity(ctx, exec_id)


def _now():
    return datetime.now(timezone.utc)


def _assert_last_activity_within(value, t0, t1):
    lo = t0.replace(microsecond=0) - timedelta(seconds=1)
    hi = t1 + timedelta(seconds=1)
    assert lo <= value <= hi, (
        f"last_activity_at={value.isoformat()} outside [{lo.isoformat()}, {hi.isoformat()}]"
    )


def _marker():
    return f"marker-{uuid.uuid4().hex}"


def _payload_text(payload):
    return json.dumps(payload)


def _seed_agent(ctx, agent_type="claude_sdk"):
    name = f"activity-{uuid.uuid4().hex[:8]}"
    agent_id = seed_test_agent(ctx["db_url"], name=name, agent_type=agent_type)
    return agent_id, name


def _start_root(ctx, worker="w-root", state="idle", agent_session_id=None):
    agent_id, agent_name = _seed_agent(ctx)
    exec_id, root = create_execution_via_api(ctx["url"], agent_id, "initial prompt")
    data = post_worker_sync(ctx["url"], worker)
    assert data["type"] == "command"
    assert data["action"]["type"] == "assign"
    assert data["action"]["session_id"] == root
    post_worker_sync(
        ctx["url"],
        worker,
        executor_report=make_executor_report(root, "running", agent_session_id),
        command_ack=data["token"],
    )
    if state == "idle":
        post_worker_sync(
            ctx["url"], worker, executor_report=make_executor_report(root, "idle")
        )
    assert get_task_queue_count(ctx["db_url"], root) == 0
    return exec_id, root, agent_name


def _delegate(ctx, root, agent_name, prompt):
    result = mcp_tools_call(
        ctx["url"], root, "delegate", {"agent": agent_name, "prompt": prompt}
    )
    return json.loads(result["content"][0]["text"])["session_id"]


def _claim_child(ctx, child, worker="w-child", agent_session_id=None):
    data = post_worker_sync(ctx["url"], worker)
    assert data["type"] == "command"
    assert data["action"]["type"] == "assign"
    assert data["action"]["session_id"] == child
    post_worker_sync(
        ctx["url"],
        worker,
        executor_report=make_executor_report(child, "running", agent_session_id),
        command_ack=data["token"],
    )


def _hierarchical_name(ctx, exec_id, session_id):
    resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}/sessions", timeout=10)
    assert resp.status_code == 200
    for entry in resp.json():
        if entry["session_id"] == session_id:
            return entry["hierarchical_name"]
    raise AssertionError(f"session {session_id} not in discovery")


def _send_lateral(ctx, sender, to, text):
    return httpx.post(
        f"{ctx['url']}/api/v1/messages",
        json={"to": to, "parts": [{"text": text}]},
        headers={"Authorization": f"Bearer {sender}"},
        timeout=10,
    )


def _message(text, msg_seq):
    return {**make_agent_message(text), "msg_seq": msg_seq}


def _message_seqs(ctx, session_id):
    return sorted(
        e["msg_seq"]
        for e in get_events(ctx["db_url"], session_id, "message")
        if e["msg_seq"] is not None
    )


def _platform_types(ctx, session_id):
    types = []
    for event in get_events(ctx["db_url"], session_id, "platform"):
        payload = event["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        for part in payload.get("parts", []):
            if "data" in part:
                types.append(part["data"].get("type"))
    return types


def _insert_execution(conn, exec_id, created_at, last_activity_at):
    conn.execute(
        "INSERT INTO executions (id, context_id, sandbox_policy, created_at, updated_at, "
        "last_activity_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            exec_id,
            f"ctx-{exec_id}",
            '{"fs_level":"unrestricted"}',
            created_at,
            created_at,
            last_activity_at,
        ),
    )


def _listed_ids(ctx, ids):
    resp = httpx.get(f"{ctx['url']}/api/v1/executions", timeout=10)
    assert resp.status_code == 200
    return [e["id"] for e in resp.json() if e["id"] in ids]


@DUAL_BACKEND
def test_create_sets_last_activity_to_created_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _ = _seed_agent(ctx)
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "hello")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT CASE WHEN last_activity_at = created_at THEN 1 ELSE 0 END "
                "FROM executions WHERE id = ?",
                (exec_id,),
            ).fetchone()
        assert row[0] == 1

        execution = _execution(ctx, exec_id)
        assert execution["last_activity_at"] != ""
        assert execution["last_activity_at"] == execution["created_at"]


@DUAL_BACKEND
def test_user_message_to_root_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx)
        _backdate(ctx, exec_id)

        t0 = _now()
        resp = post_user_message(ctx["url"], root, "more work")
        t1 = _now()

        assert resp.status_code == 200, resp.text
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_user_message_to_child_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        child = _delegate(ctx, root, agent_name, "child task")
        _backdate(ctx, exec_id)

        t0 = _now()
        resp = post_user_message(ctx["url"], child, "child follow-up")
        t1 = _now()

        assert resp.status_code == 200, resp.text
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_agent_message_to_child_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        child = _delegate(ctx, root, agent_name, "child task")
        child_name = _hierarchical_name(ctx, exec_id, child)
        baseline = _backdate(ctx, exec_id)

        resp = _send_lateral(ctx, root, child_name, "extra detail")

        assert resp.status_code == 200, resp.text
        assert resp.json()["recipient_session_id"] == child
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_agent_message_to_root_advances_last_activity_only_when_dispatched(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        child = _delegate(ctx, root, agent_name, "child task")
        root_name = _hierarchical_name(ctx, exec_id, root)
        marker = _marker()
        baseline = _backdate(ctx, exec_id)

        resp = _send_lateral(ctx, child, root_name, f"status {marker}")

        assert resp.status_code == 200, resp.text
        assert get_task_queue_count(ctx["db_url"], root) == 1
        payload, _source = get_task_queue_entry(ctx["db_url"], root)
        assert marker in _payload_text(payload)
        assert _last_activity(ctx, exec_id) == baseline

        t0 = _now()
        data = post_worker_sync(ctx["url"], "w-root")
        t1 = _now()

        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"
        assert data["action"]["session_id"] == root
        assert marker in _payload_text(data["action"]["payload"])
        assert get_task_queue_count(ctx["db_url"], root) == 0
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_root_assign_with_payload_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _ = _seed_agent(ctx)
        marker = _marker()
        exec_id, root = create_execution_via_api(
            ctx["url"], agent_id, f"initial {marker}"
        )
        assert get_task_queue_count(ctx["db_url"], root) == 1
        _backdate(ctx, exec_id)

        t0 = _now()
        data = post_worker_sync(ctx["url"], "w-fresh")
        t1 = _now()

        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        assert data["action"]["session_id"] == root
        assert marker in _payload_text(data["action"]["payload"])
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_root_resume_assign_without_payload_leaves_last_activity_unchanged(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _ = _seed_agent(ctx)
        exec_id, root = create_execution_via_api(ctx["url"], agent_id, "start")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root,))
            conn.commit()
        assert get_task_queue_count(ctx["db_url"], root) == 0
        set_session_fields(
            ctx["db_url"],
            root,
            executor_state="unassigned",
            agent_session_id="sdk-session-resume",
            cwd="/tmp/test-cwd",
        )
        baseline = _backdate(ctx, exec_id)

        data = post_worker_sync(ctx["url"], "w-resume")

        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        assert data["action"]["session_id"] == root
        assert data["action"]["resume"] is True
        assert data["action"]["payload"] is None
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_root_feed_turn_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx)
        marker = _marker()
        insert_task(ctx["db_url"], exec_id, root, f"queued {marker}")
        _backdate(ctx, exec_id)

        t0 = _now()
        data = post_worker_sync(ctx["url"], "w-root")
        t1 = _now()

        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"
        assert data["action"]["session_id"] == root
        assert marker in _payload_text(data["action"]["payload"])
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_child_dispatch_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        delegate_marker = _marker()
        child = _delegate(ctx, root, agent_name, f"child task {delegate_marker}")
        assert get_task_queue_count(ctx["db_url"], root) == 0

        baseline = _backdate(ctx, exec_id)
        data = post_worker_sync(ctx["url"], "w-child")

        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        assert data["action"]["session_id"] == child
        assert data["action"]["payload"] is not None
        assert delegate_marker in _payload_text(data["action"]["payload"])
        assert _last_activity(ctx, exec_id) == baseline

        post_worker_sync(
            ctx["url"],
            "w-child",
            executor_report=make_executor_report(child, "running"),
            command_ack=data["token"],
        )
        feed_marker = _marker()
        insert_task(ctx["db_url"], exec_id, child, f"more {feed_marker}")
        baseline = _backdate(ctx, exec_id)

        data = post_worker_sync(ctx["url"], "w-child")

        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"
        assert data["action"]["session_id"] == child
        assert feed_marker in _payload_text(data["action"]["payload"])
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_root_turn_complete_with_output_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx, state="running")
        _backdate(ctx, exec_id)

        t0 = _now()
        data = post_worker_sync(
            ctx["url"],
            "w-root",
            executor_report=make_executor_report(root, "idle"),
            turn_result=make_turn_result(root, messages=[_message("done", 1)]),
        )
        t1 = _now()

        assert data["type"] == "no_action"
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_streamed_output_leaves_last_activity_unchanged_until_root_turn_completes(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx, state="running")
        baseline = _backdate(ctx, exec_id)

        for seq, text in [(1, "working on it"), (2, "done")]:
            resp = httpx.post(
                f"{ctx['url']}/api/worker/events",
                json={
                    "workerId": "w-root",
                    "executionId": exec_id,
                    "sessionId": root,
                    "msgSeq": seq,
                    "ephemeral": False,
                    "payload": make_agent_message(text),
                },
                timeout=15,
            )
            assert resp.status_code == 201, resp.text
        assert _message_seqs(ctx, root) == [1, 2]
        assert _last_activity(ctx, exec_id) == baseline

        t0 = _now()
        data = post_worker_sync(
            ctx["url"],
            "w-root",
            executor_report=make_executor_report(root, "idle"),
            turn_result=make_turn_result(
                root, messages=[_message("working on it", 1), _message("done", 2)]
            ),
        )
        t1 = _now()

        assert data["type"] == "no_action"
        assert _message_seqs(ctx, root) == [1, 2]
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_root_empty_turn_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx, state="running")
        baseline = _backdate(ctx, exec_id)

        data = post_worker_sync(
            ctx["url"],
            "w-root",
            executor_report=make_executor_report(root, "idle"),
            turn_result=make_turn_result(root, messages=[]),
        )

        assert data["type"] == "no_action"
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_root_crashed_report_with_output_leaves_last_activity_unchanged(
    test_database,
):
    with scheduler_context(db_url=test_database, env=LONG_TICK) as ctx:
        exec_id, root, _ = _start_root(
            ctx, state="running", agent_session_id="sdk-session-crash"
        )
        set_session_fields(
            ctx["db_url"], root, cwd="/tmp/test-cwd", recovery_attempts=0
        )
        assert get_task_queue_count(ctx["db_url"], root) == 0
        baseline = _backdate(ctx, exec_id)

        data = post_worker_sync(
            ctx["url"],
            "w-root",
            executor_report=make_executor_report(root, "crashed"),
            turn_result=make_turn_result(
                root, messages=[_message("partial", 1), _message("more", 2)]
            ),
        )

        assert data["type"] == "no_action"
        assert get_session_row(ctx["db_url"], root)["executor_state"] == "crashed"
        assert _message_seqs(ctx, root) == [1, 2]
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_command_ack_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _ = _seed_agent(ctx)
        exec_id, root = create_execution_via_api(ctx["url"], agent_id, "start")
        data = post_worker_sync(ctx["url"], "w-root")
        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        token = data["token"]
        assert get_task_queue_count(ctx["db_url"], root) == 0
        baseline = _backdate(ctx, exec_id)

        data = post_worker_sync(ctx["url"], "w-root", command_ack=token)

        assert data["type"] == "no_action"
        assert get_session_row(ctx["db_url"], root)["command_token"] is None
        assert _last_activity(ctx, exec_id) == baseline


def _complete_child_turn(ctx, exec_id, root, agent_name):
    child = _delegate(ctx, root, agent_name, "child task")
    _claim_child(ctx, child)
    assert get_task_queue_count(ctx["db_url"], root) == 0
    baseline = _backdate(ctx, exec_id)

    post_worker_sync(
        ctx["url"],
        "w-child",
        executor_report=make_executor_report(child, "idle"),
        turn_result=make_turn_result(child, messages=[_message("child done", 1)]),
    )
    return child, baseline


@DUAL_BACKEND
def test_child_turn_complete_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        child, baseline = _complete_child_turn(ctx, exec_id, root, agent_name)

        assert get_task_queue_count(ctx["db_url"], root) == 1
        _payload, source = get_task_queue_entry(ctx["db_url"], root)
        assert source == f"child_result:{child}"
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_root_dispatch_of_child_notification_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        child, _baseline = _complete_child_turn(ctx, exec_id, root, agent_name)
        assert get_task_queue_count(ctx["db_url"], root) == 1

        t0 = _now()
        data = post_worker_sync(ctx["url"], "w-root")
        t1 = _now()

        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"
        assert data["action"]["session_id"] == root
        payload_text = _payload_text(data["action"]["payload"])
        assert "turn_complete" in payload_text
        assert child in payload_text
        assert get_task_queue_count(ctx["db_url"], root) == 0
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_terminate_advances_last_activity_once(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _root, _ = _start_root(ctx)
        _backdate(ctx, exec_id)

        t0 = _now()
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        t1 = _now()
        assert resp.status_code == 200, resp.text
        first = _last_activity(ctx, exec_id)
        _assert_last_activity_within(first, t0, t1)

        time.sleep(1.1)
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )

        assert resp.status_code == 200, resp.text
        assert _last_activity(ctx, exec_id) == first


@DUAL_BACKEND
def test_stopped_root_idle_result_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx, state="running")
        set_session_fields(ctx["db_url"], root, desired="stop", desired_by="user")
        baseline = _backdate(ctx, exec_id)

        data = post_worker_sync(
            ctx["url"],
            "w-root",
            executor_report=make_executor_report(root, "idle"),
            turn_result=make_turn_result(
                root, messages=[_message("stopped output", 1)]
            ),
        )

        assert _message_seqs(ctx, root) == [1]
        assert "turn_complete" not in _platform_types(ctx, root)
        assert data["type"] == "no_action"
        assert get_execution_row(ctx["db_url"], exec_id)["outcome"] is None
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_repaired_terminal_root_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database, env=LONG_TICK) as ctx:
        agent_id, _ = _seed_agent(ctx)
        exec_id, root = create_execution_via_api(ctx["url"], agent_id, "start")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root,))
            conn.commit()
        set_session_fields(
            ctx["db_url"],
            root,
            desired="run",
            outcome="completed",
            executor_state="idle",
        )
        _backdate(ctx, exec_id)

        t0 = _now()
        post_worker_sync(ctx["url"], "repair-worker")
        t1 = _now()

        assert get_execution_row(ctx["db_url"], exec_id)["outcome"] == "completed"
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_escalation_answer_advances_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx)
        decision = rest_escalate_ok(
            ctx["url"], root, {"questions": [{"question": "Which way?"}]}
        )
        _backdate(ctx, exec_id)

        t0 = _now()
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{root}/message",
            json={
                "parts": [
                    {"text": "left"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": decision["event_id"],
                        }
                    },
                ]
            },
            timeout=15,
        )
        t1 = _now()

        assert resp.status_code == 200, resp.text
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_escalation_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx)
        baseline = _backdate(ctx, exec_id)

        rest_escalate_ok(ctx["url"], root, {"questions": [{"question": "Which way?"}]})

        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_detected_crash_and_retry_leave_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database, env=LONG_TICK) as ctx:
        agent_id, _ = _seed_agent(ctx)
        exec_id, root = create_execution_via_api(ctx["url"], agent_id, "start")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root,))
            conn.commit()
        set_session_fields(
            ctx["db_url"],
            root,
            desired="run",
            executor_state="running",
            worker_id="worker-dead",
            command_token=str(uuid.uuid4()),
            command_type="feed_turn",
            command_has_payload=True,
            command_at="2020-01-01T00:00:00Z",
            agent_session_id="sdk-session-123",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )
        baseline = _backdate(ctx, exec_id)

        post_worker_sync(ctx["url"], "probe-worker")

        row = get_session_row(ctx["db_url"], root)
        assert row["executor_state"] == "crashed"
        assert row["worker_id"] is None
        assert row["command_token"] is None
        assert _last_activity(ctx, exec_id) == baseline

        for _ in range(20):
            if get_session_row(ctx["db_url"], root)["recovery_attempts"] == 1:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], root)
        assert row["recovery_attempts"] == 1
        assert row["outcome"] is None
        assert get_execution_row(ctx["db_url"], exec_id)["outcome"] is None
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_worker_reported_crash_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database, env=LONG_TICK) as ctx:
        exec_id, root, agent_name = _start_root(
            ctx, state="running", agent_session_id="sdk-root"
        )
        child = _delegate(ctx, root, agent_name, "child task")
        _claim_child(ctx, child, agent_session_id="sdk-child")
        for session in (root, child):
            set_session_fields(
                ctx["db_url"], session, cwd="/tmp/test-cwd", recovery_attempts=0
            )
        assert get_task_queue_count(ctx["db_url"], root) == 0
        baseline = _backdate(ctx, exec_id)

        for worker, session in (("w-root", root), ("w-child", child)):
            data = post_worker_sync(
                ctx["url"],
                worker,
                executor_report=make_executor_report(session, "crashed"),
            )
            assert data["type"] == "no_action"

        for session in (root, child):
            assert (
                get_session_row(ctx["db_url"], session)["executor_state"] == "crashed"
            )
        assert get_execution_row(ctx["db_url"], exec_id)["outcome"] is None
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_continue_from_leaves_last_activity_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx)
        agent_id = get_session_row(ctx["db_url"], root)["agent_id"]
        child = create_child_session_raw(
            ctx["db_url"],
            root,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        baseline = _backdate(ctx, exec_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child}/continue",
            json={"parts": [{"text": "try again"}]},
            timeout=10,
        )

        assert resp.status_code == 201, resp.text
        replacement = resp.json()["session_id"]
        assert replacement != child
        assert get_session_row(ctx["db_url"], replacement)["parent_session_id"] == root
        assert get_task_queue_count(ctx["db_url"], root) == 1
        payload, _source = get_task_queue_entry(ctx["db_url"], root)
        assert child in _payload_text(payload)
        assert _last_activity(ctx, exec_id) == baseline


def _user_action_on_child(test_database, action, expected_desired):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, agent_name = _start_root(ctx)
        child = _delegate(ctx, root, agent_name, "child task")
        _claim_child(ctx, child)
        assert get_task_queue_count(ctx["db_url"], root) == 0
        baseline = _backdate(ctx, exec_id)

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{child}/{action}", timeout=10)

        assert resp.status_code == 200, resp.text
        assert get_session_row(ctx["db_url"], child)["desired"] == expected_desired
        assert get_task_queue_count(ctx["db_url"], root) == 1
        payload, _source = get_task_queue_entry(ctx["db_url"], root)
        assert child in _payload_text(payload)
        assert get_execution_row(ctx["db_url"], exec_id)["outcome"] is None
        assert _last_activity(ctx, exec_id) == baseline


@DUAL_BACKEND
def test_user_stop_child_leaves_last_activity_unchanged(test_database):
    _user_action_on_child(test_database, "stop", "stop")


@DUAL_BACKEND
def test_user_terminate_child_leaves_last_activity_unchanged(test_database):
    _user_action_on_child(test_database, "terminate", "terminate")


@DUAL_BACKEND
def test_user_message_leaves_updated_at_unchanged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, root, _ = _start_root(ctx)
        _backdate(ctx, exec_id, updated_at="2000-06-01 00:00:00")
        updated_before = _execution(ctx, exec_id)["updated_at"]

        t0 = _now()
        resp = post_user_message(ctx["url"], root, "more work")
        t1 = _now()

        assert resp.status_code == 200, resp.text
        assert _execution(ctx, exec_id)["updated_at"] == updated_before
        _assert_last_activity_within(_last_activity(ctx, exec_id), t0, t1)


@DUAL_BACKEND
def test_list_orders_by_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        ids = ["order-a", "order-b", "order-c"]
        with db_conn(ctx["db_url"]) as conn:
            _insert_execution(
                conn, "order-a", "2025-01-03 00:00:00", "2026-01-01 00:00:00"
            )
            _insert_execution(
                conn, "order-b", "2025-01-01 00:00:00", "2026-01-03 00:00:00"
            )
            _insert_execution(
                conn, "order-c", "2025-01-02 00:00:00", "2026-01-02 00:00:00"
            )
            conn.commit()

        assert _listed_ids(ctx, ids) == ["order-b", "order-c", "order-a"]


@DUAL_BACKEND
def test_list_orders_by_subsecond_last_activity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        ids = ["subsec-a", "subsec-b"]
        with db_conn(ctx["db_url"]) as conn:
            _insert_execution(
                conn, "subsec-a", "2025-01-02 00:00:00", "2026-01-01 00:00:00.300"
            )
            _insert_execution(
                conn, "subsec-b", "2025-01-01 00:00:00", "2026-01-01 00:00:00.700"
            )
            conn.commit()

        assert _listed_ids(ctx, ids) == ["subsec-b", "subsec-a"]


@DUAL_BACKEND
def test_list_breaks_last_activity_ties_by_created_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        ids = ["tie-a", "tie-b"]
        with db_conn(ctx["db_url"]) as conn:
            _insert_execution(
                conn, "tie-b", "2025-01-01 00:00:00", "2026-01-01 00:00:00"
            )
            _insert_execution(
                conn, "tie-a", "2025-01-02 00:00:00", "2026-01-01 00:00:00"
            )
            conn.commit()

        assert _listed_ids(ctx, ids) == ["tie-a", "tie-b"]


@DUAL_BACKEND
def test_list_breaks_full_timestamp_ties_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        ids = ["tiea", "tieb", "tiec"]
        with db_conn(ctx["db_url"]) as conn:
            for exec_id in ["tieb", "tiec", "tiea"]:
                _insert_execution(
                    conn, exec_id, "2025-03-01 00:00:00", "2025-03-01 00:00:00"
                )
            conn.commit()

        assert _listed_ids(ctx, ids) == ["tiec", "tieb", "tiea"]
