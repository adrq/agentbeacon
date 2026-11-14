# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import tempfile
import uuid
from datetime import datetime

import httpx
import pytest

from tests.contract.test_wiki_sharing_concurrency import race_and_release
from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    agent_headers,
    assert_forbidden,
    assert_problem,
    assert_unauthorized,
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

V1 = "/api/v1"
VALUE_MAX_BYTES = 65536
NAME_RULE = "name must match [A-Z_][A-Z0-9_]* and be at most 255 bytes"
BODY_INVALID = "the request body is not a valid secret request"
SECRET_NOT_FOUND = "no secret with this name exists in the project"
NOT_ATTACHED = "the secret is not attached to this execution"
FETCH_NOT_FOUND = "no secret with this name is attached to this execution"
PROJECT_NOT_FOUND = "no project has this id or slug"
EXECUTION_NOT_FOUND = "no execution has this id"
PROJECT_SECRET_KEYS = {"name", "created_at", "updated_at", "attached_executions"}


def _project(url):
    return create_project_via_api(
        url, f"secrets-{uuid.uuid4().hex[:8]}", path=tempfile.mkdtemp()
    )


def _create(url, project_id, name, value):
    return httpx.post(
        f"{url}{V1}/projects/{project_id}/secrets",
        json={"name": name, "value": value},
        timeout=10,
    )


def _created(url, project_id, name, value):
    resp = _create(url, project_id, name, value)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rotate(url, project_id, name, value):
    return httpx.patch(
        f"{url}{V1}/projects/{project_id}/secrets/{name}",
        json={"value": value},
        timeout=10,
    )


def _delete(url, project_id, name):
    return httpx.delete(f"{url}{V1}/projects/{project_id}/secrets/{name}", timeout=10)


def _project_entry(url, project_id, name):
    resp = httpx.get(f"{url}{V1}/projects/{project_id}/secrets", timeout=10)
    assert resp.status_code == 200, resp.text
    return next(e for e in resp.json() if e["name"] == name)


def _launch_resp(url, agent_id, project_id, secrets=None):
    payload = {
        "root_agent_id": agent_id,
        "agent_ids": [agent_id],
        "parts": [{"text": "task"}],
        "project_id": project_id,
    }
    if secrets is not None:
        payload["secrets"] = secrets
    return httpx.post(f"{url}{V1}/executions", json=payload, timeout=10)


def _launch(url, agent_id, project_id, secrets=None):
    resp = _launch_resp(url, agent_id, project_id, secrets)
    assert resp.status_code == 201, resp.text
    return resp.json()["execution"]["id"], resp.json()["session_id"]


def _attach(url, execution_id, name):
    return httpx.post(
        f"{url}{V1}/executions/{execution_id}/secrets", json={"name": name}, timeout=10
    )


def _detach(url, execution_id, name):
    return httpx.delete(
        f"{url}{V1}/executions/{execution_id}/secrets/{name}", timeout=10
    )


def _listed(url, execution_id, headers=None):
    resp = httpx.get(
        f"{url}{V1}/executions/{execution_id}/secrets", headers=headers, timeout=10
    )
    assert resp.status_code == 200, resp.text
    return [e["name"] for e in resp.json()]


def _fetch(url, session_id, name):
    return httpx.get(
        f"{url}{V1}/secrets/{name}", headers=agent_headers(url, session_id), timeout=10
    )


def _fetched(url, session_id, name):
    resp = _fetch(url, session_id, name)
    assert resp.status_code == 200, resp.text
    return resp.json()["value"]


def _terminate(url, execution_id):
    resp = httpx.post(f"{url}{V1}/executions/{execution_id}/terminate", timeout=10)
    assert resp.status_code == 200, resp.text


def _attachment_rows(db_url, execution_id):
    """(secret deleted, detached) for each attachment row of an execution."""
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT s.deleted_at, a.detached_at FROM execution_secrets a "
            "JOIN secrets s ON s.id = a.secret_id WHERE a.execution_id = ?",
            (execution_id,),
        ).fetchall()
    return sorted(
        (deleted is not None, detached is not None) for deleted, detached in rows
    )


def _world(ctx):
    agent_id = seed_test_agent(ctx["db_url"], name=f"secrets-{uuid.uuid4().hex[:6]}")
    project = _project(ctx["url"])
    return agent_id, project["id"]


@DUAL_BACKEND
def test_create_secret_returns_metadata_and_no_value(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        resp = _create(ctx["url"], project_id, "GH_TOKEN", "value-placeholder")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == PROJECT_SECRET_KEYS
        assert body["name"] == "GH_TOKEN"
        assert body["attached_executions"] == []
        assert "value-placeholder" not in resp.text


@DUAL_BACKEND
@pytest.mark.parametrize("name", ["lower", "1ABC", "A" * 256])
def test_create_secret_rejects_invalid_names(test_database, name):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        resp = _create(ctx["url"], project_id, name, "v")
        assert_problem(resp, 400, "request.invalid", NAME_RULE)


@DUAL_BACKEND
@pytest.mark.parametrize("value", ["", "a\u0000b"])
def test_create_secret_rejects_empty_value_and_nul(test_database, value):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        resp = _create(ctx["url"], project_id, "TOKEN", value)
        assert_problem(resp, 400, "request.invalid", "non-empty string without NUL")


@DUAL_BACKEND
def test_create_secret_rejects_oversized_value(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        limits = httpx.get(f"{ctx['url']}/api/versions", timeout=10).json()["limits"]
        limit = limits["secrets"]["value_max_bytes"]
        assert limit == VALUE_MAX_BYTES

        over = _create(ctx["url"], project_id, "BIG", "x" * (limit + 1))
        assert_problem(over, 413, "request.too_large", "value exceeds the size limit")
        at = _create(ctx["url"], project_id, "BIG", "x" * limit)
        assert at.status_code == 201, at.text


@DUAL_BACKEND
def test_malformed_secret_body_detail_does_not_quote_input(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        resp = httpx.post(
            f"{ctx['url']}{V1}/projects/{project_id}/secrets",
            json={"name": "TOKEN", "value": 424242},
            timeout=10,
        )
        assert_problem(resp, 400, "request.invalid", BODY_INVALID)
        assert resp.json()["detail"] == BODY_INVALID
        assert "424242" not in resp.text


@DUAL_BACKEND
def test_multiline_value_round_trips_exactly(test_database):
    value = "-----BEGIN TEST-----\nabc\n-----END TEST-----\n"
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "PEM", value)
        _, session_id = _launch(ctx["url"], agent_id, project_id, ["PEM"])
        assert _fetched(ctx["url"], session_id, "PEM") == value


@DUAL_BACKEND
def test_duplicate_name_in_project_conflicts(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "one")
        resp = _create(ctx["url"], project_id, "TOKEN", "two")
        assert_problem(resp, 409, "resource.conflict", "already exists in the project")


@DUAL_BACKEND
def test_same_name_in_two_projects_is_independent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, first = _world(ctx)
        second = _project(ctx["url"])["id"]
        _created(ctx["url"], first, "TOKEN", "first-value")
        _created(ctx["url"], second, "TOKEN", "second-value")
        _, s1 = _launch(ctx["url"], agent_id, first, ["TOKEN"])
        _, s2 = _launch(ctx["url"], agent_id, second, ["TOKEN"])
        assert _fetched(ctx["url"], s1, "TOKEN") == "first-value"
        assert _fetched(ctx["url"], s2, "TOKEN") == "second-value"


@DUAL_BACKEND
def test_rotate_secret_refreshes_updated_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "old")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE secrets SET updated_at = '2000-01-01 00:00:00' WHERE project_id = ?",
                (project_id,),
            )
            conn.commit()
        resp = _rotate(ctx["url"], project_id, "TOKEN", "new")
        assert resp.status_code == 200, resp.text
        assert set(resp.json()) == PROJECT_SECRET_KEYS
        updated = datetime.fromisoformat(resp.json()["updated_at"])
        assert updated > datetime.fromisoformat("2000-01-01T00:00:00+00:00")


@DUAL_BACKEND
def test_rotate_unknown_secret_is_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        resp = _rotate(ctx["url"], project_id, "MISSING", "v")
        assert_problem(resp, 404, "resource.not_found", SECRET_NOT_FOUND)


@DUAL_BACKEND
def test_rotation_reaches_every_attached_execution_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v1")
        _, s1 = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        e2, s2 = _launch(ctx["url"], agent_id, project_id)
        assert _attach(ctx["url"], e2, "TOKEN").status_code == 204
        _, s3 = _launch(ctx["url"], agent_id, project_id)

        assert _rotate(ctx["url"], project_id, "TOKEN", "v2").status_code == 200
        assert _fetched(ctx["url"], s1, "TOKEN") == "v2"
        assert _fetched(ctx["url"], s2, "TOKEN") == "v2"
        assert_problem(
            _fetch(ctx["url"], s3, "TOKEN"), 404, "resource.not_found", FETCH_NOT_FOUND
        )


@DUAL_BACKEND
def test_secret_routes_with_missing_project_are_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        missing = str(uuid.uuid4())
        base = f"{ctx['url']}{V1}/projects/{missing}/secrets"
        responses = [
            httpx.get(base, timeout=10),
            httpx.post(base, json={"name": "TOKEN", "value": "v"}, timeout=10),
            httpx.patch(f"{base}/TOKEN", json={"value": "v"}, timeout=10),
            httpx.delete(f"{base}/TOKEN", timeout=10),
        ]
        for resp in responses:
            assert_problem(resp, 404, "resource.not_found", PROJECT_NOT_FOUND)


@DUAL_BACKEND
def test_secret_routes_with_missing_execution_are_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        missing = str(uuid.uuid4())
        base = f"{ctx['url']}{V1}/executions/{missing}/secrets"
        responses = [
            httpx.get(base, timeout=10),
            httpx.post(base, json={"name": "TOKEN"}, timeout=10),
            httpx.delete(f"{base}/TOKEN", timeout=10),
        ]
        for resp in responses:
            assert_problem(resp, 404, "resource.not_found", EXECUTION_NOT_FOUND)


@DUAL_BACKEND
def test_delete_refused_while_attached_to_running_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        resp = _delete(ctx["url"], project_id, "TOKEN")
        assert_problem(resp, 409, "resource.conflict", "detach it first")
        assert resp.json()["executions"] == [execution_id]


@DUAL_BACKEND
def test_delete_allowed_when_attached_execution_has_ended(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        _terminate(ctx["url"], execution_id)

        assert (
            _project_entry(ctx["url"], project_id, "TOKEN")["attached_executions"] == []
        )
        rotated = _rotate(ctx["url"], project_id, "TOKEN", "v2")
        assert rotated.json()["attached_executions"] == []
        assert _delete(ctx["url"], project_id, "TOKEN").status_code == 204


@DUAL_BACKEND
def test_delete_allowed_when_execution_terminate_requested_without_outcome(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET desired = 'terminate' WHERE id = ?",
                (execution_id,),
            )
            conn.commit()
        assert _delete(ctx["url"], project_id, "TOKEN").status_code == 204


@DUAL_BACKEND
def test_deleted_secret_value_is_cleared_in_store(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        assert _delete(ctx["url"], project_id, "TOKEN").status_code == 204
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT value, deleted_at FROM secrets WHERE project_id = ? AND name = ?",
                (project_id, "TOKEN"),
            ).fetchone()
        assert row[0] is None
        assert row[1] is not None


@DUAL_BACKEND
def test_secrets_routes_accept_project_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = _project(ctx["url"])
        _created(ctx["url"], project["slug"], "TOKEN", "v")
        resp = httpx.get(
            f"{ctx['url']}{V1}/projects/{project['slug']}/secrets", timeout=10
        )
        assert resp.status_code == 200, resp.text
        assert [e["name"] for e in resp.json()] == ["TOKEN"]


@DUAL_BACKEND
def test_launch_attaches_named_secrets(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "B_TOKEN", "b")
        _created(ctx["url"], project_id, "A_TOKEN", "a")
        execution_id, _ = _launch(
            ctx["url"], agent_id, project_id, ["B_TOKEN", "A_TOKEN", "B_TOKEN"]
        )
        assert _listed(ctx["url"], execution_id) == ["A_TOKEN", "B_TOKEN"]


@DUAL_BACKEND
def test_launch_with_secrets_requires_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _ = _world(ctx)
        resp = httpx.post(
            f"{ctx['url']}{V1}/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "task"}],
                "cwd": tempfile.gettempdir(),
                "secrets": ["TOKEN"],
            },
            timeout=10,
        )
        assert_problem(resp, 400, "request.invalid", "secrets require a project_id")


@DUAL_BACKEND
def test_project_secret_not_attached_unless_selected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "A", "a")
        _created(ctx["url"], project_id, "B", "b")
        selected, session_id = _launch(ctx["url"], agent_id, project_id, ["A"])
        assert _listed(ctx["url"], selected) == ["A"]
        assert_problem(
            _fetch(ctx["url"], session_id, "B"),
            404,
            "resource.not_found",
            FETCH_NOT_FOUND,
        )
        unselected, _ = _launch(ctx["url"], agent_id, project_id)
        assert _listed(ctx["url"], unselected) == []


@DUAL_BACKEND
@pytest.mark.parametrize(
    "names, detail",
    [
        (["A", "lower"], NAME_RULE),
        (["A", "MISSING"], "secret not found in project: MISSING"),
    ],
)
def test_multi_name_launch_with_one_invalid_name_creates_nothing(
    test_database, names, detail
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "A", "a")
        resp = _launch_resp(ctx["url"], agent_id, project_id, names)
        assert_problem(resp, 400, "request.invalid", detail)
        with db_conn(ctx["db_url"]) as conn:
            executions = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            attachments = conn.execute(
                "SELECT COUNT(*) FROM execution_secrets"
            ).fetchone()[0]
        assert executions == 0
        assert attachments == 0


@DUAL_BACKEND
def test_attach_mid_run_is_visible_on_next_fetch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, session_id = _launch(ctx["url"], agent_id, project_id)
        assert_problem(
            _fetch(ctx["url"], session_id, "TOKEN"),
            404,
            "resource.not_found",
            FETCH_NOT_FOUND,
        )

        assert _attach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert _fetched(ctx["url"], session_id, "TOKEN") == "v"

        for entry in [
            _project_entry(ctx["url"], project_id, "TOKEN"),
            _rotate(ctx["url"], project_id, "TOKEN", "v2").json(),
        ]:
            attached = entry["attached_executions"]
            assert len(attached) == 1
            assert set(attached[0]) == {"execution_id", "attached_at"}
            assert attached[0]["execution_id"] == execution_id
            assert isinstance(attached[0]["attached_at"], str)

        assert _detach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert (
            _project_entry(ctx["url"], project_id, "TOKEN")["attached_executions"] == []
        )
        rotated = _rotate(ctx["url"], project_id, "TOKEN", "v3")
        assert rotated.json()["attached_executions"] == []


@DUAL_BACKEND
def test_attach_twice_keeps_one_active_attachment(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id)
        assert _attach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert _attach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert _attachment_rows(ctx["db_url"], execution_id) == [(False, False)]


@DUAL_BACKEND
def test_detach_takes_effect_on_next_fetch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, session_id = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        assert _fetch(ctx["url"], session_id, "TOKEN").status_code == 200
        assert _detach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert_problem(
            _fetch(ctx["url"], session_id, "TOKEN"),
            404,
            "resource.not_found",
            FETCH_NOT_FOUND,
        )


@DUAL_BACKEND
def test_attach_to_ended_execution_is_refused(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id)
        _terminate(ctx["url"], execution_id)
        resp = _attach(ctx["url"], execution_id, "TOKEN")
        assert_problem(resp, 409, "execution.terminated", "secrets cannot be attached")


@DUAL_BACKEND
def test_attach_resolves_name_in_execution_project_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        other = _project(ctx["url"])["id"]
        _created(ctx["url"], other, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id)
        resp = _attach(ctx["url"], execution_id, "TOKEN")
        assert_problem(resp, 404, "resource.not_found", SECRET_NOT_FOUND)


@DUAL_BACKEND
def test_attach_to_execution_without_project_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _ = _world(ctx)
        execution_id, _ = create_execution_via_api(ctx["url"], agent_id, "task")
        resp = _attach(ctx["url"], execution_id, "TOKEN")
        assert_problem(resp, 400, "request.invalid", "the execution has no project")


@DUAL_BACKEND
@pytest.mark.parametrize("name", ["BAD\u0000NAME", "lower"])
def test_attach_with_invalid_name_is_a_bad_request(test_database, name):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        execution_id, _ = _launch(ctx["url"], agent_id, project_id)
        resp = _attach(ctx["url"], execution_id, name)
        assert_problem(resp, 400, "request.invalid", NAME_RULE)
        assert _attachment_rows(ctx["db_url"], execution_id) == []


@DUAL_BACKEND
def test_detach_keeps_attachment_history(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        assert _detach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert _attachment_rows(ctx["db_url"], execution_id) == [(False, True)]

        assert _attach(ctx["url"], execution_id, "TOKEN").status_code == 204
        assert _attachment_rows(ctx["db_url"], execution_id) == [
            (False, False),
            (False, True),
        ]


@DUAL_BACKEND
def test_detach_unattached_is_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id)
        resp = _detach(ctx["url"], execution_id, "TOKEN")
        assert_problem(resp, 404, "resource.not_found", NOT_ATTACHED)


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_attach_waits_for_secret_lock_and_sees_concurrent_delete(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, _ = _launch(ctx["url"], agent_id, project_id)

        resp = race_and_release(
            ctx,
            "SELECT id FROM secrets WHERE project_id = %s AND name = %s "
            "AND deleted_at IS NULL FOR UPDATE",
            (project_id, "TOKEN"),
            "secrets",
            "UPDATE secrets SET value = NULL, deleted_at = CURRENT_TIMESTAMP "
            "WHERE project_id = %s AND name = %s",
            (project_id, "TOKEN"),
            lambda: _attach(ctx["url"], execution_id, "TOKEN"),
        )

        assert_problem(resp, 404, "resource.not_found", SECRET_NOT_FOUND)
        assert _attachment_rows(ctx["db_url"], execution_id) == []


def _end_execution(db_url, execution_id, root_session_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE sessions SET desired = 'terminate', outcome = 'failed', "
            "executor_state = 'crashed', agent_session_id = 'as-1', cwd = '/tmp' "
            "WHERE id = ?",
            (root_session_id,),
        )
        conn.execute(
            "UPDATE executions SET desired = 'terminate', outcome = 'failed', "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (execution_id,),
        )
        conn.commit()


@DUAL_BACKEND
def test_deleted_secret_lifecycle_across_recovery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        agent_id, project_id = _world(ctx)
        _created(url, project_id, "TOKEN", "v1")
        execution_id, session_id = _launch(url, agent_id, project_id, ["TOKEN"])

        _end_execution(ctx["db_url"], execution_id, session_id)
        ended = httpx.get(f"{url}{V1}/executions/{execution_id}", timeout=10).json()
        assert ended["execution"]["outcome"] == "failed"
        assert _project_entry(url, project_id, "TOKEN")["attached_executions"] == []
        assert _delete(url, project_id, "TOKEN").status_code == 204

        recovered = httpx.post(
            f"{url}{V1}/sessions/{session_id}/recover", json={}, timeout=10
        )
        assert recovered.status_code == 200, recovered.text
        live = httpx.get(f"{url}{V1}/executions/{execution_id}", timeout=10).json()
        assert live["execution"]["outcome"] is None
        assert live["execution"]["desired"] == "run"

        agent = agent_headers(url, session_id)
        assert _listed(url, execution_id) == []
        assert _listed(url, execution_id, headers=agent) == []
        assert_problem(
            _fetch(url, session_id, "TOKEN"), 404, "resource.not_found", FETCH_NOT_FOUND
        )
        assert_problem(
            _detach(url, execution_id, "TOKEN"), 404, "resource.not_found", NOT_ATTACHED
        )
        assert _attachment_rows(ctx["db_url"], execution_id) == [(True, False)]

        _created(url, project_id, "TOKEN", "v2")
        assert _listed(url, execution_id) == []
        assert_problem(
            _detach(url, execution_id, "TOKEN"), 404, "resource.not_found", NOT_ATTACHED
        )

        assert _attach(url, execution_id, "TOKEN").status_code == 204
        assert _listed(url, execution_id) == ["TOKEN"]
        assert _fetched(url, session_id, "TOKEN") == "v2"

        assert _detach(url, execution_id, "TOKEN").status_code == 204
        assert _attachment_rows(ctx["db_url"], execution_id) == [
            (False, True),
            (True, False),
        ]


@DUAL_BACKEND
def test_reused_name_on_live_execution_lists_once(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        agent_id, project_id = _world(ctx)
        _created(url, project_id, "TOKEN", "v1")
        execution_id, session_id = _launch(url, agent_id, project_id, ["TOKEN"])
        assert _detach(url, execution_id, "TOKEN").status_code == 204
        assert _delete(url, project_id, "TOKEN").status_code == 204
        _created(url, project_id, "TOKEN", "v2")
        assert _attach(url, execution_id, "TOKEN").status_code == 204
        assert _listed(url, execution_id) == ["TOKEN"]
        assert _fetched(url, session_id, "TOKEN") == "v2"


@DUAL_BACKEND
def test_agent_lists_only_attached_names(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "ATTACHED", "a")
        _created(ctx["url"], project_id, "OTHER", "o")
        execution_id, session_id = _launch(
            ctx["url"], agent_id, project_id, ["ATTACHED"]
        )
        resp = httpx.get(
            f"{ctx['url']}{V1}/executions/{execution_id}/secrets",
            headers=agent_headers(ctx["url"], session_id),
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        assert [e["name"] for e in resp.json()] == ["ATTACHED"]
        assert set(resp.json()[0]) == {"name", "attached_at"}


@DUAL_BACKEND
def test_agent_fetches_attached_value(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "A", "value-a")
        _, session_id = _launch(ctx["url"], agent_id, project_id, ["A"])
        resp = _fetch(ctx["url"], session_id, "A")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"name": "A", "value": "value-a"}
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.headers["cache-control"] == "no-store"


@DUAL_BACKEND
def test_any_session_in_execution_can_fetch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, root_id = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                'sandbox_policy) VALUES (?, ?, ?, ?, \'{"fs_level":"unrestricted"}\')',
                (child_id, execution_id, root_id, agent_id),
            )
            conn.commit()
        assert _fetched(ctx["url"], child_id, "TOKEN") == "v"


@DUAL_BACKEND
def test_unattached_detached_deleted_and_unknown_fetches_are_indistinguishable(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        agent_id, project_id = _world(ctx)
        for name in ("UNATTACHED", "DETACHED", "DELETED"):
            _created(url, project_id, name, "v")
        execution_id, session_id = _launch(
            url, agent_id, project_id, ["DETACHED", "DELETED"]
        )
        assert _detach(url, execution_id, "DETACHED").status_code == 204
        assert _detach(url, execution_id, "DELETED").status_code == 204
        assert _delete(url, project_id, "DELETED").status_code == 204

        bodies = []
        for name in ("UNATTACHED", "DETACHED", "DELETED", "UNKNOWN"):
            resp = _fetch(url, session_id, name)
            assert_problem(resp, 404, "resource.not_found", FETCH_NOT_FOUND)
            bodies.append(resp.json())
        assert all(b == bodies[0] for b in bodies)


@DUAL_BACKEND
@pytest.mark.parametrize("segment", ["BAD%00NAME", "lower"])
def test_fetch_with_invalid_name_is_the_fixed_not_found(test_database, segment):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _, session_id = _launch(ctx["url"], agent_id, project_id)
        resp = httpx.get(
            f"{ctx['url']}{V1}/secrets/{segment}",
            headers=agent_headers(ctx["url"], session_id),
            timeout=10,
        )
        assert_problem(resp, 404, "resource.not_found", FETCH_NOT_FOUND)
        assert resp.json()["detail"] == FETCH_NOT_FOUND


@DUAL_BACKEND
def test_agent_cannot_reach_another_executions_secrets(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        _, session_a = _launch(ctx["url"], agent_id, project_id)
        execution_b, _ = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        listing = httpx.get(
            f"{ctx['url']}{V1}/executions/{execution_b}/secrets",
            headers=agent_headers(ctx["url"], session_a),
            timeout=10,
        )
        assert_forbidden(listing)
        assert_problem(
            _fetch(ctx["url"], session_a, "TOKEN"),
            404,
            "resource.not_found",
            FETCH_NOT_FOUND,
        )


@DUAL_BACKEND
def test_agent_cannot_manage_secrets(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, session_id = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        headers = agent_headers(ctx["url"], session_id)
        url = ctx["url"]
        responses = [
            httpx.get(
                f"{url}{V1}/projects/{project_id}/secrets", headers=headers, timeout=10
            ),
            httpx.post(
                f"{url}{V1}/projects/{project_id}/secrets",
                json={"name": "NEW", "value": "v"},
                headers=headers,
                timeout=10,
            ),
            httpx.patch(
                f"{url}{V1}/projects/{project_id}/secrets/TOKEN",
                json={"value": "v2"},
                headers=headers,
                timeout=10,
            ),
            httpx.delete(
                f"{url}{V1}/projects/{project_id}/secrets/TOKEN",
                headers=headers,
                timeout=10,
            ),
            httpx.post(
                f"{url}{V1}/executions/{execution_id}/secrets",
                json={"name": "TOKEN"},
                headers=headers,
                timeout=10,
            ),
            httpx.delete(
                f"{url}{V1}/executions/{execution_id}/secrets/TOKEN",
                headers=headers,
                timeout=10,
            ),
        ]
        for resp in responses:
            assert_forbidden(resp)


@DUAL_BACKEND
def test_human_cannot_fetch_a_value(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        assert_forbidden(httpx.get(f"{ctx['url']}{V1}/secrets/TOKEN", timeout=10))


@DUAL_BACKEND
def test_fetch_after_execution_terminate_is_unauthorized(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, project_id = _world(ctx)
        _created(ctx["url"], project_id, "TOKEN", "v")
        execution_id, session_id = _launch(ctx["url"], agent_id, project_id, ["TOKEN"])
        headers = agent_headers(ctx["url"], session_id)
        _terminate(ctx["url"], execution_id)
        resp = httpx.get(f"{ctx['url']}{V1}/secrets/TOKEN", headers=headers, timeout=10)
        assert_unauthorized(resp)


@DUAL_BACKEND
def test_secret_value_does_not_leak_in_exercised_surfaces(test_database):
    first = "sentinel-7f3c9e-not-a-real-token"
    second = "sentinel-0b81d4-not-a-real-token"
    with scheduler_context(db_url=test_database, env={"RUST_LOG": "trace"}) as ctx:
        url = ctx["url"]
        agent_id, project_id = _world(ctx)
        seen = []

        created = _create(url, project_id, "TOKEN", first)
        assert created.status_code == 201, created.text
        seen.append(created.text)
        execution_id, session_id = _launch(url, agent_id, project_id, ["TOKEN"])
        rotated = _rotate(url, project_id, "TOKEN", second)
        assert rotated.status_code == 200, rotated.text
        seen.append(rotated.text)
        other_id, _ = _launch(url, agent_id, project_id)
        assert _attach(url, other_id, "TOKEN").status_code == 204
        assert _fetched(url, session_id, "TOKEN") == second
        assert _detach(url, other_id, "TOKEN").status_code == 204

        malformed = httpx.post(
            f"{url}{V1}/projects/{project_id}/secrets",
            json={"name": "LEAK", "value": first, "extra": True},
            timeout=10,
        )
        assert_problem(malformed, 400, "request.invalid", BODY_INVALID)
        conflict = _create(url, project_id, "TOKEN", first)
        assert_problem(conflict, 409, "resource.conflict", "already exists")
        seen += [malformed.text, conflict.text]

        for path in [
            f"/projects/{project_id}/secrets",
            f"/executions/{execution_id}",
            f"/executions/{execution_id}/secrets",
            f"/executions/{execution_id}/events",
            f"/executions/{other_id}/events",
        ]:
            resp = httpx.get(f"{url}{V1}{path}", timeout=10)
            assert resp.status_code == 200, (path, resp.text)
            seen.append(resp.text)

        with open(ctx["process"].log_path, errors="replace") as log:
            seen.append(log.read())

        for text in seen:
            assert first not in text
            assert second not in text
