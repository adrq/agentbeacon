# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import concurrent.futures
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


def terminate_session(url: str, session_id: str):
    resp = httpx.post(f"{url}/api/v1/sessions/{session_id}/terminate", timeout=5)
    assert resp.status_code == 200


def create_branch_execution(ctx: dict, git_dir: str, branch_name: str):
    agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
    project = create_project_via_api(ctx["url"], f"wt-{branch_name}", path=git_dir)
    resp = httpx.post(
        f"{ctx['url']}/api/v1/executions",
        json={
            "root_agent_id": agent_id,
            "agent_ids": [agent_id],
            "parts": [{"text": "test"}],
            "project_id": project["id"],
            "branch": branch_name,
        },
        timeout=5,
    )
    assert resp.status_code == 201
    exec_id = resp.json()["execution"]["id"]
    root = get_root_session(ctx["url"], exec_id)
    return exec_id, root["id"], root


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_with_worktree(test_database):
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
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["path"] == root["worktree_path"]
            assert data["exists"] is True
            assert data["head_sha"] is not None
            assert data["branch"] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_named_branch(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-branch", path=git_dir)

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
            session_id = root["id"]

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
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
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_nonexistent_session_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/bogus-id/worktree", timeout=10)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worktree_info_deleted_directory(test_database):
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

            shutil.rmtree(wt_path)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["exists"] is False
            assert data["branch"] is None
            assert data["head_sha"] is None
            assert data["path"] == wt_path
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_on_terminal_session(test_database):
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

            cancel_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
            )
            assert cancel_resp.status_code == 200

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["path"] == wt_path

            assert not os.path.isdir(wt_path)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_non_terminal_rejects(test_database):
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
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 409
            assert "terminal" in resp.json()["error"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_no_worktree_returns_404(test_database):
    tmpdir = tempfile.mkdtemp()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", cwd=tmpdir
            )

            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            cancel_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
            )
            assert cancel_resp.status_code == 200

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_already_deleted_directory(test_database):
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

            cancel_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
            )
            assert cancel_resp.status_code == 200

            shutil.rmtree(wt_path, ignore_errors=True)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_clears_db_column(test_database):
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

            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT worktree_path FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            assert row[0] is not None

            cancel_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
            )
            assert cancel_resp.status_code == 200

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200

            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT worktree_path FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            assert row[0] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_dry_run(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            _, session_id, root = create_branch_execution(ctx, git_dir, "dry-run-test")
            wt_path = root["worktree_path"]
            assert wt_path is not None

            with open(os.path.join(wt_path, "dirty.txt"), "w") as f:
                f.write("dirty")

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree?dry_run=true",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is False
            assert data["dry_run"] is True
            assert data["dirty"] is True
            assert "untracked" in data["dirty_summary"]
            assert data["branch"] is not None
            assert data["branch"].startswith("beacon/")
            assert data["directory_missing"] is False

            assert os.path.isdir(wt_path)
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_dry_run_missing_directory(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-dry-miss", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]
            wt_path = root["worktree_path"]

            shutil.rmtree(wt_path)

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree?dry_run=true",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is False
            assert data["dry_run"] is True
            assert data["dirty"] is None
            assert data["dirty_summary"] is None
            assert data["branch"] is None
            assert data["directory_missing"] is True
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_with_branch_deletion(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            _, session_id, root = create_branch_execution(ctx, git_dir, "branch-del")
            wt_path = root["worktree_path"]

            branch_out = subprocess.run(
                ["git", "-C", wt_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            )
            branch_name = branch_out.stdout.strip()
            assert branch_name.startswith("beacon/")

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree?delete_branch=true",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["branch_deleted"] is True

            branch_check = subprocess.run(
                ["git", "-C", git_dir, "branch", "--list", branch_name],
                capture_output=True,
                text=True,
            )
            assert branch_name not in branch_check.stdout
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_without_branch_deletion(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            _, session_id, root = create_branch_execution(ctx, git_dir, "branch-keep")
            wt_path = root["worktree_path"]

            branch_out = subprocess.run(
                ["git", "-C", wt_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            )
            branch_name = branch_out.stdout.strip()

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["branch_deleted"] is False

            branch_check = subprocess.run(
                ["git", "-C", git_dir, "branch", "--list", branch_name],
                capture_output=True,
                text=True,
            )
            assert branch_name in branch_check.stdout
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_detached_head(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-nonbeacon", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree?delete_branch=true",
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["branch_deleted"] is False
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_running_session_409(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-running", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree", timeout=10
            )
            assert resp.status_code == 409
            assert "terminal" in resp.json()["error"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_already_cleaned_404(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-cleaned", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 404
            assert "no worktree" in resp.json()["error"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_directory_missing_succeeds(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-miss", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]
            wt_path = root["worktree_path"]

            terminate_session(ctx["url"], session_id)

            shutil.rmtree(wt_path, ignore_errors=True)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True

            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT worktree_path FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            assert row[0] is None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_concurrent(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-conc", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            terminate_session(ctx["url"], session_id)

            def do_delete():
                return httpx.delete(
                    f"{ctx['url']}/api/v1/sessions/{session_id}/worktree",
                    timeout=10,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(do_delete) for _ in range(2)]
                results = [f.result() for f in futures]

            status_codes = sorted([r.status_code for r in results])
            assert status_codes == [200, 200]

            for r in results:
                assert r.json()["deleted"] is True
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_worktree_preserves_base_commit_sha(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "wt-sha", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )
            root = get_root_session(ctx["url"], exec_id)
            session_id = root["id"]

            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT base_commit_sha FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            original_sha = row[0]

            terminate_session(ctx["url"], session_id)

            resp = httpx.delete(
                f"{ctx['url']}/api/v1/sessions/{session_id}/worktree", timeout=10
            )
            assert resp.status_code == 200

            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT base_commit_sha FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            assert row[0] == original_sha
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
