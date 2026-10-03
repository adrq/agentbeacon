# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import subprocess

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)
from tests.contract.test_session_diff import get_root_session, make_git_repo


@DUAL_BACKEND
def test_an_oversized_diff_is_200_with_truncated(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="diff-truncated")
            project = create_project_via_api(ctx["url"], "diff-truncated", path=git_dir)
            exec_id, _session_id = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)

            with open(os.path.join(root["worktree_path"], "large.txt"), "w") as f:
                f.write("x" * 1_100_000 + "\n")
            subprocess.run(
                ["git", "-C", root["worktree_path"], "add", "large.txt"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=30
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/json")
            assert not resp.headers["content-type"].startswith(
                "application/problem+json"
            )

            data = resp.json()
            assert data["truncated"] is True
            assert "patch" not in data
            assert data["summary"] == {
                "files_changed": 1,
                "insertions": 1,
                "deletions": 0,
            }
            assert data["files"] == [
                {"path": "large.txt", "status": "A", "insertions": 1, "deletions": 0}
            ]
    finally:
        subprocess.run(["rm", "-rf", git_dir], check=False)


@DUAL_BACKEND
def test_a_normal_diff_is_untouched(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="diff-normal")
            project = create_project_via_api(ctx["url"], "diff-normal", path=git_dir)
            exec_id, _session_id = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)

            with open(os.path.join(root["worktree_path"], "small.txt"), "w") as f:
                f.write("hello\n")
            subprocess.run(
                ["git", "-C", root["worktree_path"], "add", "small.txt"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=30
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "truncated" not in data
            assert data["patch"]
    finally:
        subprocess.run(["rm", "-rf", git_dir], check=False)
