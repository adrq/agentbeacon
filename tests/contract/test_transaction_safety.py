# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
    create_execution_via_api,
    db_conn,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    get_execution_row,
    make_executor_report,
    make_turn_result,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


def _put_page(base_url, project_id, slug, title, body, tags=None):
    payload = {"title": title, "body": body}
    if tags is not None:
        payload["tags"] = tags
    return httpx.put(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


def _update_page(
    base_url,
    project_id,
    slug,
    revision_number,
    old_body,
    new_body,
    title=None,
    add_tags=None,
    remove_tags=None,
):
    payload = {
        "revision_number": revision_number,
        "edits": [{"old_string": old_body, "new_string": new_body}],
    }
    if title is not None:
        payload["title"] = title
    if add_tags is not None:
        payload["add_tags"] = add_tags
    if remove_tags is not None:
        payload["remove_tags"] = remove_tags
    return httpx.patch(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_patch_revision_increments_atomically(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-txn-test")
        pid = project["id"]

        _put_page(ctx["url"], pid, "atomic-page", "V1", "body v1")
        resp = _update_page(
            ctx["url"],
            pid,
            "atomic-page",
            1,
            "body v1",
            "body v2",
            title={"old": "V1", "new": "V2"},
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2

        rev_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{pid}/wiki/pages/atomic-page/revisions",
            timeout=5,
        )
        assert rev_resp.status_code == 200
        revisions = rev_resp.json()
        assert len(revisions) == 1
        assert revisions[0]["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_patch_occ_conflict_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-occ-test")
        pid = project["id"]

        _put_page(ctx["url"], pid, "occ-page", "V1", "body v1")
        _update_page(ctx["url"], pid, "occ-page", 1, "body v1", "body v2")

        resp = _update_page(ctx["url"], pid, "occ-page", 1, "body v2", "stale body")
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_create_page_with_tags_atomic(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-tags-test")
        pid = project["id"]

        resp = _put_page(
            ctx["url"], pid, "tagged-page", "Tagged", "body", tags=["alpha", "beta"]
        )
        assert resp.status_code == 201
        assert sorted(resp.json()["tags"]) == ["alpha", "beta"]

        get_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{pid}/wiki/pages/tagged-page", timeout=5
        )
        assert sorted(get_resp.json()["tags"]) == ["alpha", "beta"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_patch_page_with_tags_atomic(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-tags-update")
        pid = project["id"]

        _put_page(ctx["url"], pid, "tag-update", "V1", "body", tags=["old"])
        resp = _update_page(
            ctx["url"],
            pid,
            "tag-update",
            1,
            "body",
            "body v2",
            title={"old": "V1", "new": "V2"},
            add_tags=["new1", "new2"],
            remove_tags=["old"],
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2
        assert sorted(resp.json()["tags"]) == ["new1", "new2"]

        get_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{pid}/wiki/pages/tag-update", timeout=5
        )
        assert "old" not in get_resp.json()["tags"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_patch_page_without_tag_deltas_preserves_existing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-tags-preserve")
        pid = project["id"]

        _put_page(ctx["url"], pid, "keep-tags", "V1", "body", tags=["keep-me"])
        resp = _update_page(
            ctx["url"],
            pid,
            "keep-tags",
            1,
            "body",
            "body v2",
            title={"old": "V1", "new": "V2"},
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["keep-me"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_execution_already_completed_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )
        set_session_fields(
            ctx["db_url"], lead_sid, desired="terminate", outcome="completed"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_execution_already_canceled_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )
        set_session_fields(
            ctx["db_url"], lead_sid, desired="terminate", outcome="canceled"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_execution_from_awaiting_input(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "DELETE FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            )
            conn.commit()

        set_session_fields(
            ctx["db_url"], lead_sid, executor_state="idle", worker_id="w-idle"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200
        assert resp.json()["execution"]["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_emits_state_change_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        events_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=10
        )
        events = events_resp.json()["items"]
        exec_state_events = [
            e
            for e in events
            if e["event_type"] == "state_change" and e.get("session_id") is None
        ]
        assert len(exec_state_events) >= 1
        payload = exec_state_events[-1]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload.get("outcome") is not None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_does_not_overwrite_terminated_outcome(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="running",
            worker_id="crash-worker",
        )
        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )

        _crash_resp = post_worker_sync(
            ctx["url"],
            "crash-worker",
            executor_report=make_executor_report(lead_sid, "crashed"),
            turn_result=make_turn_result(
                lead_sid,
                error="process died",
                error_kind="executor_failed",
            ),
        )

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_double_terminate_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp1 = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp1.status_code == 200

        resp2 = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp2.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_not_found_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="lead-agent")
        fake_id = str(uuid.uuid4())
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{fake_id}/terminate", timeout=10
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_message_does_not_resurrect_terminated_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{lead_sid}/message",
            json={"parts": [{"text": "hello"}]},
            timeout=10,
        )
        assert resp.status_code == 409

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_does_not_overwrite_terminated_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="running",
            worker_id="turn-worker",
        )
        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )

        post_worker_sync(
            ctx["url"],
            "turn-worker",
            executor_report=make_executor_report(lead_sid, "idle"),
            turn_result=make_turn_result(lead_sid, messages=[]),
        )

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "canceled"
