"""Contract tests for auto-worktree creation on execution."""

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
    """Create a temporary git repo, optionally with an initial commit."""
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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_auto_worktree_git_project(test_database):
    """Execution against git project auto-creates detached HEAD worktree."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "git-project", path=git_dir)
            assert project["is_git"] is True

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
            data = resp.json()
            wt_path = data["execution"]["worktree_path"]
            assert wt_path is not None
            assert os.path.isdir(wt_path)

            # Verify detached HEAD
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
    """Execution against non-git project uses project path directly."""
    tmpdir = tempfile.mkdtemp()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "non-git-project", path=tmpdir)
            assert project["is_git"] is False

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
            data = resp.json()
            assert data["execution"]["worktree_path"] is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_worktree_empty_git_repo(test_database):
    """Empty git repo (no commits) falls back to project path."""
    git_dir = make_git_repo(with_commit=False)
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "empty-git", path=git_dir)
            # is_git is true because .git dir exists
            assert project["is_git"] is True

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
            data = resp.json()
            # No worktree because no commits to detach from
            assert data["execution"]["worktree_path"] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicit_branch_still_works(test_database):
    """Explicit branch parameter creates named branch worktree."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "branch-project", path=git_dir)

            resp = httpx.post(
                f"{ctx['url']}/api/executions",
                json={
                    "agent_id": agent_id,
                    "prompt": "test",
                    "project_id": project["id"],
                    "branch": "test-feature",
                },
                timeout=5,
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["execution"]["worktree_path"] is not None

            # Verify named branch exists
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
    """Auto-worktree uses detached HEAD -- no new branch is created."""
    git_dir = make_git_repo()
    try:
        # List branches before
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

            resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
            data = resp.json()
            wt_path = data["execution"]["worktree_path"]
            assert wt_path is not None

        # List branches after
        after = subprocess.run(
            ["git", "-C", git_dir, "branch", "--list"],
            capture_output=True,
            text=True,
        )
        branches_after = after.stdout.strip()
        assert branches_before == branches_after

        # Verify detached HEAD in worktree
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
    """Pre-validation errors don't leave orphaned worktree directories.

    NOTE: This tests the early-failure path (invalid agent_id) which fails
    before worktree creation. The post-worktree cleanup path (lines 189-194
    of execution.rs) requires a failure in persist_and_enqueue which is
    difficult to trigger from a contract test. The post-worktree cleanup
    is covered by code review verification.
    """
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            project = create_project_via_api(
                ctx["url"], "cleanup-project", path=git_dir
            )

            # Use invalid agent_id to trigger early validation failure (before worktree creation)
            resp = httpx.post(
                f"{ctx['url']}/api/executions",
                json={
                    "agent_id": "nonexistent-agent",
                    "prompt": "test",
                    "project_id": project["id"],
                },
                timeout=5,
            )
            assert resp.status_code == 400

            # Verify no orphaned worktree directories remain
            projects_dir = os.path.expanduser("~/.agentbeacon/projects")
            exec_dir = os.path.join(projects_dir, project["id"], "executions")
            if os.path.exists(exec_dir):
                entries = os.listdir(exec_dir)
                assert len(entries) == 0, f"orphaned worktree dirs: {entries}"
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_worktree_dir_handled(test_database):
    """Multiple concurrent worktrees work for the same project.

    NOTE: The stale-directory cleanup path (create_worktree lines 317-322)
    requires a pre-existing directory at the exact UUID-based path, which
    cannot be triggered via the API. This test validates that multiple
    worktrees coexist correctly for the same project.
    """
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "stale-project", path=git_dir)

            # Create first execution to get a worktree
            exec_id_1, _ = create_execution_via_api(
                ctx["url"], agent_id, "first", project_id=project["id"]
            )
            resp1 = httpx.get(f"{ctx['url']}/api/executions/{exec_id_1}", timeout=5)
            wt_path_1 = resp1.json()["execution"]["worktree_path"]
            assert wt_path_1 is not None
            assert os.path.isdir(wt_path_1)

            # Create second execution (different UUID, different worktree path)
            exec_id_2, _ = create_execution_via_api(
                ctx["url"], agent_id, "second", project_id=project["id"]
            )
            resp2 = httpx.get(f"{ctx['url']}/api/executions/{exec_id_2}", timeout=5)
            wt_path_2 = resp2.json()["execution"]["worktree_path"]
            assert wt_path_2 is not None
            assert wt_path_1 != wt_path_2
            assert os.path.isdir(wt_path_2)
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
