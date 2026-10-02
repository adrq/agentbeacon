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


def make_git_repo(with_commit: bool = True) -> str:
    tmpdir = tempfile.mkdtemp()
    subprocess.run(["git", "init", tmpdir], check=True, capture_output=True)
    if with_commit:
        subprocess.run(
            [
                "git",
                "-C",
                tmpdir,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@test.com",
                "commit",
                "--allow-empty",
                "-m",
                "init",
            ],
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
def test_auto_worktree_git_project(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "git-project", path=git_dir)
            assert project["is_git"] is True

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None
            assert os.path.isdir(wt_path)

            assert f"/executions/{exec_id}/sessions/" in wt_path
            assert root["id"] in wt_path

            result = subprocess.run(
                ["git", "-C", wt_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == "HEAD"
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_worktree_non_git_project(test_database):
    tmpdir = tempfile.mkdtemp()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "non-git-project", path=tmpdir)
            assert project["is_git"] is False

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            assert root["worktree_path"] is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_worktree_empty_git_repo(test_database):
    git_dir = make_git_repo(with_commit=False)
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "empty-git", path=git_dir)
            assert project["is_git"] is True

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            assert root["worktree_path"] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicit_branch_still_works(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "branch-project", path=git_dir)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions",
                json={
                    "root_agent_id": agent_id,
                    "agent_ids": [agent_id],
                    "parts": [{"text": "test"}],
                    "project_id": project["id"],
                    "branch": "test-feature",
                },
                timeout=5,
            )
            assert resp.status_code == 201
            exec_id = resp.json()["execution"]["id"]

            root = get_root_session(ctx["url"], exec_id)
            assert root["worktree_path"] is not None

            result = subprocess.run(
                ["git", "-C", git_dir, "branch", "--list", "beacon/test-feature"],
                capture_output=True,
                text=True,
            )
            assert "beacon/test-feature" in result.stdout
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_detached_head_no_branch_created(test_database):
    git_dir = make_git_repo()
    try:
        before = subprocess.run(
            ["git", "-C", git_dir, "branch", "--list"],
            capture_output=True,
            text=True,
        )
        branches_before = before.stdout.strip()

        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "detach-project", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

        after = subprocess.run(
            ["git", "-C", git_dir, "branch", "--list"],
            capture_output=True,
            text=True,
        )
        branches_after = after.stdout.strip()
        assert branches_before == branches_after

        result = subprocess.run(
            ["git", "-C", wt_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "HEAD"
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_orphaned_worktree_on_early_validation_failure(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            project = create_project_via_api(
                ctx["url"], "cleanup-project", path=git_dir
            )

            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions",
                json={
                    "root_agent_id": "nonexistent-agent",
                    "agent_ids": ["nonexistent-agent"],
                    "parts": [{"text": "test"}],
                    "project_id": project["id"],
                },
                timeout=5,
            )
            assert resp.status_code == 400

            projects_dir = os.path.expanduser("~/.agentbeacon/projects")
            exec_dir = os.path.join(projects_dir, project["id"], "executions")
            if os.path.exists(exec_dir):
                entries = os.listdir(exec_dir)
                assert len(entries) == 0, f"orphaned worktree dirs: {entries}"
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_worktree_dir_handled(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "stale-project", path=git_dir)

            exec_id_1, _ = create_execution_via_api(
                ctx["url"], agent_id, "first", project_id=project["id"]
            )
            root1 = get_root_session(ctx["url"], exec_id_1)
            wt_path_1 = root1["worktree_path"]
            assert wt_path_1 is not None
            assert os.path.isdir(wt_path_1)

            exec_id_2, _ = create_execution_via_api(
                ctx["url"], agent_id, "second", project_id=project["id"]
            )
            root2 = get_root_session(ctx["url"], exec_id_2)
            wt_path_2 = root2["worktree_path"]
            assert wt_path_2 is not None
            assert wt_path_1 != wt_path_2
            assert os.path.isdir(wt_path_2)
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
