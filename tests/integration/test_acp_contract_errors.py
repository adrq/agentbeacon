"""ACP Protocol Contract Test - Error Handling

Verifies the worker's handling of malformed JSON-RPC responses.

Run with: uv run pytest tests/integration/test_acp_contract_errors.py -v
"""

import time

import pytest

from tests.testhelpers import cleanup_processes
from tests.integration.worker_test_helpers import (
    create_mock_scheduler,
    start_worker,
    clear_state,
    enqueue_session,
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


def test_malformed_jsonrpc_response(mock_scheduler):
    """Contract test - malformed JSON-RPC response.

    Verifies that worker fails task when agent sends invalid JSON or malformed JSON-RPC structure.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="INVALID_JSONRPC")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is not None, (
            f"Task should fail on malformed JSON-RPC: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
