"""ACP Agent Configuration Contract Tests.

Verifies the worker's handling of ACP agent configurations from task payload.

Run with: uv run pytest tests/integration/test_acp_config.py -v
"""

import time
from pathlib import Path

import pytest
import requests

from tests.contracts.schema_helpers import build_acp_task, build_canonical_task
from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_and_wait_for_a2a_agent,
    start_mock_scheduler,
    wait_for_port,
)
from tests.integration.worker_test_helpers import (
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
    """Enqueue a session with custom ACP agent config."""
    task_payload = {
        "agent_id": "mock-agent",
        "agent_type": "acp",
        "agent_config": agent_config,
        "sandbox_config": {},
        "message": {"parts": [{"kind": "text", "text": prompt_text}]},
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
    """Verify worker successfully executes ACP agent with only required fields (command, args)."""
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
    """Verify worker successfully executes ACP agent with all optional fields (timeout, env)."""
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


@pytest.mark.skip(
    reason="Phase 5: A2A executor not yet implemented in new executor system"
)
def test_mixed_a2a_and_acp_agents():
    """Verify worker can handle workflows with both A2A and ACP agents, dispatching correctly based on agent type."""
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            agent_proc = start_and_wait_for_a2a_agent(
                18765, Path(__file__).parent.parent.parent
            )
            processes.append(agent_proc)

            a2a_task = build_canonical_task(
                node_id="node-mixed-a2a",
                text="Test A2A agent",
                agent="mock-agent",
                execution_id="exec-mixed-a2a",
            )

            acp_task = build_acp_task(
                node_id="node-mixed-acp",
                text="Test ACP agent",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-mixed-acp",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=a2a_task
            )
            assert response.status_code == 200

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(5)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()

            a2a_result = [r for r in results if r["executionId"] == "exec-mixed-a2a"]
            assert len(a2a_result) == 1, f"Should have result for A2A task: {results}"
            assert a2a_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"A2A task should complete successfully: {a2a_result[0]}"
            )

            acp_result = [r for r in results if r["executionId"] == "exec-mixed-acp"]
            assert len(acp_result) == 1, f"Should have result for ACP task: {results}"
            assert acp_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"ACP task should complete successfully: {acp_result[0]}"
            )

        finally:
            cleanup_processes(processes)


@pytest.mark.skip(
    reason="Phase 5+: worker no longer loads --agents-config YAML; "
    "config validation happens per-session via task payload"
)
def test_invalid_acp_configs():
    """Verify worker rejects ACP agent configs with validation errors at load time (missing command, zero/negative timeout)."""
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            worker_proc = start_worker(
                f"http://localhost:{mock_orchestrator_port}",
                agents_config="examples/agents-with-invalid.yaml",
            )
            processes.append(worker_proc)

            exit_code = worker_proc.wait(timeout=5)

            assert exit_code != 0, (
                f"Worker should fail at startup with invalid ACP config, got exit code: {exit_code}"
            )

            worker_output, _ = worker_proc.communicate(timeout=1)
            assert (
                "invalid" in worker_output.lower()
                or "config" in worker_output.lower()
                or "validation" in worker_output.lower()
            ), f"Worker error should mention config validation: {worker_output}"

        finally:
            cleanup_processes(processes)
