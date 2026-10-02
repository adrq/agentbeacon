# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import pytest
import requests

from tests.testhelpers import (
    cleanup_processes,
)
from tests.worker_test_helpers import (
    create_mock_scheduler,
    start_worker,
    clear_state,
    get_results,
    mark_complete,
    poll_until,
)


@pytest.fixture()
def mock_scheduler():
    url, port, proc, pm = create_mock_scheduler()
    yield url, port, proc
    cleanup_processes([proc])
    pm.release_port(port)


def _enqueue_acp_session(
    url,
    agent_config,
    session_id="sess-1",
    execution_id="exec-1",
    prompt_text="hello from test",
):
    task_payload = {
        "agent_id": "mock-agent",
        "driver": {"platform": "acp", "config": {}},
        "agent_config": agent_config,
        "message": {"role": "ROLE_USER", "parts": [{"text": prompt_text}]},
    }
    resp = requests.post(
        f"{url}/test/enqueue_session",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": task_payload,
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue session failed: {resp.text}"


def test_acp_agent_required_fields_only(mock_scheduler):
    url, _, _ = mock_scheduler
    clear_state(url)

    _enqueue_acp_session(
        url,
        agent_config={
            "command": "uv",
            "args": ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", "acp"],
        },
        prompt_text="Test minimal config",
    )

    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete with minimal ACP config: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_acp_agent_all_optional_fields(mock_scheduler):
    url, _, _ = mock_scheduler
    clear_state(url)

    _enqueue_acp_session(
        url,
        agent_config={
            "command": "uv",
            "args": ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", "acp"],
            "timeout": 60,
            "env": {"TEST_VAR": "test_value"},
        },
        prompt_text="Test full config",
    )

    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete with full ACP config: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
