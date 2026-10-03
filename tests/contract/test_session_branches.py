# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import shutil
import subprocess
import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)


def make_git_repo_with_branches() -> str:
    tmpdir = tempfile.mkdtemp()
    subprocess.run(
        ["git", "init", "-b", "main", tmpdir], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", tmpdir, "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    with open(os.path.join(tmpdir, "README.md"), "w") as f:
        f.write("# Test\n")
    subprocess.run(["git", "-C", tmpdir, "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", tmpdir, "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "branch", "feature/foo"],
        check=True,
        capture_output=True,
    )
    return tmpdir


def get_root_session(url: str, exec_id: str) -> dict:
    resp = httpx.get(f"{url}/api/v1/executions/{exec_id}", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    root = next(s for s in data["sessions"] if s["parent_session_id"] is None)
    return root


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_branches_lists_local_branches(test_database):
    git_dir = make_git_repo_with_branches()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "branches-project", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/branches",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            branch_names = [b["name"] for b in data["branches"]]
            assert "main" in branch_names
            assert "feature/foo" in branch_names

            main_branch = next(b for b in data["branches"] if b["name"] == "main")
            assert main_branch["is_default"] is True

            feature_branch = next(
                b for b in data["branches"] if b["name"] == "feature/foo"
            )
            assert feature_branch["is_default"] is False

            assert "current_branch" in data
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_branches_no_worktree(test_database):
    tmpdir = tempfile.mkdtemp()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", cwd=tmpdir
            )

            root = get_root_session(ctx["url"], exec_id)
            assert root["worktree_path"] is None

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/branches",
                timeout=10,
            )
            assert resp.status_code == 400
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_branches_default_heuristic_master(test_database):
    tmpdir = tempfile.mkdtemp()
    subprocess.run(
        ["git", "init", "-b", "master", tmpdir], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", tmpdir, "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    with open(os.path.join(tmpdir, "README.md"), "w") as f:
        f.write("# Test\n")
    subprocess.run(["git", "-C", tmpdir, "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", tmpdir, "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "branches-master", path=tmpdir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/branches",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            master_branch = next(
                (b for b in data["branches"] if b["name"] == "master"), None
            )
            assert master_branch is not None
            assert master_branch["is_default"] is True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_branches_detached_head_no_bogus_entry(test_database):
    git_dir = make_git_repo_with_branches()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "branches-detached", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

            head_sha = subprocess.run(
                ["git", "-C", wt_path, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", wt_path, "checkout", head_sha],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/branches",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            branch_names = [b["name"] for b in data["branches"]]
            for name in branch_names:
                assert "HEAD" not in name, f"bogus branch entry: {name}"

            assert data["current_branch"] is None

            assert "main" in branch_names
            assert "feature/foo" in branch_names
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_branches_excludes_beacon_scratch_branches(test_database):
    git_dir = make_git_repo_with_branches()
    subprocess.run(
        ["git", "-C", git_dir, "branch", "beacon/session-abc123"],
        check=True,
        capture_output=True,
    )
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "branches-beacon", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/branches",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            branch_names = [b["name"] for b in data["branches"]]
            for name in branch_names:
                assert not name.startswith("beacon/"), f"internal branch leaked: {name}"
            assert "main" in branch_names
            assert "feature/foo" in branch_names
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
