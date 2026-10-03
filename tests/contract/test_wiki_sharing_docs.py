# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import scheduler_context


def fetch_docs(ctx):
    resp = httpx.get(f"{ctx['url']}/api/docs", timeout=5)
    assert resp.status_code == 200
    return resp.text


def section(docs, heading):
    marker = f"### {heading}\n"
    assert marker in docs, f"missing section {heading!r}"
    return docs.split(marker, 1)[1].split("\n### ", 1)[0]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_docs_document_the_search_endpoint(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        docs = fetch_docs(ctx)
        body = section(docs, "Search wiki")
        assert "GET /api/v1/wiki/search?q=" in body
        assert "project=" in body
        assert "/wiki/pages?q=" not in docs


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_docs_document_put_as_create_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        docs = fetch_docs(ctx)
        body = section(docs, "Create page")
        assert "PUT /api/v1/projects/{project_id}/wiki/pages/{slug}" in body
        assert '{"title": "Page Title", "body": "Page content"' in body
        assert "revision_number" not in body


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_docs_document_patch_as_the_only_update_verb(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        docs = fetch_docs(ctx)
        body = section(docs, "Edit page")
        assert "PATCH /api/v1/projects/{project_id}/wiki/pages/{slug}" in body
        assert '"revision_number"' in body
        assert '"add_tags"' in body
        assert '"remove_tags"' in body
        assert '"title": {"old"' in body
        assert "old_string" in body


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_docs_document_the_acknowledge_share_flow(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        docs = fetch_docs(ctx)
        body = section(docs, "Publishing a page to a share tag")
        assert '"error": "share_tag_requires_confirmation"' in body
        assert '"acknowledge_share": true' in body
        assert "all history travels with the page" in body


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_docs_document_cross_project_access_and_its_limits(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        docs = fetch_docs(ctx)
        body = section(docs, "Reading and editing another project's page")
        assert '"access"' in body
        assert "read_write" in body
        assert "add_tags" in body
        assert "DELETE" in body


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_docs_document_the_membership_admin_routes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        docs = fetch_docs(ctx)
        body = section(docs, "Share tag administration")
        assert "GET /api/v1/wiki/tags" in body
        assert "POST /api/v1/wiki/tags/{tag_id}/members" in body
        assert "PATCH /api/v1/wiki/tags/{tag_id}/members/{project}" in body
        assert "DELETE /api/v1/wiki/tags/{tag_id}/members/{project}" in body
        assert "operator_scope_only" in body
