# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import random

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

BUDGET = 64 * 1024
LEAF_CAP = 16 * 1024


def _compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _insert_event(db_url, exec_id, session_id, payload, event_type="message"):
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, ?, ?)",
            (exec_id, session_id, event_type, json.dumps(payload)),
        )
        row = conn.execute(
            "SELECT MAX(id) FROM events WHERE execution_id = ?", (exec_id,)
        ).fetchone()
        conn.commit()
    return str(row[0])


def _served(url, exec_id, event_id):
    page = httpx.get(
        f"{url}/api/v1/executions/{exec_id}/events", params={"limit": 500}, timeout=30
    ).json()
    return next(e for e in page["items"] if e["id"] == event_id)


def _full(url, exec_id, event_id):
    resp = httpx.get(f"{url}/api/v1/executions/{exec_id}/events/{event_id}", timeout=30)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _skeleton(value, path=""):
    if isinstance(value, str):
        return "<str>"
    if isinstance(value, list):
        return [_skeleton(v) for v in value]
    if isinstance(value, dict):
        return {k: _skeleton(v) for k, v in sorted(value.items())}
    return value


def _strings(value, path="", out=None):
    if out is None:
        out = {}
    if isinstance(value, str):
        out[path] = value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _strings(v, f"{path}[{i}]", out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _strings(v, f"{path}.{k}" if path else k, out)
    return out


def _setup(ctx, name):
    agent_id = seed_test_agent(ctx["db_url"], name=name)
    return create_execution_via_api(ctx["url"], agent_id, "truncation")


def _one_dominant_leaf():
    return {
        "role": "ROLE_AGENT",
        "parts": [
            {"text": "x" * (300 * 1024)},
            {"data": {"type": "tool_call", "tool_call_id": "call-42", "status": "ok"}},
        ],
    }


def _many_medium_strings():
    return {
        "role": "ROLE_AGENT",
        "content": [{"type": "text", "text": "y" * 4000} for _ in range(40)],
        "usage": {"input_tokens": 1200, "output_tokens": 900},
    }


def _many_small_leaves():
    return {
        "role": "ROLE_AGENT",
        "parts": [{"text": "q" * 3000} for _ in range(400)],
    }


def _structure_heavy():
    return {
        "role": "ROLE_AGENT",
        "parts": [{f"k{i}{'p' * 40}": i} for i in range(1500)],
    }


def _logic_fields():
    return {
        "role": "ROLE_AGENT",
        "parts": [
            {
                "data": {
                    "type": "tool_call_update",
                    "tool_call_id": "call-7",
                    "status": "completed",
                    "is_error": False,
                    "executor_state": "idle",
                    "outcome": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                }
            },
            {"text": "z" * (200 * 1024)},
        ],
    }


NEAR_BUDGET_LOGIC = {
    "type": "tool_call_update",
    "tool_call_id": "call-7",
    "status": "completed",
    "session_id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
    "executor_state": "idle",
    "outcome": "completed",
}


def _near_budget_with_one_display_leaf():
    parts = []
    while True:
        parts.append(
            {
                "data": {
                    **NEAR_BUDGET_LOGIC,
                    "tool_call_id": f"call-{len(parts):05d}",
                }
            }
        )
        skeleton = {"role": "ROLE_AGENT", "parts": parts + [{"text": ""}]}
        if len(_compact(skeleton)) >= BUDGET - 200:
            break
    return {"role": "ROLE_AGENT", "parts": parts + [{"text": "w" * 4000}]}


def _short_logic_fields_over_budget():
    return {
        "role": "ROLE_AGENT",
        "parts": [
            {
                "data": {
                    "type": "tool_call_update",
                    "tool_call_id": f"call-{i:05d}",
                    "status": "completed",
                    "session_id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
                    "child_session_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "executor_state": "idle",
                    "outcome": "completed",
                }
            }
            for i in range(400)
        ],
    }


def _attachments():
    return {
        "role": "ROLE_USER",
        "parts": [
            {"text": "look at this"},
            {"raw": "QUJD" * (60 * 1024), "mimeType": "image/png", "name": "big.png"},
        ],
        "content": [
            {"type": "image", "source": {"type": "base64", "data": "ZZZZ" * 20000}}
        ],
    }


CORPORA = {
    "one_dominant_leaf": _one_dominant_leaf,
    "near_budget_with_one_display_leaf": _near_budget_with_one_display_leaf,
    "many_medium_strings": _many_medium_strings,
    "many_small_leaves": _many_small_leaves,
    "logic_fields": _logic_fields,
    "attachments": _attachments,
}


@DUAL_BACKEND
def test_structure_and_non_string_values_survive(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-structure")
        for name, build in CORPORA.items():
            original = build()
            event_id = _insert_event(test_database, exec_id, session_id, original)
            served = _served(ctx["url"], exec_id, event_id)["payload"]

            assert _skeleton(served) == _skeleton(original), name
            assert json.dumps(_skeleton(served), sort_keys=True) == json.dumps(
                _skeleton(original), sort_keys=True
            ), name


@DUAL_BACKEND
def test_output_is_valid_json_within_the_budget(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-budget")
        for name, build in CORPORA.items():
            event_id = _insert_event(test_database, exec_id, session_id, build())
            served = _served(ctx["url"], exec_id, event_id)
            body = json.dumps(
                served["payload"], separators=(",", ":"), ensure_ascii=False
            )
            assert json.loads(body) is not None
            assert len(body.encode()) <= BUDGET, (name, len(body))


@DUAL_BACKEND
def test_the_transform_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-idempotent")
        for name, build in CORPORA.items():
            event_id = _insert_event(test_database, exec_id, session_id, build())
            once = _served(ctx["url"], exec_id, event_id)["payload"]
            again_id = _insert_event(test_database, exec_id, session_id, once)
            twice = _served(ctx["url"], exec_id, again_id)["payload"]
            assert twice == once, name


@DUAL_BACKEND
def test_no_array_member_is_ever_dropped(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-arrays")
        original = _many_medium_strings()
        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)["payload"]
        assert len(served["content"]) == len(original["content"])
        for block in served["content"]:
            assert block["type"] == "text"


@DUAL_BACKEND
def test_logic_fields_pass_through_byte_identical(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-logic")
        original = _logic_fields()
        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)["payload"]
        assert served["parts"][0]["data"] == original["parts"][0]["data"]


@DUAL_BACKEND
def test_short_fields_survive_a_ceiling_driven_to_zero(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-near-budget")
        original = _near_budget_with_one_display_leaf()
        skeleton = json.loads(json.dumps(original))
        skeleton["parts"][-1]["text"] = ""
        assert len(_compact(skeleton)) < BUDGET
        assert len(_compact(original)) > BUDGET

        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        assert len(_compact(served["payload"])) <= BUDGET
        assert served["payload"]["parts"][-1]["text"] != original["parts"][-1]["text"]
        assert served["payload"]["role"] == "ROLE_AGENT"
        for index, part in enumerate(original["parts"][:-1]):
            assert served["payload"]["parts"][index]["data"] == part["data"], index


@DUAL_BACKEND
def test_short_fields_survive_when_the_payload_cannot_be_reduced(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-protected-only")
        original = _short_logic_fields_over_budget()
        assert len(_compact(original)) > BUDGET
        for text in _strings(original).values():
            assert len(text.encode()) <= 256, "a leaf is longer than 256 bytes"

        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        assert served["byte_size"] == len(json.dumps(original))
        assert served["payload"] == original


@DUAL_BACKEND
def test_base64_leaves_are_omitted_not_shortened(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-base64")
        original = _attachments()
        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        assert set(served["omitted_paths"]) == {
            "parts[1].raw",
            "content[0].source.data",
        }
        assert served["payload"]["parts"][1]["raw"] == ""
        assert served["payload"]["content"][0]["source"]["data"] == ""
        assert served["payload"]["parts"][1]["mimeType"] == "image/png"
        assert served["payload"]["parts"][1]["name"] == "big.png"


@DUAL_BACKEND
def test_small_payloads_are_untouched(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-small")
        original = {"role": "ROLE_AGENT", "parts": [{"text": "hello"}]}
        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)
        assert served["payload"] == original
        assert "truncated" not in served
        assert "byte_size" not in served
        assert "omitted_paths" not in served


@DUAL_BACKEND
def test_a_shortened_event_advertises_its_full_size(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-size")
        original = _one_dominant_leaf()
        stored_len = len(json.dumps(original))
        event_id = _insert_event(test_database, exec_id, session_id, original)

        served = _served(ctx["url"], exec_id, event_id)
        assert served["truncated"] is True
        assert served["byte_size"] == stored_len
        assert len(served["payload"]["parts"][0]["text"]) < len(
            original["parts"][0]["text"]
        )


@DUAL_BACKEND
def test_a_payload_of_many_small_leaves_still_fits(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-many-leaves")
        original = _many_small_leaves()
        assert len(json.dumps(original)) > 1_000_000

        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        body = json.dumps(served["payload"], separators=(",", ":"), ensure_ascii=False)
        assert len(body.encode()) <= BUDGET, len(body)
        assert len(served["payload"]["parts"]) == len(original["parts"])
        for part in served["payload"]["parts"]:
            assert "text" in part


@DUAL_BACKEND
def test_structure_heavy_payloads_are_served_whole(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-structure-heavy")
        original = _structure_heavy()
        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        assert served["byte_size"] == len(json.dumps(original))
        assert served["payload"] == original


@DUAL_BACKEND
def test_fetch_full_returns_the_exact_stored_payload(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-fetch-full")
        original = _one_dominant_leaf()
        event_id = _insert_event(test_database, exec_id, session_id, original)

        full = _full(ctx["url"], exec_id, event_id)
        assert full["payload"] == original
        assert "truncated" not in full
        assert "byte_size" not in full


@DUAL_BACKEND
def test_fetch_full_is_scoped_to_its_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_a, session_a = _setup(ctx, "trunc-scope-a")
        exec_b, _session_b = _setup(ctx, "trunc-scope-b")
        event_id = _insert_event(
            test_database, exec_a, session_a, {"role": "ROLE_AGENT", "parts": []}
        )

        urls = {
            "mismatched parent": f"{ctx['url']}/api/v1/executions/{exec_b}/events/{event_id}",
            "missing event": f"{ctx['url']}/api/v1/executions/{exec_a}/events/999999",
            "unparseable event": f"{ctx['url']}/api/v1/executions/{exec_a}/events/not-a-number",
            "missing parent": f"{ctx['url']}/api/v1/executions/exec-does-not-exist/events/{event_id}",
        }
        bodies = {}
        for name, url in urls.items():
            resp = httpx.get(url, timeout=15)
            assert resp.status_code == 404, f"{name}: {resp.status_code}"
            assert resp.headers["content-type"].startswith(
                "application/problem+json"
            ), f"{name}: {resp.headers['content-type']}"
            assert resp.json()["code"] == "resource.not_found", f"{name}: {resp.text}"
            bodies[name] = resp.json()

        distinct = {json.dumps(b, sort_keys=True) for b in bodies.values()}
        assert len(distinct) == 1, bodies


@DUAL_BACKEND
def test_randomized_payloads_hold_every_property(test_database):
    rng = random.Random(20260828)
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-randomized")
        for _ in range(12):
            parts = []
            for _ in range(rng.randint(1, 8)):
                kind = rng.choice(["text", "data", "raw"])
                if kind == "text":
                    parts.append(
                        {"text": rng.choice("abc€😀") * rng.randint(1, 90_000)}
                    )
                elif kind == "data":
                    parts.append(
                        {
                            "data": {
                                "type": "tool_call",
                                "tool_call_id": f"c{rng.randint(0, 999)}",
                                "n": rng.randint(0, 10_000),
                                "ok": rng.choice([True, False]),
                            }
                        }
                    )
                else:
                    parts.append({"raw": "QUJD" * rng.randint(1, 30_000)})
            original = {"role": "ROLE_AGENT", "parts": parts}

            event_id = _insert_event(test_database, exec_id, session_id, original)
            served = _served(ctx["url"], exec_id, event_id)

            assert _skeleton(served["payload"]) == _skeleton(original)
            body = json.dumps(
                served["payload"], separators=(",", ":"), ensure_ascii=False
            )
            if len(json.dumps(original)) > BUDGET:
                assert served["truncated"] is True
                assert len(body.encode()) <= BUDGET
            else:
                assert served["payload"] == original

            served_strings = _strings(served["payload"])
            original_strings = _strings(original)
            for path, value in served_strings.items():
                assert original_strings[path].startswith(value) or value == ""


def _lookalike_attachments():
    filler = "z" * 90000
    return {
        "role": "ROLE_USER",
        "parts": [
            {"raw": "QUJD" * 20000, "mediaType": "image/png", "filename": "real.png"},
            {
                "data": {
                    "type": "vendor_blob",
                    "parts": [{"raw": "keep-me-parts"}],
                    "content": [{"source": {"data": "keep-me-content"}}],
                }
            },
            {"text": filler},
        ],
        "content": [
            {"type": "image", "source": {"type": "base64", "data": "WFla" * 20000}}
        ],
    }


@DUAL_BACKEND
def test_only_root_level_attachments_are_emptied(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-lookalike")
        original = _lookalike_attachments()
        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert set(served["omitted_paths"]) == {
            "parts[0].raw",
            "content[0].source.data",
        }
        assert served["payload"]["parts"][0]["raw"] == ""
        assert served["payload"]["content"][0]["source"]["data"] == ""

        nested = served["payload"]["parts"][1]["data"]
        assert nested["parts"][0]["raw"] == "keep-me-parts"
        assert nested["content"][0]["source"]["data"] == "keep-me-content"
        assert nested["type"] == "vendor_blob"


@DUAL_BACKEND
def test_a_payload_that_reserializes_under_budget_is_not_truncated(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-reserialize")
        original = {
            "role": "ROLE_AGENT",
            "parts": [{"text": "a\tb"} for _ in range(2000)],
        }
        stored = json.dumps(original, indent=4)
        assert len(stored) > BUDGET, len(stored)
        assert len(_compact(original)) < BUDGET, len(_compact(original))

        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, stored),
            )
            row = conn.execute(
                "SELECT MAX(id) FROM events WHERE execution_id = ?", (exec_id,)
            ).fetchone()
            conn.commit()
        event_id = str(row[0])

        served = _served(ctx["url"], exec_id, event_id)
        assert served["payload"] == original
        assert "truncated" not in served
        assert "byte_size" not in served
        assert "omitted_paths" not in served


@DUAL_BACKEND
def test_escape_heavy_text_is_measured_after_escaping(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-escapes")
        original = {
            "role": "ROLE_AGENT",
            "parts": [{"text": '"\\\n\t' * 10_000} for _ in range(3)],
        }
        assert len(_compact(original).encode()) > BUDGET

        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        body = _compact(served["payload"])
        assert len(body.encode()) <= BUDGET, len(body.encode())
        served_strings = _strings(served["payload"])
        original_strings = _strings(original)
        for path, kept in served_strings.items():
            assert original_strings[path].startswith(kept), path


@DUAL_BACKEND
def test_multibyte_text_is_cut_on_character_boundaries(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup(ctx, "trunc-multibyte")
        original = {
            "role": "ROLE_AGENT",
            "parts": [{"text": "😀" * 20_000}, {"text": "é" * 20_000}],
        }
        assert len(_compact(original).encode()) > BUDGET

        event_id = _insert_event(test_database, exec_id, session_id, original)
        served = _served(ctx["url"], exec_id, event_id)

        assert served["truncated"] is True
        body = _compact(served["payload"])
        assert len(body.encode()) <= BUDGET, len(body.encode())
        served_strings = _strings(served["payload"])
        original_strings = _strings(original)
        for path, kept in served_strings.items():
            assert original_strings[path].startswith(kept), path
            assert kept.encode().decode() == kept, path
