"""Contract tests for GET/DELETE /api/sessions/{id}/worktree endpoints."""

import os
import shutil
import subprocess
import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
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


def get_root_session(url: str, exec_id: str) -> dict:
    """Fetch execution detail and return the root session."""
    resp = httpx.get(f"{url}/api/executions/{exec_id}", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    root = next(s for s in data["sessions"] if s["parent_session_id"] is None)
    return root


# --- GET /api/sessions/{id}/worktree ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_with_worktree(test_database):
    """GET worktree returns 200 with path, exists=true, head_sha for auto-worktree."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-info", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]
            assert root["worktree_path"] is not None

            resp = httpx.get(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["path"] == root["worktree_path"]
            assert data["exists"] is True
            assert data["head_sha"] is not None
            # Auto-worktree uses detached HEAD
            assert data["branch"] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_named_branch(test_database):
    """GET worktree returns branch name for named-branch worktree."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-branch", path=git_dir)

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
            exec_id = resp.json()["execution"]["id"]

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            resp = httpx.get(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["exists"] is True
            assert data["branch"] is not None
            assert data["branch"].startswith("beacon/")
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_no_worktree_returns_404(test_database):
    """GET worktree returns 404 for session without worktree_path."""
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
                f"{ctx['url']}/api/sessions/{root['id']}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_nonexistent_session_returns_404(test_database):
    """GET worktree returns 404 for nonexistent session."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/sessions/bogus-id/worktree", timeout=10)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_deleted_directory(test_database):
    """GET worktree returns 200 with exists=false when dir is gone."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-deleted", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

            # Delete the directory externally
            shutil.rmtree(wt_path)

            resp = httpx.get(
                f"{ctx['url']}/api/sessions/{root['id']}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["exists"] is False
            assert data["branch"] is None
            assert data["head_sha"] is None
            assert data["path"] == wt_path
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


# --- DELETE /api/sessions/{id}/worktree ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_on_terminal_session(test_database):
    """DELETE worktree on canceled session returns 200 and removes dir."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-delete", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]
            wt_path = root["worktree_path"]
            assert wt_path is not None
            assert os.path.isdir(wt_path)

            # Cancel to reach terminal state
            cancel_resp = httpx.post(
                f"{ctx['url']}/api/sessions/{session_id}/cancel", timeout=5
            )
            assert cancel_resp.status_code == 200

            # DELETE worktree
            resp = httpx.delete(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["path"] == wt_path

            # Directory should be gone
            assert not os.path.isdir(wt_path)

            # Subsequent GET worktree should return 404 (no worktree)
            resp = httpx.get(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_non_terminal_rejects(test_database):
    """DELETE worktree on non-terminal session returns 409."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-reject", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            resp = httpx.delete(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 409
            assert "terminal" in resp.json()["error"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_no_worktree_returns_404(test_database):
    """DELETE worktree on session without worktree returns 404."""
    tmpdir = tempfile.mkdtemp()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", cwd=tmpdir
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            # Cancel to reach terminal state
            cancel_resp = httpx.post(
                f"{ctx['url']}/api/sessions/{session_id}/cancel", timeout=5
            )
            assert cancel_resp.status_code == 200

            resp = httpx.delete(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_already_deleted_directory(test_database):
    """DELETE worktree succeeds even if directory already gone (clears DB)."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-gone", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]
            wt_path = root["worktree_path"]
            assert wt_path is not None

            # Cancel to reach terminal state
            cancel_resp = httpx.post(
                f"{ctx['url']}/api/sessions/{session_id}/cancel", timeout=5
            )
            assert cancel_resp.status_code == 200

            # Delete directory manually before calling API
            shutil.rmtree(wt_path, ignore_errors=True)

            resp = httpx.delete(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_clears_db_column(test_database):
    """DELETE worktree sets worktree_path to NULL in DB."""
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-db", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            # Verify worktree_path is set
            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT worktree_path FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            assert row[0] is not None

            # Cancel to reach terminal state
            cancel_resp = httpx.post(
                f"{ctx['url']}/api/sessions/{session_id}/cancel", timeout=5
            )
            assert cancel_resp.status_code == 200

            # DELETE worktree
            resp = httpx.delete(
                f"{ctx['url']}/api/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200

            # Verify worktree_path is NULL
            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT worktree_path FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            assert row[0] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
