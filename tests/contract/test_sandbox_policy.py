# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import create_child_session_raw, post_worker_sync


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_default_when_omitted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution"]["sandbox_policy"] == {"fs_level": "unrestricted"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_workspace(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": "workspace"},
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution"]["sandbox_policy"] == {"fs_level": "workspace"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_read_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": "read_only"},
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution"]["sandbox_policy"] == {"fs_level": "read_only"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_empty_object_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_unknown_field_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": "workspace", "extra": True},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_unknown_variant_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": "yolo"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_null_fs_level_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": None},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_explicit_unrestricted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": "unrestricted"},
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution"]["sandbox_policy"] == {"fs_level": "unrestricted"}


@pytest.mark.parametrize(
    "bad_value",
    [None, [], "string", 123, True],
    ids=["null", "array", "string", "number", "boolean"],
)
@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_non_object_rejected(test_database, bad_value):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": bad_value,
            },
            timeout=5,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for sandbox_policy={bad_value!r}, got {resp.status_code}: {resp.text}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_inherited_by_root_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "sandbox_policy": {"fs_level": "workspace"},
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        exec_id = data["execution"]["id"]
        session_id = data["session_id"]

        detail = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5
        ).json()
        session = next(s for s in detail["sessions"] if s["id"] == session_id)
        assert session["sandbox_policy"] == {"fs_level": "workspace"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_inherited_by_delegated_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        helper = seed_test_agent(ctx["db_url"], name="helper")

        exec_id, session_id = create_execution_via_api(
            ctx["url"],
            agent_id=lead,
            agent_ids=[lead, helper],
            cwd=tempfile.gettempdir(),
            sandbox_policy={"fs_level": "read_only"},
        )

        sync_data = post_worker_sync(ctx["url"], "w1")
        assert sync_data.get("type") == "command"

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "delegate",
            {"agent": "helper", "prompt": "do something"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]
        assert child_session_id

        detail = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5
        ).json()
        child = next(s for s in detail["sessions"] if s["id"] == child_session_id)
        assert child["sandbox_policy"] == {"fs_level": "read_only"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sandbox_policy_inherited_by_continued_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(
            ctx["url"],
            agent_id=agent_id,
            cwd=tempfile.gettempdir(),
            sandbox_policy={"fs_level": "read_only"},
        )

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET sandbox_policy = ? WHERE id = ?",
                ('{"fs_level":"read_only"}', child_sid),
            )
            conn.commit()

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_sid}/continue",
            json={"parts": [{"text": "continue"}]},
            timeout=5,
        )
        assert resp.status_code == 201
        new_sid = resp.json()["session_id"]

        detail = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5
        ).json()
        continued = next(s for s in detail["sessions"] if s["id"] == new_sid)
        assert continued["sandbox_policy"] == {"fs_level": "read_only"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_corrupted_sandbox_policy_returns_500_on_execution_detail(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"],
            agent_id=agent_id,
            cwd=tempfile.gettempdir(),
            sandbox_policy={"fs_level": "workspace"},
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET sandbox_policy = ? WHERE id = ?",
                ("INVALID_JSON", session_id),
            )
            conn.commit()

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        assert resp.status_code == 500, (
            f"Expected 500 for corrupted sandbox_policy, got {resp.status_code}: {resp.text}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_corrupted_sandbox_policy_returns_500_on_session_get(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"],
            agent_id=agent_id,
            cwd=tempfile.gettempdir(),
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET sandbox_policy = ? WHERE id = ?",
                ('{"fs_level":"yolo"}', session_id),
            )
            conn.commit()

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/{session_id}", timeout=5)
        assert resp.status_code == 500, (
            f"Expected 500 for corrupted sandbox_policy, got {resp.status_code}: {resp.text}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_corrupted_sandbox_policy_prevents_assign(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"],
            agent_id=agent_id,
            cwd=tempfile.gettempdir(),
            sandbox_policy={"fs_level": "workspace"},
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET sandbox_policy = ? WHERE id = ?",
                ("NOT_VALID", session_id),
            )
            conn.commit()

        sync_data = post_worker_sync(ctx["url"], "w1")
        assert (
            sync_data.get("type") != "command"
            or sync_data.get("session_id") != session_id
        ), "Reconciler should refuse to assign a session with corrupted sandbox_policy"
