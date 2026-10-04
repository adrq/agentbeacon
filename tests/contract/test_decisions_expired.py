# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import threading
import time

import httpx
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _terminate(db_url, exec_id, desired_only=False):
    with db_conn(db_url) as conn:
        if desired_only:
            conn.execute(
                "UPDATE executions SET desired = 'terminate' WHERE id = ?", (exec_id,)
            )
        else:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
            )
        conn.commit()


def _walk(url):
    resp = httpx.get(
        f"{url}/api/v1/decisions", params={"state": "resolved"}, timeout=30
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _pending(url):
    resp = httpx.get(f"{url}/api/v1/decisions", timeout=30)
    assert resp.status_code == 200, resp.text
    return resp.json()["decisions"]


def _detail(url, event_id):
    resp = httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=15)
    assert resp.status_code == 200, resp.text
    return resp.json()


@DUAL_BACKEND
def test_an_unresolved_decision_under_a_terminated_execution_is_expired(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-basic")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Expiring?"}]}
        )
        _terminate(test_database, exec_id)

        walked = [i for i in _walk(ctx["url"]) if i["event_id"] == decision["event_id"]]
        assert len(walked) == 1
        assert walked[0]["status"] == "resolved"

        assert _detail(ctx["url"], decision["event_id"])["status"] == "expired"

        assert decision["event_id"] not in [d["event_id"] for d in _pending(ctx["url"])]


@DUAL_BACKEND
def test_the_crash_gap_state_also_expires(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-crash-gap")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Gap?"}]}
        )
        _terminate(test_database, exec_id, desired_only=True)

        assert _detail(ctx["url"], decision["event_id"])["status"] == "expired"
        assert decision["event_id"] not in [d["event_id"] for d in _pending(ctx["url"])]


@DUAL_BACKEND
def test_a_pending_decision_under_a_live_execution_is_in_neither(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-live-pending")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Live?"}]}
        )

        assert decision["event_id"] not in [i["event_id"] for i in _walk(ctx["url"])]
        assert _detail(ctx["url"], decision["event_id"])["status"] == "pending"
        assert decision["event_id"] in [d["event_id"] for d in _pending(ctx["url"])]


@DUAL_BACKEND
def test_an_answered_decision_under_a_live_execution_is_in_history(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-answered-live")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Answered?"}]}
        )
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "yes"},
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
        assert resp.status_code == 200, resp.text

        assert decision["event_id"] in [i["event_id"] for i in _walk(ctx["url"])]
        assert decision["event_id"] not in [d["event_id"] for d in _pending(ctx["url"])]
        assert _detail(ctx["url"], decision["event_id"])["status"] == "answered"


@DUAL_BACKEND
def test_a_database_with_no_markers_still_walks(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-no-markers")
        expected = []
        for i in range(3):
            exec_id, session_id = create_execution_via_api(
                ctx["url"], agent_id, f"task {i}"
            )
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )
            _terminate(test_database, exec_id)
            expected.append(decision["event_id"])

        with db_conn(test_database) as conn:
            markers = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'platform' "
                "AND payload LIKE '%question_answer%'"
            ).fetchone()[0]
        assert markers == 0

        walked = [i["event_id"] for i in _walk(ctx["url"])]
        assert set(expected) <= set(walked)
        assert len(walked) == len(set(walked))


def _recover(db_url, exec_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE executions SET outcome = NULL, desired = 'run' WHERE id = ?",
            (exec_id,),
        )
        conn.commit()


@DUAL_BACKEND
def test_recovery_moves_a_decision_from_expired_back_to_pending(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-recovered")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Recoverable?"}]}
        )
        event_id = decision["event_id"]

        _terminate(test_database, exec_id)
        assert _detail(ctx["url"], event_id)["status"] == "expired"
        assert [
            d["event_id"] for d in _pending(ctx["url"]) if d["event_id"] == event_id
        ] == []

        _recover(test_database, exec_id)
        assert _detail(ctx["url"], event_id)["status"] == "pending"
        assert [
            d["event_id"] for d in _pending(ctx["url"]) if d["event_id"] == event_id
        ] == [event_id]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_detail_read_overlapping_recovery_never_reports_expired(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-raced")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Raced?"}]}
        )
        event_id = decision["event_id"]
        _terminate(test_database, exec_id)

        result = {}

        def read_detail():
            result["body"] = httpx.get(
                f"{ctx['url']}/api/v1/decisions/{event_id}", timeout=30
            ).json()

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )

            thread = threading.Thread(target=read_detail)
            thread.start()
            time.sleep(2)
            assert thread.is_alive(), "the detail read was expected to take the lock"

            holder.execute(
                "UPDATE executions SET outcome = NULL, desired = 'run' WHERE id = ?",
                (exec_id,),
            )
            holder.commit()

        thread.join(timeout=30)
        assert not thread.is_alive()

        assert result["body"]["status"] == "pending"
        assert [
            d["event_id"] for d in _pending(ctx["url"]) if d["event_id"] == event_id
        ] == [event_id]


@DUAL_BACKEND
def test_a_decision_the_pending_list_serves_is_never_expired_in_detail(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-overlap")

        for round_index in range(6):
            exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
            decision = rest_escalate_ok(
                ctx["url"],
                session_id,
                {"questions": [{"question": f"Overlap {round_index}?"}]},
            )
            event_id = decision["event_id"]
            _terminate(test_database, exec_id)

            observations = []
            stop = threading.Event()

            def observe():
                while not stop.is_set():
                    served = any(
                        d["event_id"] == event_id for d in _pending(ctx["url"])
                    )
                    observations.append(
                        (served, _detail(ctx["url"], event_id)["status"])
                    )

            watcher = threading.Thread(target=observe)
            watcher.start()
            time.sleep(0.05)
            _recover(test_database, exec_id)
            time.sleep(0.2)
            stop.set()
            watcher.join(timeout=30)
            assert not watcher.is_alive()

            seen_served = False
            for served, status in observations:
                seen_served = seen_served or served
                if seen_served:
                    assert status != "expired", observations
            assert seen_served, "the recovered decision was never served as pending"


@DUAL_BACKEND
def test_the_api_terminate_expires_its_decisions_by_the_time_it_answers(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="expired-api-terminate")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Still open?"}]}
        )
        event_id = decision["event_id"]
        assert [
            d["event_id"] for d in _pending(ctx["url"]) if d["event_id"] == event_id
        ] == [event_id]

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["execution"]["outcome"] in ("canceled", "completed")

        assert _detail(ctx["url"], event_id)["status"] == "expired"
        assert [
            d["event_id"] for d in _pending(ctx["url"]) if d["event_id"] == event_id
        ] == []
        walked = [i for i in _walk(ctx["url"]) if i["event_id"] == event_id]
        assert len(walked) == 1
