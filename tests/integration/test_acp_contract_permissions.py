"""ACP Protocol Contract Test - Session/Request_Permission Auto-Approval

Verifies the worker's auto-approval of permission requests.

Run with: uv run pytest tests/integration/test_acp_contract_permissions.py -v
"""

import time
from pathlib import Path

import pytest

from tests.testhelpers import cleanup_processes, start_worker_with_retry_config
from tests.integration.worker_test_helpers import (
    create_mock_scheduler,
    clear_state,
    enqueue_session,
    get_results,
    mark_complete,
    poll_until,
)

BASE_DIR = Path(__file__).parent.parent.parent


@pytest.fixture()
def mock_scheduler():
    url, port, proc, pm = create_mock_scheduler()
    yield url, port, proc
    cleanup_processes([proc])
    pm.release_port(port)


def _start_worker_with_output(scheduler_url):
    """Start worker with stdout capture for log inspection."""
    return start_worker_with_retry_config(
        scheduler_url=scheduler_url,
        startup_attempts=10,
        reconnect_attempts=10,
        retry_delay_ms=100,
        interval="500ms",
        base_dir=BASE_DIR,
    )


def test_session_request_permission_auto_approval(mock_scheduler):
    """Contract test - session/request_permission auto-approval.

    Verifies that worker responds to session/request_permission with approval outcome
    and logs warning at WARN level with auto-approval message and tool ID.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="REQUEST_PERMISSION")
    worker = _start_worker_with_output(url)
    worker_output = ""
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete after auto-approval: {results[0]}"
        )

    finally:
        mark_complete(url)
        time.sleep(1)
        worker.terminate()
        worker_output, _ = worker.communicate(timeout=5)
        cleanup_processes([worker])

    # Verify WARN log contains auto-approval message WITH tool ID
    assert "WARN" in worker_output or "warn" in worker_output.lower(), (
        f"Worker should log at WARN level: {worker_output}"
    )
    has_auto_approval = (
        "Auto-approved session/request_permission" in worker_output
        or "auto-approved" in worker_output.lower()
    )
    has_tool_id = (
        "toolId" in worker_output
        or "tool_id" in worker_output
        or "toolCallId" in worker_output
    )
    assert has_auto_approval and has_tool_id, (
        f"Worker should log auto-approval WITH explicit tool ID: {worker_output}"
    )
