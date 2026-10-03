# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import httpx

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    seed_test_agent,
)

WIKI_TAGS_ROOT = "/api/v1/wiki/tags"
WIKI_SEARCH_ROUTE = "/api/v1/wiki/search"

TAG_SEED_BODY = "tag seed page, not shared"


def bearer(session_id: str = None) -> dict:
    return {"Authorization": f"Bearer {session_id}"} if session_id else None


def set_config_flag(url: str, name: str, value: bool) -> httpx.Response:
    return httpx.post(
        f"{url}/api/v1/config",
        json={"name": name, "value": "true" if value else "false"},
        timeout=5,
    )


def list_wiki_tags(url: str, session_id: str = None) -> httpx.Response:
    return httpx.get(f"{url}{WIKI_TAGS_ROOT}", headers=bearer(session_id), timeout=5)


def find_wiki_tag(url: str, name: str) -> dict:
    resp = list_wiki_tags(url)
    assert resp.status_code == 200, resp.text
    for entry in resp.json():
        if entry["tag"] == name:
            return entry
    return None


def tag_id_for(url: str, name: str) -> str:
    entry = find_wiki_tag(url, name)
    assert entry is not None, f"tag {name!r} does not exist"
    return entry["tag_id"]


def seed_tag(url: str, name: str, project: str = None) -> str:
    if project is None:
        project = create_project_via_api(url, f"tag-seed-{name}")["id"]
    resp = wiki_put(
        url, project, f"seed-{name}", f"Seed {name}", TAG_SEED_BODY, tags=[name]
    )
    assert resp.status_code == 201, f"seeding tag {name!r}: {resp.text}"
    return project


def admit_member(
    url: str,
    tag_id: str,
    project: str,
    access_level: str,
    acknowledge_share: bool = None,
) -> httpx.Response:
    payload = {"project": project, "access_level": access_level}
    if acknowledge_share is not None:
        payload["acknowledge_share"] = acknowledge_share
    return httpx.post(
        f"{url}{WIKI_TAGS_ROOT}/{tag_id}/members", json=payload, timeout=5
    )


def set_member_access(
    url: str,
    tag_id: str,
    project: str,
    access_level: str,
    acknowledge_share: bool = None,
) -> httpx.Response:
    payload = {"access_level": access_level}
    if acknowledge_share is not None:
        payload["acknowledge_share"] = acknowledge_share
    return httpx.patch(
        f"{url}{WIKI_TAGS_ROOT}/{tag_id}/members/{project}", json=payload, timeout=5
    )


def revoke_member(url: str, tag_id: str, project: str) -> httpx.Response:
    return httpx.delete(f"{url}{WIKI_TAGS_ROOT}/{tag_id}/members/{project}", timeout=5)


def make_share_tag(url: str, name: str, members: dict, acknowledge_share=True) -> str:
    seed_tag(url, name)
    tag_id = tag_id_for(url, name)
    for project, access_level in members.items():
        resp = admit_member(
            url, tag_id, project, access_level, acknowledge_share=acknowledge_share
        )
        assert resp.status_code in (200, 201), (
            f"admit {project} to {name} failed: {resp.status_code} {resp.text}"
        )
    return tag_id


def wiki_put(
    url: str,
    project: str,
    slug: str,
    title: str,
    body: str,
    tags: list = None,
    summary: str = None,
    acknowledge_share: bool = None,
    session_id: str = None,
    revision_number: int = None,
) -> httpx.Response:
    payload = {"title": title, "body": body}
    if tags is not None:
        payload["tags"] = tags
    if summary is not None:
        payload["summary"] = summary
    if acknowledge_share is not None:
        payload["acknowledge_share"] = acknowledge_share
    if revision_number is not None:
        payload["revision_number"] = revision_number
    return httpx.put(
        f"{url}/api/v1/projects/{project}/wiki/pages/{slug}",
        json=payload,
        headers=bearer(session_id),
        timeout=5,
    )


def wiki_patch(
    url: str,
    project: str,
    slug: str,
    revision_number: int,
    edits: list = None,
    title: dict = None,
    add_tags: list = None,
    remove_tags: list = None,
    acknowledge_share: bool = None,
    summary: str = None,
    session_id: str = None,
) -> httpx.Response:
    payload = {"revision_number": revision_number}
    if edits is not None:
        payload["edits"] = edits
    if title is not None:
        payload["title"] = title
    if add_tags is not None:
        payload["add_tags"] = add_tags
    if remove_tags is not None:
        payload["remove_tags"] = remove_tags
    if acknowledge_share is not None:
        payload["acknowledge_share"] = acknowledge_share
    if summary is not None:
        payload["summary"] = summary
    return httpx.patch(
        f"{url}/api/v1/projects/{project}/wiki/pages/{slug}",
        json=payload,
        headers=bearer(session_id),
        timeout=5,
    )


def wiki_replace_body(
    url: str,
    project: str,
    slug: str,
    revision_number: int,
    old_body: str,
    new_body: str,
    session_id: str = None,
    **kwargs,
) -> httpx.Response:
    return wiki_patch(
        url,
        project,
        slug,
        revision_number,
        edits=[{"old_string": old_body, "new_string": new_body}],
        session_id=session_id,
        **kwargs,
    )


def wiki_get(
    url: str, project: str, slug: str, session_id: str = None
) -> httpx.Response:
    return httpx.get(
        f"{url}/api/v1/projects/{project}/wiki/pages/{slug}",
        headers=bearer(session_id),
        timeout=5,
    )


def wiki_search(url: str, session_id: str = None, **params) -> httpx.Response:
    return httpx.get(
        f"{url}{WIKI_SEARCH_ROUTE}",
        params=params,
        headers=bearer(session_id),
        timeout=5,
    )


def membership_rows(db_url: str, tag_name: str, project_id: str) -> list:
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT m.id, m.access_level, m.revoked_at "
            "FROM wiki_tag_members m JOIN wiki_tags t ON t.id = m.tag_id "
            "WHERE t.name = ? AND m.project_id = ? ORDER BY m.created_at",
            (tag_name, project_id),
        ).fetchall()
    return [tuple(row) for row in rows]


def untag_page_in_db(db_url: str, project_id: str, slug: str, tag_name: str) -> None:
    with db_conn(db_url) as conn:
        conn.execute(
            "DELETE FROM wiki_page_tags WHERE tag_id = "
            "(SELECT id FROM wiki_tags WHERE name = ?) AND page_id = "
            "(SELECT id FROM wiki_pages WHERE slug = ? AND project_id = ? "
            "AND deleted_at IS NULL)",
            (tag_name, slug, project_id),
        )
        conn.commit()


def revoke_membership_in_db(db_url: str, tag_name: str, project_id: str) -> None:
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE wiki_tag_members SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE tag_id = (SELECT id FROM wiki_tags WHERE name = ?) "
            "AND project_id = ? AND revoked_at IS NULL",
            (tag_name, project_id),
        )
        conn.commit()


SHARE_TAG = "api-contract"

CROSS_PROJECT_WRITES_FLAG = "wiki.cross_project_writes"

ERR_CROSS_PROJECT_COLLECTION = "cross_project_collection"
ERR_CROSS_PROJECT_CREATE = "cross_project_create"
ERR_READ_ONLY_MEMBER = "read_only_member"
ERR_NOT_PAGE_OWNER = "not_page_owner"


def build_world(ctx):
    agent_id = seed_test_agent(ctx["db_url"], name="matrix-agent")
    projects = {}
    sessions = {}
    for name in ["owner", "read_member", "write_member", "outsider"]:
        project = create_project_via_api(ctx["url"], f"matrix-{name.replace('_', '-')}")
        projects[name] = project
        _, session_id = create_execution_via_api(
            ctx["url"], agent_id, "matrix", project_id=project["id"]
        )
        sessions[name] = session_id
    sessions["anon"] = None

    tag_id = make_share_tag(
        ctx["url"],
        SHARE_TAG,
        {
            projects["owner"]["id"]: "read_write",
            projects["read_member"]["id"]: "read",
            projects["write_member"]["id"]: "read_write",
        },
    )
    return projects, sessions, tag_id


def materialize_page(url, project_id, slug, tagged, session_id=None):
    resp = wiki_put(
        url,
        project_id,
        slug,
        "Matrix Page",
        "original body",
        tags=[SHARE_TAG] if tagged else None,
        acknowledge_share=True if tagged else None,
        session_id=session_id,
    )
    assert resp.status_code == 201, f"materializing {slug}: {resp.text}"
    resp = wiki_patch(
        url,
        project_id,
        slug,
        1,
        edits=[{"old_string": "original body", "new_string": "matrix body text"}],
        session_id=session_id,
    )
    assert resp.status_code == 200, f"seeding revision for {slug}: {resp.text}"
