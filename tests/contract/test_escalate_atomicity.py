# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import uuid

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate, rest_escalate_ok


def _platform_rows(db_url, exec_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT id, payload FROM events WHERE execution_id = ? "
            "AND event_type = 'platform' ORDER BY id ASC",
            (exec_id,),
        ).fetchall()


@DUAL_BACKEND
def test_one_batch_is_one_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="atomicity")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        body = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {"question": "Q1?", "context": "c1"},
                    {
                        "question": "Q2?",
                        "options": [
                            {"label": "A", "description": "first"},
                            {"label": "B", "description": "second"},
                        ],
                    },
                    {"question": "Q3?"},
                ],
                "importance": "blocking",
            },
        )
        assert set(body) == {"batch_id", "event_id"}

        rows = _platform_rows(test_database, exec_id)
        assert len(rows) == 1
        assert str(rows[0][0]) == body["event_id"]

        data = json.loads(rows[0][1])["parts"][0]["data"]
        assert data["type"] == "escalate"
        assert data["batch_id"] == body["batch_id"]
        assert data["importance"] == "blocking"
        assert [q["question"] for q in data["questions"]] == ["Q1?", "Q2?", "Q3?"]
        assert data["questions"][0]["context"] == "c1"
        assert data["questions"][1]["options"][0]["label"] == "A"
        assert "batch_size" not in data
        assert "batch_index" not in data
        assert "question" not in data


@DUAL_BACKEND
def test_the_stored_shape_classifies_as_an_escalation_everywhere(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="atomicity-shape")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        body = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Envelope?"}]}
        )

        stored = json.loads(_platform_rows(test_database, exec_id)[0][1])
        assert stored["role"] == "ROLE_AGENT"
        assert set(stored) == {"role", "parts"}

        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=10
        ).json()
        surfaced = [e for e in page["items"] if e["id"] == body["event_id"]]
        assert len(surfaced) == 1
        assert surfaced[0]["event_type"] == "escalate"

        detail = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/{body['event_id']}",
            timeout=10,
        ).json()
        assert detail["event_type"] == "escalate"
        assert detail["payload"] == surfaced[0]["payload"]


@DUAL_BACKEND
def test_options_may_carry_labels_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="atomicity-labels-only")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        body = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Pick one",
                        "options": [{"label": "A"}, {"label": "B"}],
                    }
                ]
            },
        )

        stored = json.loads(_platform_rows(test_database, exec_id)[0][1])
        options = stored["parts"][0]["data"]["questions"][0]["options"]
        assert [o["label"] for o in options] == ["A", "B"]
        for option in options:
            assert "description" not in option, option

        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{body['event_id']}", timeout=10
        ).json()
        served = detail["questions"][0]["options"]
        assert [o["label"] for o in served] == ["A", "B"]
        assert [o["description"] for o in served] == [None, None]


@DUAL_BACKEND
def test_options_may_mix_described_and_bare_labels(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="atomicity-mixed-options")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Pick one",
                        "options": [
                            {"label": "A", "description": "explained"},
                            {"label": "B"},
                        ],
                    }
                ]
            },
        )

        stored = json.loads(_platform_rows(test_database, exec_id)[0][1])
        options = stored["parts"][0]["data"]["questions"][0]["options"]
        assert options[0]["description"] == "explained"
        assert "description" not in options[1]


@DUAL_BACKEND
def test_validation_happens_before_any_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="atomicity-validation")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        before = len(_platform_rows(test_database, exec_id))

        rejected = [
            {"questions": []},
            {"questions": [{"question": f"Q{i}?"} for i in range(5)]},
            {"questions": [{"question": "Q?"}], "importance": "urgent"},
            {
                "questions": [
                    {
                        "question": "Q?",
                        "options": [{"label": "only", "description": "x"}],
                    }
                ]
            },
        ]
        for body in rejected:
            resp = rest_escalate(ctx["url"], session_id, body)
            assert resp.status_code == 400, body
        assert len(_platform_rows(test_database, exec_id)) == before


@DUAL_BACKEND
def test_escalate_is_root_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="atomicity-root")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        child_id = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'child', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (child_id, exec_id, session_id, agent_id),
            )
            conn.commit()

        resp = rest_escalate(ctx["url"], child_id, {"questions": [{"question": "Q?"}]})
        assert resp.status_code == 403
