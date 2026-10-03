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
    if with_commit:
        with open(os.path.join(tmpdir, "README.md"), "w") as f:
            f.write("# Test\n")
        subprocess.run(
            ["git", "-C", tmpdir, "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", "init"],
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
def test_diff_with_changes(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-project", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

            with open(os.path.join(wt_path, "new_file.txt"), "w") as f:
                f.write("hello world\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "new_file.txt"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["files"]) >= 1
            new_file = next(
                (f for f in data["files"] if f["path"] == "new_file.txt"), None
            )
            assert new_file is not None
            assert new_file["status"] == "A"
            assert new_file["insertions"] == 1

            assert data["summary"]["files_changed"] >= 1
            assert data["summary"]["insertions"] >= 1

            assert "patch" in data
            assert "new_file.txt" in data["patch"]
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_empty(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-empty", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            assert data["files"] == []
            assert data["summary"]["files_changed"] == 0
            assert data["summary"]["insertions"] == 0
            assert data["summary"]["deletions"] == 0
            assert data["patch"] == ""
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_stat_only(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-stat", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "new.txt"), "w") as f:
                f.write("data\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "new.txt"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"stat": "true"},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["files"]) >= 1
            assert "patch" not in data
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_custom_base(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-base", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "committed.txt"), "w") as f:
                f.write("committed content\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "committed.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "add committed"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "HEAD~1"},
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["files"]) >= 1
            committed_file = next(
                (f for f in data["files"] if f["path"] == "committed.txt"), None
            )
            assert committed_file is not None
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_no_worktree_or_cwd_session(test_database):
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
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 400
            assert "not a git repository" in resp.json()["error"].lower()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_nonexistent_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/sessions/nonexistent-id/worktree/diff", timeout=10
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_invalid_base_ref(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-badref", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "nonexistent-ref-abc123"},
                timeout=10,
            )
            assert resp.status_code == 400
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_flag_injection(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-inject", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "--exec=whoami"},
                timeout=10,
            )
            assert resp.status_code == 400
            assert "invalid base ref" in resp.json()["error"].lower()
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_binary_file(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-binary", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "image.bin"), "wb") as f:
                f.write(bytes(range(256)))
            subprocess.run(
                ["git", "-C", wt_path, "add", "image.bin"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            bin_file = next(
                (f for f in data["files"] if f["path"] == "image.bin"), None
            )
            assert bin_file is not None
            assert bin_file["insertions"] == 0
            assert bin_file["deletions"] == 0
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_renamed_file(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-rename", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            subprocess.run(
                ["git", "-C", wt_path, "mv", "README.md", "GUIDE.md"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            paths = {f["path"] for f in data["files"]}
            statuses = {f["path"]: f["status"] for f in data["files"]}

            assert "README.md" in paths
            assert "GUIDE.md" in paths
            assert statuses["README.md"] == "D"
            assert statuses["GUIDE.md"] == "A"

            assert all(f["status"] != "R" for f in data["files"])
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_deleted_worktree(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-deleted", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

            shutil.rmtree(wt_path)

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 404
            assert "no longer exists" in resp.json()["error"].lower()
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_large_patch_truncation(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-large", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            large_content = "x" * 1_100_000 + "\n"
            with open(os.path.join(wt_path, "large.txt"), "w") as f:
                f.write(large_content)
            subprocess.run(
                ["git", "-C", wt_path, "add", "large.txt"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/json")
            data = resp.json()

            assert data["truncated"] is True
            assert "patch" not in data
            assert data["summary"]["files_changed"] >= 1
            assert data["summary"]["insertions"] >= 1
            assert len(data["files"]) >= 1

            resp_stat = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"stat": "true"},
                timeout=10,
            )
            assert resp_stat.status_code == 200
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_committed_changes_still_visible(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-committed", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]
            assert wt_path is not None

            assert root.get("base_commit_sha") is not None
            assert len(root["base_commit_sha"]) == 40

            with open(os.path.join(wt_path, "agent_work.txt"), "w") as f:
                f.write("work done by agent\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "agent_work.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "agent commit"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            assert data["summary"]["files_changed"] >= 1
            agent_file = next(
                (f for f in data["files"] if f["path"] == "agent_work.txt"), None
            )
            assert agent_file is not None, (
                "committed file should appear in default diff"
            )
            assert agent_file["status"] == "A"

            resp_head = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff",
                params={"base": "HEAD"},
                timeout=10,
            )
            assert resp_head.status_code == 200
            head_data = resp_head.json()
            assert head_data["summary"]["files_changed"] == 0
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_committed_plus_uncommitted(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-mixed", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            with open(os.path.join(wt_path, "committed.txt"), "w") as f:
                f.write("committed\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "committed.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", wt_path, "commit", "-m", "first commit"],
                check=True,
                capture_output=True,
            )

            with open(os.path.join(wt_path, "uncommitted.txt"), "w") as f:
                f.write("uncommitted\n")
            subprocess.run(
                ["git", "-C", wt_path, "add", "uncommitted.txt"],
                check=True,
                capture_output=True,
            )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            paths = {f["path"] for f in data["files"]}
            assert "committed.txt" in paths, "committed file should be in diff"
            assert "uncommitted.txt" in paths, "uncommitted file should be in diff"
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_diff_multiple_commits(test_database):
    git_dir = make_git_repo()
    try:
        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
            project = create_project_via_api(ctx["url"], "diff-multi", path=git_dir)

            exec_id, _ = create_execution_via_api(
                ctx["url"], agent_id, "test", project_id=project["id"]
            )

            root = get_root_session(ctx["url"], exec_id)
            wt_path = root["worktree_path"]

            for i in range(3):
                fname = f"file_{i}.txt"
                with open(os.path.join(wt_path, fname), "w") as f:
                    f.write(f"content {i}\n")
                subprocess.run(
                    ["git", "-C", wt_path, "add", fname],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", wt_path, "commit", "-m", f"commit {i}"],
                    check=True,
                    capture_output=True,
                )

            resp = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{root['id']}/worktree/diff", timeout=10
            )
            assert resp.status_code == 200
            data = resp.json()

            paths = {f["path"] for f in data["files"]}
            for i in range(3):
                assert f"file_{i}.txt" in paths
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
