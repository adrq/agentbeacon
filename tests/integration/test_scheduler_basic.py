"""Basic scheduler integration test - Simple happy path workflow execution.

This test implements exactly what was requested: a focused integration test that
starts the scheduler, registers a 2-node sequential workflow (A2A + ACP nodes),
and uses mock polling to execute both tasks sequentially.
"""

import requests
import uuid
import time

from tests.contracts import schema_helpers as contract_schema_helpers
from tests.testhelpers import scheduler_context


def test_scheduler_basic_integration():
    """Test basic scheduler integration with 2-node sequential workflow via HTTP polling."""
    with scheduler_context() as scheduler:
        scheduler_url = scheduler["url"]

        # Register 2-node sequential workflow for HTTP polling test
        workflow_yaml = """
name: basic-integration-test
description: Simple 2-node sequential workflow for HTTP polling test
tasks:
    - id: test-task-1
        agent: mock-agent
        task:
            messages:
                - role: user
                    messageId: "msg-test-task-1"
                    kind: message
                    parts:
                        - kind: text
                            text: Analyze the processed data
    - id: test-task-2
        agent: mock-agent
        depends_on: [test-task-1]
        task:
            messages:
                - role: user
                    messageId: "msg-test-task-2"
                    kind: message
                    parts:
                        - kind: text
                            text: Analyze the processed data
""".strip()

        register_response = requests.post(
            f"{scheduler_url}/api/workflows/register",
            json={"workflow_yaml": workflow_yaml},
            timeout=10,
        )
        assert register_response.status_code == 201, (
            f"Registration failed: {register_response.text}"
        )

        response_data = register_response.json()

        # Verify registration response contains expected fields
        assert "name" in response_data, (
            f"Registration should return name: {response_data}"
        )
        assert response_data["name"] == "basic-integration-test"
        assert "ref" in response_data  # Should have a workflow reference

        workflow_ref = response_data["ref"]

        # Get Agent Card to discover proper A2A endpoint (per A2A protocol)
        agent_card_response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json", timeout=5
        )
        assert agent_card_response.status_code == 200, (
            f"Failed to get agent card: {agent_card_response.text}"
        )

        agent_card = agent_card_response.json()
        assert "url" in agent_card, f"Agent card should have url field: {agent_card}"

        # Handle URLs from agent card (may have wrong port for test)
        a2a_url = agent_card["url"]

        # For tests, replace the port in the agent card URL with our actual test port
        if "localhost:9456" in a2a_url:
            # Agent card has hardcoded port 9456, replace with our test port
            test_port = scheduler_url.split(":")[-1]
            a2a_endpoint = a2a_url.replace("localhost:9456", f"localhost:{test_port}")
        elif a2a_url.startswith("/"):
            # Relative URL - prepend base scheduler URL
            a2a_endpoint = scheduler_url + a2a_url
        else:
            # Absolute URL - use as-is
            a2a_endpoint = a2a_url

        # Start workflow execution via A2A JSON-RPC (following spec properly)
        # Per A2A spec: contextId is server-generated, not client-provided
        message = {
            "role": "user",
            "parts": [
                {"kind": "data", "data": {"data": {"workflowRef": workflow_ref}}}
            ],
            "messageId": str(uuid.uuid4()),
            "kind": "message",
        }

        jsonrpc_request = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {"message": message},
            "id": str(uuid.uuid4()),
        }

        # Send JSON-RPC request to start workflow execution using discovered endpoint
        execute_response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=10)
        assert execute_response.status_code == 200, (
            f"Workflow execution failed: {execute_response.text}"
        )

        execute_data = execute_response.json()
        assert "result" in execute_data, f"Should return result: {execute_data}"
        assert execute_data.get("error") is None, (
            f"Should not have error: {execute_data}"
        )

        # Poll for first task (should be test-task-1 with no dependencies)
        poll_response = requests.get(f"{scheduler_url}/api/worker/poll", timeout=5)
        assert poll_response.status_code == 200

        poll_data = poll_response.json()
        assert "task" in poll_data
        task = poll_data["task"]

        assert task is not None, "Should return test-task-1"
        assert task["nodeId"] == "test-task-1"
        assert task["agent"] == "mock-agent"
        assert task["workflowRef"] == workflow_ref
        assert task["workflowRegistryId"], "workflowRegistryId should be present"
        assert task["workflowVersion"], "workflowVersion should be present"
        assert isinstance(task.get("protocolMetadata", {}), dict)
        contract_schema_helpers.validate_payload("a2a-task", task["task"])
        assert "prompt" not in task["task"], (
            "Canonical task payload should omit legacy prompt"
        )

        execution_id = task["executionId"]

        # Submit result for first task (using A2A-compliant format)
        result_response = requests.post(
            f"{scheduler_url}/api/worker/result",
            json={
                "nodeId": "test-task-1",
                "executionId": execution_id,
                "taskStatus": {
                    "state": "completed",
                    "timestamp": "2025-09-19T10:00:00Z",
                },
                "artifacts": [
                    {
                        "artifactId": str(uuid.uuid4()),
                        "name": "task-1-output",
                        "description": "Output from test task 1",
                        "parts": [{"text": "Task 1 completed successfully"}],
                    }
                ],
            },
            timeout=5,
        )
        assert result_response.status_code == 200

        # Poll for second task (should be test-task-2 after dependency satisfied)
        # Allow brief delay for async task submission after first task completion
        second_task = None
        for attempt in range(10):  # Try for up to 5 seconds (10 * 0.5s)
            second_poll = requests.get(f"{scheduler_url}/api/worker/poll", timeout=5)
            assert second_poll.status_code == 200

            second_poll_data = second_poll.json()
            second_task = second_poll_data["task"]

            if second_task is not None:
                break

            time.sleep(0.5)

        assert second_task is not None, (
            "Should return test-task-2 after test-task-1 completion"
        )
        assert second_task["nodeId"] == "test-task-2"
        assert second_task["agent"] == "mock-agent"
        assert second_task["workflowRef"] == workflow_ref
        assert second_task["workflowRegistryId"], "workflowRegistryId should be present"
        assert second_task["workflowVersion"], "workflowVersion should be present"
        assert isinstance(second_task.get("protocolMetadata", {}), dict)
        contract_schema_helpers.validate_payload("a2a-task", second_task["task"])
        assert "prompt" not in second_task["task"], (
            "Canonical task payload should omit legacy prompt"
        )

        second_execution_id = second_task["executionId"]

        # Submit result for second task (using A2A-compliant format)
        second_result = requests.post(
            f"{scheduler_url}/api/worker/result",
            json={
                "nodeId": "test-task-2",
                "executionId": second_execution_id,
                "taskStatus": {
                    "state": "completed",
                    "timestamp": "2025-09-19T10:01:00Z",
                },
                "artifacts": [
                    {
                        "artifactId": str(uuid.uuid4()),
                        "name": "task-2-output",
                        "description": "Output from test task 2",
                        "parts": [{"text": "Task 2 completed successfully"}],
                    }
                ],
            },
            timeout=5,
        )
        assert second_result.status_code == 200

        # Poll again - should have no more tasks (workflow complete)
        final_poll = requests.get(f"{scheduler_url}/api/worker/poll", timeout=5)
        assert final_poll.status_code == 200

        final_data = final_poll.json()
        assert final_data["task"] is None, (
            "Should have no more tasks after workflow completion"
        )


def test_poll_endpoint_no_tasks_returns_null():
    """Contract test: GET /api/worker/poll returns {task: null} when no tasks available."""
    with scheduler_context() as scheduler:
        scheduler_url = scheduler["url"]

        # Test contract: GET /api/worker/poll with no tasks
        poll_response = requests.get(f"{scheduler_url}/api/worker/poll", timeout=5)

        # Contract expectations
        assert poll_response.status_code == 200, (
            f"Poll endpoint should return 200, got {poll_response.status_code}"
        )

        poll_data = poll_response.json()
        assert "task" in poll_data, f"Response should have 'task' field: {poll_data}"
        assert poll_data["task"] is None, (
            f"Should return null when no tasks available: {poll_data}"
        )


def test_result_endpoint_rejects_invalid_execution():
    """Contract test: POST /api/worker/result rejects results for non-existent execution IDs."""
    with scheduler_context() as scheduler:
        scheduler_url = scheduler["url"]

        # Test contract: POST /api/worker/result with non-existent execution ID
        test_result = {
            "nodeId": "test-node-123",
            "executionId": "exec-456",  # This execution ID doesn't exist
            "taskStatus": {"state": "completed", "timestamp": "2025-09-19T10:00:00Z"},
            "artifacts": [
                {
                    "artifactId": str(uuid.uuid4()),
                    "name": "test-output",
                    "description": "Test output artifact",
                    "parts": [{"text": "Task completed successfully"}],
                }
            ],
        }

        result_response = requests.post(
            f"{scheduler_url}/api/worker/result", json=test_result, timeout=5
        )

        # Contract expectations: should reject non-existent execution
        assert result_response.status_code in [400], (
            f"Should reject invalid execution with 400, got {result_response.status_code}: {result_response.text}"
        )

        # Response should indicate error
        response_data = result_response.json()
        assert "error" in response_data, (
            f"Error response should have error field: {response_data}"
        )


def test_result_endpoint_validates_required_fields():
    """Contract test: POST /api/worker/result validates required A2A fields."""
    with scheduler_context() as scheduler:
        scheduler_url = scheduler["url"]

        # Test contract: Missing required fields should be rejected
        incomplete_result = {
            "nodeId": "test-node-123",
            # Missing executionId, taskStatus, artifacts
        }

        result_response = requests.post(
            f"{scheduler_url}/api/worker/result", json=incomplete_result, timeout=5
        )

        # Contract expectations
        assert result_response.status_code == 400, (
            f"Should reject incomplete results with 400, got {result_response.status_code}"
        )

        error_data = result_response.json()
        assert "error" in error_data, (
            f"Error response should have error field: {error_data}"
        )


def test_old_nodes_format_rejected():
    """Test that workflows using old 'nodes' format are rejected with schema validation error."""
    with scheduler_context() as scheduler:
        scheduler_url = scheduler["url"]

        # Test workflow using old schema format with "nodes" and "request"
        old_format_yaml = """
name: old-format-test
description: Test workflow using old schema format
nodes:
  - id: test-node-1
    agent: mock-agent
    request:
      prompt: This should be rejected
""".strip()

        # Attempt to register old format workflow
        register_response = requests.post(
            f"{scheduler_url}/api/workflows/register",
            json={"workflow_yaml": old_format_yaml},
            timeout=10,
        )

        # Should be rejected with 400 status
        assert register_response.status_code == 400, (
            f"Old format should be rejected with 400, got {register_response.status_code}: {register_response.text}"
        )

        # Response should contain schema validation error about nodes field
        error_data = register_response.json()
        assert "error" in error_data, (
            f"Error response should have error field: {error_data}"
        )

        # Should contain error message about nodes field not being supported
        error_message = error_data["error"].lower()
        assert "nodes" in error_message and "not supported" in error_message, (
            f"Error should mention 'nodes' field not supported: {error_data['error']}"
        )
