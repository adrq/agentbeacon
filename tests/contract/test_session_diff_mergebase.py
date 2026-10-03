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


def make_diverged_repo() -> str:
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
        ["git", "-C", tmpdir, "checkout", "-b", "feature/work"],
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "-C", tmpdir, "checkout", "main"],
        check=True,
        capture_output=True,
    )
    with open(os.path.join(tmpdir, "main_only.txt"), "w") as f:
        f.write("main content\n")
    subprocess.run(
        ["git", "-C", tmpdir, "add", "main_only.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "commit", "-m", "main commit"],
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "-C", tmpdir, "checkout", "feature/work"],
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
def test_diff_mergebase_branch_resolution(test_database):
    git_dir = make_diverged_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-mergebase", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

            with open(os.path.join(wt_path, "feature_only.txt"), "w") as f:
                f.write("feature content\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "feature_only.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "feature commit"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "main"},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            paths = {f["path"] for f in data["files"]}
            assert "feature_only.txt" in paths, (
                "feature branch change should appear in merge-base diff"
            )
            assert "main_only.txt" not in paths, (
                "main-only changes should not appear when using merge-base"
            )
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_sha_base_not_mergebased(test_database):
    git_dir = make_diverged_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "diff-sha-direct", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "feature_only.txt"), "w") as f:
                f.write("feature content\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "feature_only.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "feature commit"],
                check=True,
                capture_output=True,
            )

            base_sha = root["base_commit_sha"]
            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": base_sha},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            paths = {f["path"] for f in data["files"]}
            assert "feature_only.txt" in paths
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


def make_hex_branch_repo() -> str:
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
        ["git", "-C", tmpdir, "branch", "deadbeef"],
        check=True,
        capture_output=True,
    )
    return tmpdir


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_hex_branch_name_uses_mergebase(test_database):
    git_dir = make_hex_branch_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "diff-hex-branch", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "new.txt"), "w") as f:
                f.write("content\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "new.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "add new"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "deadbeef"},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            paths = {f["path"] for f in data["files"]}
            assert "new.txt" in paths
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


def make_content_identical_repo() -> str:
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
        ["git", "-C", tmpdir, "branch", "parallel"],
        check=True,
        capture_output=True,
    )

    with open(os.path.join(tmpdir, "file.txt"), "w") as f:
        f.write("content\n")
    subprocess.run(
        ["git", "-C", tmpdir, "add", "file.txt"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", tmpdir, "commit", "-m", "add file"],
        check=True,
        capture_output=True,
    )

    main_sha = subprocess.run(
        ["git", "-C", tmpdir, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", tmpdir, "checkout", "parallel"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "cherry-pick", main_sha],
        check=True,
        capture_output=True,
    )

    return tmpdir


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_content_identical_branch(test_database):
    git_dir = make_content_identical_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "diff-content-identical", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "main"},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            assert data["content_identical"] is True
            assert data["files"] == []
            assert data["summary"]["files_changed"] == 0
            assert data.get("patch") == ""
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_diverged_branch_not_content_identical(test_database):
    git_dir = make_diverged_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "diff-not-identical", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "feature_only.txt"), "w") as f:
                f.write("feature content\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "feature_only.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "feature commit"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "main"},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            assert "content_identical" not in data or data["content_identical"] is False
            assert len(data["files"]) > 0
            assert "feature_only.txt" in {f["path"] for f in data["files"]}
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


def make_orphan_branch_repo() -> str:
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
        ["git", "-C", tmpdir, "checkout", "--orphan", "orphan-branch"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "rm", "-rf", "."], check=True, capture_output=True
    )
    with open(os.path.join(tmpdir, "orphan.txt"), "w") as f:
        f.write("orphan content\n")
    subprocess.run(
        ["git", "-C", tmpdir, "add", "orphan.txt"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", tmpdir, "commit", "-m", "orphan root"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", tmpdir, "checkout", "main"],
        check=True,
        capture_output=True,
    )
    return tmpdir


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_mergebase_failure_returns_400(test_database):
    git_dir = make_orphan_branch_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(
                ctx["url"], "diff-mergebase-fail", path=git_dir
            )

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "orphan-branch"},
                timeout=10,
            )
            assert resp.status_code == 400
            assert "no merge base found" in resp.json()["error"].lower()
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
