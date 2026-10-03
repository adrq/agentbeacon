# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import concurrent.futures
import random
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
from tests.sse_helpers import (
    _persisted,
    _position,
    _stream_sse_events,
)

SEEDS = [20260828, 990001, 5]


def _history(url, exec_id, before=None, limit=500):
    params = {"limit": limit}
    if before is not None:
        params["before"] = before
    resp = httpx.get(
        f"{url}/api/v1/executions/{exec_id}/events", params=params, timeout=30
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _page_back(url, exec_id, before, floor=None):
    out = []
    cursor = before
    for _ in range(50):
        page = _history(url, exec_id, before=cursor, limit=25)
        out = page["items"] + out
        if floor is not None and any(int(e["id"]) <= floor for e in page["items"]):
            break
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    return out


def _committed(db_url, exec_id):
    with db_conn(db_url) as conn:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT id FROM events WHERE execution_id = ? ORDER BY id ASC",
                (exec_id,),
            ).fetchall()
        ]


def _one_round(ctx, db_url, exec_id, session_id, rng, drop=None, duplicate=None):
    live = []
    done = threading.Event()

    def stream():
        live.extend(
            _stream_sse_events(
                f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                timeout=rng.uniform(2.0, 4.0),
            )
        )
        done.set()

    threading.Thread(target=stream, daemon=True).start()

    writers = rng.randint(1, 4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=writers) as pool:
        futures = []
        for i in range(writers):
            time.sleep(rng.uniform(0.0, 0.25))
            futures.append(
                pool.submit(
                    rest_escalate_ok,
                    ctx["url"],
                    session_id,
                    {"questions": [{"question": f"w{i}?"}]},
                )
            )
        for f in futures:
            f.result()

    assert done.wait(timeout=30)

    seam = _position(live)
    walked = [e["id"] for e in _page_back(ctx["url"], exec_id, seam["history_before"])]
    cutoff = set(_committed(db_url, exec_id))

    sequence = walked + [frame["data"]["id"] for frame in _persisted(live)]
    if drop is not None and drop in sequence:
        sequence = [i for i in sequence if i != drop]
    if duplicate is not None and duplicate in sequence:
        sequence = sequence + [duplicate]
    return sequence, cutoff


def _assert_round(sequence, cutoff, label):
    assert len(sequence) == len(set(sequence)), (
        f"{label}: the same row was rendered twice — "
        f"{sorted(i for i in set(sequence) if sequence.count(i) > 1)}"
    )
    assert set(sequence) == cutoff, (
        f"{label}: missing {sorted(cutoff - set(sequence))}, "
        f"invented {sorted(set(sequence) - cutoff)}"
    )


@DUAL_BACKEND
def test_repeated_splices_never_gap_or_duplicate(test_database):
    for seed in SEEDS:
        rng = random.Random(seed)
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name=f"splice-{seed}")
            exec_id, session_id = create_execution_via_api(
                ctx["url"], agent_id, "splice"
            )

            accumulated = []
            for round_index in range(4):
                sequence, cutoff = _one_round(
                    ctx, test_database, exec_id, session_id, rng
                )
                _assert_round(sequence, cutoff, f"seed {seed} round {round_index}")
                accumulated.append((sequence, cutoff))

            for index, (sequence, cutoff) in enumerate(accumulated):
                _assert_round(sequence, cutoff, f"seed {seed} accumulated {index}")


@DUAL_BACKEND
def test_the_dedupe_key_is_the_opaque_id(test_database):
    rng = random.Random(4242)
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="splice-dedupe")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "splice")
        for i in range(12):
            rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )

        sequence, cutoff = _one_round(ctx, test_database, exec_id, session_id, rng)
        assert all(isinstance(i, str) for i in sequence)
        assert set(sequence) == cutoff


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_the_property_assertions_can_actually_fail(test_database):
    rng = random.Random(31337)
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="splice-teeth")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "splice")
        for i in range(4):
            rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"T{i}?"}]}
            )

        sequence, cutoff = _one_round(ctx, test_database, exec_id, session_id, rng)
        _assert_round(sequence, cutoff, "clean")
        assert sequence, "the round rendered nothing to drop"

        victim = sequence[0]
        dropped, cutoff_d = _one_round(
            ctx, test_database, exec_id, session_id, rng, drop=victim
        )
        with pytest.raises(AssertionError, match="missing"):
            _assert_round(dropped, cutoff_d, "dropped")

        duplicated, cutoff_u = _one_round(
            ctx, test_database, exec_id, session_id, rng, duplicate=victim
        )
        with pytest.raises(AssertionError, match="rendered twice"):
            _assert_round(duplicated, cutoff_u, "duplicated")
