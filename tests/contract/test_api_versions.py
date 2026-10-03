# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import scheduler_context

KNOWN_REST_VERSIONS = ["v1.0"]


def _discovery(url):
    resp = httpx.get(f"{url}/api/versions", timeout=5)
    assert resp.status_code == 200, resp.text
    return resp


@DUAL_BACKEND
def test_discovery_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _discovery(ctx["url"])
        body = resp.json()

        assert body["discovery_version"] == 1
        assert set(body) == {"discovery_version", "server", "rest_versions", "limits"}
        assert set(body["server"]) == {"version"}
        assert isinstance(body["server"]["version"], str)
        assert "features" not in body


@DUAL_BACKEND
def test_rest_versions_are_append_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        served = _discovery(ctx["url"]).json()["rest_versions"]
        assert served[: len(KNOWN_REST_VERSIONS)] == KNOWN_REST_VERSIONS, (
            "rest_versions is append-only: existing entries never change order "
            "or disappear"
        )


@DUAL_BACKEND
def test_limits_cover_every_advertised_bound(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        limits = _discovery(ctx["url"]).json()["limits"]

        assert limits["events"] == {
            "default_page": 100,
            "max_page": 500,
            "string_leaf_max_bytes": 16384,
            "payload_budget_bytes": 65536,
        }
        assert limits["decisions"] == {
            "resolved_default_page": 50,
            "resolved_max_page": 200,
        }


@DUAL_BACKEND
def test_discovery_is_never_cached(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _discovery(ctx["url"])
        assert resp.headers["cache-control"] == "no-store"


@DUAL_BACKEND
def test_discovery_has_only_the_known_members(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        body = _discovery(ctx["url"]).json()
        known = {"discovery_version", "server", "rest_versions", "limits"}
        assert set(body) - known == set()
