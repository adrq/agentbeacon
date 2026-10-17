# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import tempfile
import uuid

import httpx
import pytest

from tests.testhelpers import (
    db_conn,
    ensure_driver_via_api,
    scheduler_context,
    seed_test_agent,
)

NO_WORKERS = {"AGENTBEACON_MAX_WORKERS": "0"}

LABEL_63 = "a" * 63
NAME_253_WILDCARD = "*." + ".".join(["b" * 63, "c" * 63, "d" * 63, "e" * 59])
LABEL_64 = "a" * 64
NAME_254 = ".".join(["b" * 63, "c" * 63, "d" * 63, "e" * 62])


@pytest.fixture(scope="module")
def shared():
    with scheduler_context(env=NO_WORKERS) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="ceiling-agent")
        yield {**ctx, "agent_id": agent_id}


def _create_body(agent_id, network=None, omit_network=False):
    body = {
        "root_agent_id": agent_id,
        "agent_ids": [agent_id],
        "parts": [{"text": "go"}],
        "cwd": tempfile.gettempdir(),
    }
    if not omit_network:
        body["network"] = network
    return body


def _create(ctx, network=None, omit_network=False):
    return httpx.post(
        f"{ctx['url']}/api/v1/executions",
        json=_create_body(ctx["agent_id"], network, omit_network),
        timeout=10,
    )


def _allowlist(*entries):
    return {"kind": "sandboxed", "mode": "allowlist", "entries": list(entries)}


def _execution_count(ctx):
    with db_conn(ctx["db_url"]) as conn:
        return conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]


def _events(ctx, exec_id):
    resp = httpx.get(
        f"{ctx['url']}/api/v1/executions/{exec_id}/events?limit=200", timeout=10
    )
    assert resp.status_code == 200
    return resp.json()["items"]


def _ceiling_events(ctx, exec_id):
    found = []
    for event in _events(ctx, exec_id):
        if event["event_type"] != "platform":
            continue
        parts = event["payload"].get("parts") or []
        if parts and parts[0].get("data", {}).get("type") == "ceiling_changed":
            found.append(event)
    return found


@pytest.mark.parametrize(
    "raw,canonical",
    [
        ("Bücher.Example:80", "xn--bcher-kva.example:80"),
        ("example.com.:443", "example.com:443"),
        ("example.com\u3002:443", "example.com:443"),
        ("example.com\uff0e:443", "example.com:443"),
        ("example.com\uff61:443", "example.com:443"),
        ("*.example.com\u3002:443", "*.example.com:443"),
        ("*.example.com\uff0e:443", "*.example.com:443"),
        ("*.example.com\uff61:443", "*.example.com:443"),
        ("example.com.\u00ad:443", "example.com:443"),
        ("*.Example.com:443", "*.example.com:443"),
        (f"{LABEL_63}.com:443", f"{LABEL_63}.com:443"),
        (f"{NAME_253_WILDCARD}:443", f"{NAME_253_WILDCARD}:443"),
        ("[2001:DB8:0:0::5]:22", "[2001:db8::5]:22"),
        ("[2001:db8::5/128]:22", "[2001:db8::5]:22"),
        ("10.0.0.5/32:22", "10.0.0.5:22"),
        ("10.0.0.0/8:5432-5440", "10.0.0.0/8:5432-5440"),
        ("api.x.com:443-443", "api.x.com:443"),
        ("x.com:1-65535", "x.com:*"),
        ("126.0.0.0/7:80", "126.0.0.0/7:80"),
        ("0.0.0.0/0:443", "0.0.0.0/0:443"),
        ("a--b.com:443", "a--b.com:443"),
        ("host.internal:5432", "host.internal:5432"),
    ],
)
def test_create_accepts_entry_spelling(shared, raw, canonical):
    resp = _create(shared, _allowlist(raw))
    assert resp.status_code == 201, resp.text
    assert resp.json()["execution"]["network"]["ceiling"]["entries"] == [canonical]


@pytest.mark.parametrize(
    "raw,reason_fragment",
    [
        ("under_score.com:443", "invalid host name"),
        ("example.com..:443", "invalid host name"),
        ("a..example.com:443", "invalid host name"),
        ("ab--cd.com:443", "invalid host name"),
        ("-a.com:443", "invalid host name"),
        (f"{LABEL_64}.com:443", "invalid host name"),
        (f"{NAME_254}:443", "invalid host name"),
        ("example.123:443", "all digits"),
        ("1.2.3.4.5:80", "invalid IPv4 address"),
        ("*:443", "wildcard"),
        ("*.:443", "invalid host name"),
        ("a.*.b:443", "wildcard"),
        ("*.*.b:443", "wildcard"),
        ("*.10.0.0.1:443", "wildcard"),
        ("010.0.0.1:80", "leading zeros"),
        ("2001:db8::1:80", "brackets"),
        ("[fe80::1%eth0]:80", "zone"),
        ("[::ffff:10.0.0.1]:80", "IPv4-mapped"),
        ("10.0.0.5/24:80", "10.0.0.0/24"),
        ("127.0.0.0/7:80", "126.0.0.0/7"),
        ("10.0.0.0/08:80", "prefix length"),
        ("10.0.0.0/33:80", "prefix length"),
        ("10.0.0.0/:80", "prefix length"),
        ("x.com:0", "outside 1-65535"),
        ("x.com:080", "leading zero"),
        ("x.com:+80", "invalid port"),
        ("x.com:9-3", "ends before"),
        ("x.com", "missing port"),
        ("127.0.0.1:80", "host.internal"),
        ("127.1.0.0/16:80", "host.internal"),
        ("[::1]:80", "host.internal"),
    ],
)
def test_create_rejects_entry_spelling(shared, raw, reason_fragment):
    resp = _create(shared, _allowlist(raw))
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "request.invalid"
    assert body["detail"] == "1 network entry is invalid"
    assert [e["entry"] for e in body["entries"]] == [raw]
    assert reason_fragment in body["entries"][0]["reason"]


def test_authoring_error_names_entry_as_written(shared):
    resp = _create(shared, _allowlist("127.0.0.1:80-80"))
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "request.invalid"
    assert [e["entry"] for e in body["entries"]] == ["127.0.0.1:80-80"]
    assert (
        "loopback addresses refer to the sandbox itself" in body["entries"][0]["reason"]
    )


@pytest.mark.parametrize(
    "network,expected",
    [
        (
            {"kind": "sandboxed", "mode": "none"},
            {"kind": "sandboxed", "mode": "none", "entries": []},
        ),
        (
            {"kind": "sandboxed", "mode": "allowlist", "entries": []},
            {"kind": "sandboxed", "mode": "allowlist", "entries": []},
        ),
        (
            _allowlist("z.example.com:443", "a.example.com:443", "z.example.com:443"),
            _allowlist("a.example.com:443", "z.example.com:443"),
        ),
        (
            {"kind": "sandboxed", "mode": "egress", "entries": ["host.internal:5432"]},
            {"kind": "sandboxed", "mode": "egress", "entries": ["host.internal:5432"]},
        ),
        (
            {"kind": "sandboxed", "mode": "egress"},
            {"kind": "sandboxed", "mode": "egress", "entries": []},
        ),
        ({"kind": "unsandboxed"}, {"kind": "unsandboxed"}),
    ],
)
def test_create_accepts_ceiling(shared, network, expected):
    resp = _create(shared, network)
    assert resp.status_code == 201, resp.text
    assert resp.json()["execution"]["network"]["ceiling"] == expected


@pytest.mark.parametrize(
    "network,detail_fragment",
    [
        (
            {"kind": "sandboxed", "mode": "none", "entries": ["a.com:443"]},
            "mode none takes no entries",
        ),
        ({"kind": "sandboxed", "mode": "allowlist"}, "allowlist requires entries"),
        (
            {"kind": "sandboxed", "mode": "all"},
            "network.mode must be one of none, allowlist, egress",
        ),
        (None, "network must not be null"),
        ({"mode": "allowlist", "entries": []}, "network.kind is required"),
        ({"kind": "boxed"}, 'network.kind must be "unsandboxed" or "sandboxed"'),
        (
            {"kind": "unsandboxed", "mode": "none"},
            "a network of kind unsandboxed takes no other fields",
        ),
        ("unsandboxed", "network must be an object with a kind"),
        (
            {"kind": "sandboxed", "mode": "open", "entries": []},
            "network.mode must be one of none, allowlist, egress",
        ),
        (
            {"kind": "sandboxed", "mode": "allowlist", "entries": [], "x": 1},
            "unknown network field: x",
        ),
        (
            {"kind": "sandboxed", "mode": "allowlist", "entries": [443]},
            "network.entries must be an array of strings",
        ),
        (
            {"kind": "sandboxed", "mode": "allowlist", "entries": "a.com:443"},
            "network.entries must be an array of strings",
        ),
        (5, "network must be an object with a kind"),
    ],
)
def test_create_rejects_ceiling(shared, network, detail_fragment):
    resp = _create(shared, network)
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "request.invalid"
    assert detail_fragment in body["detail"]


def test_create_reports_every_invalid_entry(shared):
    before = _execution_count(shared)
    resp = _create(
        shared,
        _allowlist("x.com:0", "under_score.com:443", "github.com:443", "[::1]:80"),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "request.invalid"
    assert body["detail"] == "3 network entries are invalid"
    assert [e["entry"] for e in body["entries"]] == [
        "x.com:0",
        "under_score.com:443",
        "[::1]:80",
    ]
    reasons = [e["reason"] for e in body["entries"]]
    assert "outside 1-65535" in reasons[0]
    assert "invalid host name" in reasons[1]
    assert "loopback addresses refer to the sandbox itself" in reasons[2]
    assert _execution_count(shared) == before


def test_create_without_network_is_unsandboxed(shared):
    resp = _create(shared, omit_network=True)
    assert resp.status_code == 201
    network = resp.json()["execution"]["network"]
    assert network["ceiling"] == {"kind": "unsandboxed"}
    assert network["by"] == "user"
    assert isinstance(network["at"], str)
    exec_id = resp.json()["execution"]["id"]
    assert _ceiling_events(shared, exec_id) == []


def test_create_sandboxed_writes_one_launch_event(shared):
    ceiling = _allowlist("github.com:443")
    resp = _create(shared, ceiling)
    assert resp.status_code == 201
    execution = resp.json()["execution"]
    exec_id = execution["id"]
    launch = _ceiling_events(shared, exec_id)
    assert len(launch) == 1
    assert launch[0]["session_id"] is None
    assert launch[0]["payload"] == {
        "parts": [
            {
                "data": {
                    "type": "ceiling_changed",
                    "execution_id": exec_id,
                    "ceiling": ceiling,
                    "previous": None,
                    "by": "user",
                    "at": execution["network"]["at"],
                }
            }
        ]
    }
    first = min(_events(shared, exec_id), key=lambda e: int(e["id"]))
    assert first["id"] == launch[0]["id"]


def test_grandfathered_execution_reads_unsandboxed(shared):
    exec_id = str(uuid.uuid4())
    with db_conn(shared["db_url"]) as conn:
        conn.execute(
            "INSERT INTO executions (id, context_id, sandbox_policy) VALUES (?, ?, ?)",
            (exec_id, f"ctx-{exec_id}", '{"fs_level":"unrestricted"}'),
        )
        conn.commit()
    resp = httpx.get(f"{shared['url']}/api/v1/executions/{exec_id}", timeout=10)
    assert resp.status_code == 200
    assert resp.json()["execution"]["network"] == {
        "ceiling": {"kind": "unsandboxed"},
        "by": None,
        "at": None,
    }


def test_corrupt_ceiling_row_is_an_error(shared):
    resp = _create(shared, _allowlist("github.com:443"))
    exec_id = resp.json()["execution"]["id"]
    with db_conn(shared["db_url"]) as conn:
        conn.execute(
            "UPDATE executions SET ceiling_entries = ? WHERE id = ?",
            ('["GitHub.com:443"]', exec_id),
        )
        conn.commit()
    resp = httpx.get(f"{shared['url']}/api/v1/executions/{exec_id}", timeout=10)
    assert resp.status_code == 500
    assert "malformed ceiling in DB" in resp.json()["error"]


def _patch(ctx, exec_id, body, headers=None):
    return httpx.patch(
        f"{ctx['url']}/api/v1/executions/{exec_id}",
        json=body,
        headers=headers or {},
        timeout=10,
    )


def _create_sandboxed(ctx, ceiling):
    resp = _create(ctx, ceiling)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ceiling_row(ctx, exec_id):
    with db_conn(ctx["db_url"]) as conn:
        return conn.execute(
            "SELECT ceiling_mode, ceiling_entries, ceiling_by, ceiling_at, updated_at "
            "FROM executions WHERE id = ?",
            (exec_id,),
        ).fetchone()


EGRESS = {"kind": "sandboxed", "mode": "egress", "entries": []}
NONE = {"kind": "sandboxed", "mode": "none", "entries": []}


def test_patch_widens_ceiling_with_one_event(shared):
    launch = _allowlist("github.com:443")
    exec_id = _create_sandboxed(shared, launch)["execution"]["id"]
    resp = _patch(shared, exec_id, {"network": EGRESS})
    assert resp.status_code == 200
    network = resp.json()["network"]
    assert network["ceiling"] == EGRESS
    assert network["by"] == "user"
    events = _ceiling_events(shared, exec_id)
    assert len(events) == 2
    data = events[-1]["payload"]["parts"][0]["data"]
    assert data == {
        "type": "ceiling_changed",
        "execution_id": exec_id,
        "ceiling": EGRESS,
        "previous": launch,
        "by": "user",
        "at": network["at"],
    }


def test_patch_narrows_ceiling_to_none(shared):
    launch = _allowlist("github.com:443", "pypi.org:443")
    exec_id = _create_sandboxed(shared, launch)["execution"]["id"]
    resp = _patch(shared, exec_id, {"network": NONE})
    assert resp.status_code == 200
    assert resp.json()["network"]["ceiling"] == NONE
    data = _ceiling_events(shared, exec_id)[-1]["payload"]["parts"][0]["data"]
    assert data["ceiling"] == NONE
    assert data["previous"] == launch


def test_patch_narrow_then_widen_chains_previous(shared):
    narrow = _allowlist("github.com:443")
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    assert _patch(shared, exec_id, {"network": narrow}).status_code == 200
    assert _patch(shared, exec_id, {"network": EGRESS}).status_code == 200
    datas = [e["payload"]["parts"][0]["data"] for e in _ceiling_events(shared, exec_id)]
    assert [d["ceiling"] for d in datas] == [EGRESS, narrow, EGRESS]
    assert [d["previous"] for d in datas] == [None, EGRESS, narrow]


def test_patch_same_ceiling_writes_nothing(shared):
    launch = _allowlist("pypi.org:443", "github.com:443")
    exec_id = _create_sandboxed(shared, launch)["execution"]["id"]
    before_row = _ceiling_row(shared, exec_id)
    before_events = len(_ceiling_events(shared, exec_id))
    resp = _patch(shared, exec_id, {"network": launch})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == exec_id
    assert body["network"]["ceiling"] == _allowlist("github.com:443", "pypi.org:443")
    assert len(_ceiling_events(shared, exec_id)) == before_events
    assert _ceiling_row(shared, exec_id) == before_row
    detail = httpx.get(f"{shared['url']}/api/v1/executions/{exec_id}", timeout=10)
    assert body["status"] == detail.json()["execution"]["status"]
    assert (
        body["completion_eligible"] == detail.json()["execution"]["completion_eligible"]
    )


def test_patch_returns_derived_fields_for_active_execution(shared):
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    resp = _patch(shared, exec_id, {"network": NONE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "working"
    assert body["completion_eligible"] is False
    detail = httpx.get(f"{shared['url']}/api/v1/executions/{exec_id}", timeout=10)
    assert body["status"] == detail.json()["execution"]["status"]
    assert (
        body["completion_eligible"] == detail.json()["execution"]["completion_eligible"]
    )


def test_patch_rejects_agent_session_bearer(shared):
    created = _create_sandboxed(shared, EGRESS)
    exec_id = created["execution"]["id"]
    resp = _patch(
        shared,
        exec_id,
        {"network": NONE},
        headers={"Authorization": f"Bearer {created['session_id']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "auth.forbidden"
    assert "not available via agent session auth" in resp.json()["detail"]
    assert len(_ceiling_events(shared, exec_id)) == 1
    detail = httpx.get(f"{shared['url']}/api/v1/executions/{exec_id}", timeout=10)
    assert detail.json()["execution"]["network"]["ceiling"] == EGRESS


def test_patch_after_terminate_is_refused(shared):
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    term = httpx.post(
        f"{shared['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
    )
    assert term.status_code == 200
    before_row = _ceiling_row(shared, exec_id)
    resp = _patch(shared, exec_id, {"network": NONE})
    assert resp.status_code == 409
    assert resp.json()["code"] == "execution.terminated"
    assert "the execution has ended" in resp.json()["detail"]
    assert _ceiling_row(shared, exec_id) == before_row
    assert len(_ceiling_events(shared, exec_id)) == 1


def test_patch_on_unsandboxed_execution_is_refused(shared):
    resp = _create(shared, omit_network=True)
    exec_id = resp.json()["execution"]["id"]
    resp = _patch(shared, exec_id, {"network": EGRESS})
    assert resp.status_code == 409
    assert resp.json()["code"] == "resource.conflict"
    assert "this execution is unsandboxed" in resp.json()["detail"]
    assert _ceiling_events(shared, exec_id) == []


def test_patch_unsandboxed_kind_is_refused(shared):
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    resp = _patch(shared, exec_id, {"network": {"kind": "unsandboxed"}})
    assert resp.status_code == 400
    assert resp.json()["code"] == "request.invalid"
    assert 'network.kind must be "sandboxed"' in resp.json()["detail"]
    assert len(_ceiling_events(shared, exec_id)) == 1


@pytest.mark.parametrize(
    "body",
    [{"title": "x"}, {"network": NONE, "title": "x"}],
)
def test_patch_rejects_other_fields(shared, body):
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    before_row = _ceiling_row(shared, exec_id)
    resp = _patch(shared, exec_id, body)
    assert resp.status_code == 400
    assert resp.json()["code"] == "request.invalid"
    assert "only network can be patched; unknown field: title" in resp.json()["detail"]
    assert _ceiling_row(shared, exec_id) == before_row
    assert len(_ceiling_events(shared, exec_id)) == 1


@pytest.mark.parametrize("body", [{}, {"network": None}])
def test_patch_rejects_missing_or_null_network(shared, body):
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    before_row = _ceiling_row(shared, exec_id)
    resp = _patch(shared, exec_id, body)
    assert resp.status_code == 400
    assert resp.json()["code"] == "request.invalid"
    assert resp.json()["detail"] == "network is required"
    assert _ceiling_row(shared, exec_id) == before_row
    assert len(_ceiling_events(shared, exec_id)) == 1


def test_patch_uses_the_ceiling_validator(shared):
    exec_id = _create_sandboxed(shared, EGRESS)["execution"]["id"]
    resp = _patch(shared, exec_id, {"network": _allowlist("x.com:0")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "request.invalid"
    assert body["detail"] == "1 network entry is invalid"
    assert [e["entry"] for e in body["entries"]] == ["x.com:0"]
    assert "outside 1-65535" in body["entries"][0]["reason"]


def _create_agent(ctx, sandbox_config):
    return httpx.post(
        f"{ctx['url']}/api/v1/agents",
        json={
            "name": f"endpoints-{uuid.uuid4().hex[:8]}",
            "driver_id": ensure_driver_via_api(ctx["url"]),
            "config": {"command": "echo", "args": ["test"], "timeout": 60},
            "sandbox_config": sandbox_config,
        },
        timeout=10,
    )


def _patch_agent(ctx, agent_id, body):
    return httpx.patch(f"{ctx['url']}/api/v1/agents/{agent_id}", json=body, timeout=10)


def _stored_sandbox_config(ctx, agent_id):
    with db_conn(ctx["db_url"]) as conn:
        raw = conn.execute(
            "SELECT sandbox_config FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()[0]
    return None if raw is None else json.loads(raw)


def test_agent_sandbox_config_uses_the_entry_validator(shared):
    stored = {"network_endpoints": ["api.example.com:443"]}
    agent_id = _create_agent(shared, stored).json()["id"]
    resp = _patch_agent(
        shared,
        agent_id,
        {
            "sandbox_config": {
                "network_endpoints": [
                    "bad_name:443",
                    "api.example.com:443",
                    "127.0.0.1:11434",
                ]
            }
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "request.invalid"
    assert body["detail"] == "2 network entries are invalid"
    assert [e["entry"] for e in body["entries"]] == ["bad_name:443", "127.0.0.1:11434"]
    assert "invalid host name" in body["entries"][0]["reason"]
    assert (
        "loopback addresses refer to the sandbox itself" in body["entries"][1]["reason"]
    )
    assert _stored_sandbox_config(shared, agent_id) == stored


def test_agent_sandbox_config_canonicalized_on_create_and_update(shared):
    resp = _create_agent(
        shared,
        {
            "network_endpoints": [
                "API.Example.com:443",
                "a.example.com:443",
                "api.example.com:443",
            ]
        },
    )
    assert resp.status_code == 201
    agent_id = resp.json()["id"]
    canonical = {"network_endpoints": ["a.example.com:443", "api.example.com:443"]}
    assert resp.json()["sandbox_config"] == canonical
    assert _stored_sandbox_config(shared, agent_id) == canonical

    resp = _patch_agent(
        shared,
        agent_id,
        {"sandbox_config": {"network_endpoints": ["Z.example.com:443"]}},
    )
    assert resp.status_code == 200
    assert resp.json()["sandbox_config"] == {"network_endpoints": ["z.example.com:443"]}
    assert _stored_sandbox_config(shared, agent_id) == {
        "network_endpoints": ["z.example.com:443"]
    }


SANDBOX_CONFIG_SHAPE = 'sandbox_config must be null or {"network_endpoints": [...]}'


@pytest.mark.parametrize(
    "value,detail",
    [
        ({}, SANDBOX_CONFIG_SHAPE),
        ({"network_endpoints": [], "x": 1}, SANDBOX_CONFIG_SHAPE),
        (
            {"network_endpoints": "a.com:443"},
            "sandbox_config.network_endpoints must be an array of strings",
        ),
        (
            {"network_endpoints": [443]},
            "sandbox_config.network_endpoints must be an array of strings",
        ),
        (["a.com:443"], SANDBOX_CONFIG_SHAPE),
        ("a.com:443", SANDBOX_CONFIG_SHAPE),
    ],
)
def test_agent_sandbox_config_rejects_other_shapes(shared, value, detail):
    resp = _create_agent(shared, value)
    assert resp.status_code == 400
    assert resp.json()["code"] == "request.invalid"
    assert resp.json()["detail"] == detail


def test_agent_sandbox_config_empty_list_accepted(shared):
    resp = _create_agent(shared, {"network_endpoints": []})
    assert resp.status_code == 201
    agent_id = resp.json()["id"]
    assert resp.json()["sandbox_config"] == {"network_endpoints": []}
    assert _stored_sandbox_config(shared, agent_id) == {"network_endpoints": []}


def test_agent_update_null_clears_sandbox_config(shared):
    agent_id = _create_agent(
        shared, {"network_endpoints": ["api.example.com:443"]}
    ).json()["id"]
    resp = _patch_agent(shared, agent_id, {"sandbox_config": None})
    assert resp.status_code == 200
    assert resp.json()["sandbox_config"] is None
    got = httpx.get(f"{shared['url']}/api/v1/agents/{agent_id}", timeout=10)
    assert got.json()["sandbox_config"] is None
    assert _stored_sandbox_config(shared, agent_id) is None


def test_agent_update_omitting_sandbox_config_keeps_it(shared):
    stored = {"network_endpoints": ["api.example.com:443"]}
    agent_id = _create_agent(shared, stored).json()["id"]
    resp = _patch_agent(shared, agent_id, {"name": f"renamed-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200
    assert resp.json()["sandbox_config"] == stored
    got = httpx.get(f"{shared['url']}/api/v1/agents/{agent_id}", timeout=10)
    assert got.json()["sandbox_config"] == stored
    assert _stored_sandbox_config(shared, agent_id) == stored
