"""Contract tests for transaction safety.

Tests verify CAS (Compare-And-Swap) execution status transitions,
wiki OCC atomicity, and wiki page + tag atomicity.

CAS atomicity is guaranteed by the database engine (single atomic UPDATE
with WHERE status IN (...)). Sequential tests that pre-set conflicting
state fully validate our handling of each CasResult variant.
"""

import json
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


def _set_execution_status(ctx, exec_id, status):
    """Set execution status directly in DB."""
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "UPDATE executions SET status = ? WHERE id = ?",
            (status, exec_id),
        )
        conn.commit()


def _set_session_status(ctx, session_id, status):
    """Set session status directly in DB."""
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "UPDATE sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
        conn.commit()


def _create_child_session(ctx, parent_id, exec_id, agent_id, status="submitted"):
    """Insert a child session directly into the DB."""
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (child_id, exec_id, parent_id, agent_id, status),
        )
        conn.commit()
    return child_id


def _put_page(base_url, project_id, slug, title, body, revision_number=None, tags=None):
    """PUT a wiki page, returning the raw response."""
    payload = {"title": title, "body": body}
    if revision_number is not None:
        payload["revision_number"] = revision_number
    if tags is not None:
        payload["tags"] = tags
    return httpx.put(
        f"{base_url}/api/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


# --- Execution status CAS ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_execution_already_completed_returns_409(test_database):
    """CAS prevents cancel from overwriting completed status."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "completed")
        _set_session_status(ctx, lead_sid, "completed")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp.status_code == 409

        with db_conn(ctx["db_url"]) as conn:
            status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert status == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_complete_execution_already_canceled_returns_409(test_database):
    """CAS prevents complete from overwriting canceled status."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "canceled")
        _set_session_status(ctx, lead_sid, "canceled")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 409

        with db_conn(ctx["db_url"]) as conn:
            status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_execution_from_submitted(test_database):
    """CAS allows cancel from submitted (no sessions yet working)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, _lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        # Execution starts as submitted

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["execution"]["status"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_emits_state_change_event(test_database):
    """Verify state_change event is recorded on successful cancel."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "working")
        _set_session_status(ctx, lead_sid, "working")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp.status_code == 200

        events_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/events", timeout=10
        )
        events = events_resp.json()
        exec_state_events = [
            e
            for e in events
            if e["event_type"] == "state_change" and e.get("session_id") is None
        ]
        assert len(exec_state_events) >= 1
        payload = exec_state_events[-1]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["to"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_does_not_overwrite_canceled(test_database):
    """Crash handler CAS skips execution already canceled.

    Simulates the race window where execution CAS won (canceled) but cascade
    hasn't reached the root session yet — session is still "working" when it
    crashes. The crash handler must reach propagate_failure_to_execution() and
    the CAS must miss, leaving execution as "canceled" (not "failed").
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        # Set execution terminal directly (bypass cascade) while session stays working
        _set_session_status(ctx, lead_sid, "working")
        _set_execution_status(ctx, exec_id, "canceled")

        # Report crash on the still-working root session
        crash_resp = httpx.post(
            f"{ctx['url']}/api/worker/sync",
            json={
                "sessionResult": {
                    "sessionId": lead_sid,
                    "error": "process died",
                    "errorKind": "executor_failed",
                }
            },
            timeout=10,
        )
        assert crash_resp.status_code == 200

        # Execution stays canceled (crash handler CAS missed — execution already terminal)
        with db_conn(ctx["db_url"]) as conn:
            final_status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert final_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_double_cancel_returns_409(test_database):
    """Second cancel returns 409 (execution is already terminal)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "working")
        _set_session_status(ctx, lead_sid, "working")

        resp1 = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp1.status_code == 200

        resp2 = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp2.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_not_found_returns_404(test_database):
    """CAS NotFound: cancel nonexistent execution returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="lead-agent")
        fake_id = str(uuid.uuid4())
        resp = httpx.post(f"{ctx['url']}/api/executions/{fake_id}/cancel", timeout=10)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_complete_execution_submitted_rejects(test_database):
    """Complete from submitted returns 409 (CAS only accepts working/input-required)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, _lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        # Execution starts as submitted

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 409


# --- Forward-only CAS paths ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_message_does_not_resurrect_canceled_execution(test_database):
    """Messaging CAS prevents resurrecting a terminal execution.

    Simulates the race window where execution is canceled but session is still
    input-required. A user message should not flip execution back to working.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        # Session is input-required (waiting for user), execution already canceled
        _set_session_status(ctx, lead_sid, "input-required")
        _set_execution_status(ctx, exec_id, "canceled")

        _ = httpx.post(
            f"{ctx['url']}/api/sessions/{lead_sid}/message",
            json={"message": "hello"},
            timeout=10,
        )
        # Message delivery may succeed or fail — either way, execution stays canceled
        with db_conn(ctx["db_url"]) as conn:
            final_status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert final_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_does_not_overwrite_canceled_execution(test_database):
    """Worker turn-complete CAS skips input-required transition on canceled execution.

    Simulates the race window where execution is canceled but the root session
    is still working (cascade hasn't reached it). Turn-complete should not
    flip execution to input-required.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        # Session still working, execution already canceled
        _set_session_status(ctx, lead_sid, "working")
        _set_execution_status(ctx, exec_id, "canceled")

        resp = httpx.post(
            f"{ctx['url']}/api/worker/sync",
            json={
                "sessionResult": {
                    "sessionId": lead_sid,
                    "turnMessages": [],
                    "hasPendingTurn": False,
                }
            },
            timeout=10,
        )
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            final_status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert final_status == "canceled"


# --- Wiki OCC atomicity ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_update_revision_increments_atomically(test_database):
    """Update page: revision archive + increment happen atomically."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-txn-test")
        pid = project["id"]

        _put_page(ctx["url"], pid, "atomic-page", "V1", "body v1")
        resp = _put_page(
            ctx["url"], pid, "atomic-page", "V2", "body v2", revision_number=1
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2

        # Verify revision 1 is archived
        rev_resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/pages/atomic-page/revisions",
            timeout=5,
        )
        assert rev_resp.status_code == 200
        revisions = rev_resp.json()
        assert len(revisions) == 1
        assert revisions[0]["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_update_occ_conflict_returns_409(test_database):
    """Concurrent updates: second update with stale revision gets 409."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-occ-test")
        pid = project["id"]

        _put_page(ctx["url"], pid, "occ-page", "V1", "body v1")
        _put_page(ctx["url"], pid, "occ-page", "V2", "body v2", revision_number=1)

        # Stale revision
        resp = _put_page(
            ctx["url"], pid, "occ-page", "Stale", "stale body", revision_number=1
        )
        assert resp.status_code == 409


# --- Wiki page + tags atomic ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_create_page_with_tags_atomic(test_database):
    """Page creation with tags: both committed atomically."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-tags-test")
        pid = project["id"]

        resp = _put_page(
            ctx["url"], pid, "tagged-page", "Tagged", "body", tags=["alpha", "beta"]
        )
        assert resp.status_code == 201
        assert sorted(resp.json()["tags"]) == ["alpha", "beta"]

        # Verify via GET
        get_resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/pages/tagged-page", timeout=5
        )
        assert sorted(get_resp.json()["tags"]) == ["alpha", "beta"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_update_page_with_tags_atomic(test_database):
    """Page update with new tags: revision + tags committed atomically."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-tags-update")
        pid = project["id"]

        _put_page(ctx["url"], pid, "tag-update", "V1", "body", tags=["old"])
        resp = _put_page(
            ctx["url"],
            pid,
            "tag-update",
            "V2",
            "body v2",
            revision_number=1,
            tags=["new1", "new2"],
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2
        assert sorted(resp.json()["tags"]) == ["new1", "new2"]

        # Verify old tag no longer associated
        get_resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/pages/tag-update", timeout=5
        )
        assert "old" not in get_resp.json()["tags"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_update_page_without_tags_preserves_existing(test_database):
    """Page update without tags field: existing tags unchanged."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-tags-preserve")
        pid = project["id"]

        _put_page(ctx["url"], pid, "keep-tags", "V1", "body", tags=["keep-me"])
        resp = _put_page(
            ctx["url"], pid, "keep-tags", "V2", "body v2", revision_number=1
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["keep-me"]
