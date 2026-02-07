"""Basic scheduler integration test - Simple happy path workflow execution.

This test implements exactly what was requested: a focused integration test that
starts the scheduler, registers a 2-node sequential workflow (A2A + ACP nodes),
and uses mock polling to execute both tasks sequentially.
"""

import pytest

pytestmark = pytest.mark.skip(reason="Deferred: DAG model removed")

import requests
import uuid
import time
import pytest

from tests.contracts import schema_helpers as contract_schema_helpers
from tests.testhelpers import scheduler_context


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_scheduler_basic_integration(test_database):
    """Test basic scheduler integration with 2-node sequential workflow via HTTP polling."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Register 2-node sequential workflow for HTTP polling test
        workflow_yaml = """
name: basic-integration-test
description: Simple 2-node sequential workflow for HTTP polling test
tasks:
  - id: test-task-1
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-test-task-1"
        kind: message
        parts:
          - kind: text
            text: Analyze the processed data
  - id: test-task-2
    agent: mock-agent
    depends_on: [test-task-1]
    task:
      message:
        role: user
        messageId: "msg-test-task-2"
        kind: message
        parts:
          - kind: text
            text: Analyze the processed data
""".strip()

        register_response = requests.post(
            f"{scheduler_url}/api/registry/workflows",
            json={
                "namespace": "test",
                "name": "basic-integration-test",
                "version": "1.0.0",
                "isLatest": True,
                "workflowYaml": workflow_yaml,
            },
            timeout=10,
        )
        assert register_response.status_code == 201, (
            f"Registration failed: {register_response.text}"
        )

        response_data = register_response.json()

        # Verify registration response contains expected fields
        assert "workflowRegistryId" in response_data, (
            f"Registration should return workflowRegistryId: {response_data}"
        )
        assert response_data["workflowRegistryId"] == "test/basic-integration-test"
        assert "version" in response_data
        assert response_data["version"] == "1.0.0"

        workflow_ref = response_data["workflowRegistryId"]

        # Get Agent Card to discover proper A2A endpoint (per A2A protocol)
        agent_card_response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json", timeout=5
        )
        assert agent_card_response.status_code == 200, (
            f"Failed to get agent card: {agent_card_response.text}"
        )

        agent_card = agent_card_response.json()
        assert "url" in agent_card, f"Agent card should have url field: {agent_card}"
        a2a_endpoint = agent_card["url"]

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

        # Sync as idle worker to get first task (should be test-task-1 with no dependencies)
        sync_response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert sync_response.status_code == 200

        sync_data = sync_response.json()
        assert sync_data["type"] == "task_assigned", f"Should assign task: {sync_data}"
        assert "task" in sync_data

        task = sync_data["task"]
        assert task["nodeId"] == "test-task-1"
        assert task["agent"] == "mock-agent"
        assert (
            task["workflowRef"] == f"{workflow_ref}:latest"
        )  # Resolves to :latest when submitted without version
        assert task["workflowRegistryId"], "workflowRegistryId should be present"
        assert task["workflowVersion"], "workflowVersion should be present"
        assert isinstance(task.get("protocolMetadata", {}), dict)
        contract_schema_helpers.validate_payload("message-send-params", task["task"])
        assert "prompt" not in task["task"], (
            "Canonical task payload should omit legacy prompt"
        )

        execution_id = task["executionId"]

        # Submit result for first task via sync (using A2A-compliant format)
        result_sync = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "test-task-1",
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
            },
            timeout=5,
        )
        assert result_sync.status_code == 200

        # Sync for second task (should be test-task-2 after dependency satisfied)
        # Scheduler always returns NoAction after result submission, so poll for next task
        second_task = None
        for attempt in range(10):  # Try for up to 5 seconds (10 * 0.5s)
            second_sync = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert second_sync.status_code == 200

            second_sync_data = second_sync.json()
            if second_sync_data.get("type") == "task_assigned":
                second_task = second_sync_data["task"]
                break

            time.sleep(0.2)

        assert second_task is not None, (
            "Should return test-task-2 after test-task-1 completion"
        )
        assert second_task["nodeId"] == "test-task-2"
        assert second_task["agent"] == "mock-agent"
        assert (
            second_task["workflowRef"] == f"{workflow_ref}:latest"
        )  # Resolves to :latest when submitted without version
        assert second_task["workflowRegistryId"], "workflowRegistryId should be present"
        assert second_task["workflowVersion"], "workflowVersion should be present"
        assert isinstance(second_task.get("protocolMetadata", {}), dict)
        contract_schema_helpers.validate_payload(
            "message-send-params", second_task["task"]
        )
        assert "prompt" not in second_task["task"], (
            "Canonical task payload should omit legacy prompt"
        )

        second_execution_id = second_task["executionId"]

        # Submit result for second task via sync (using A2A-compliant format)
        second_result_sync = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": second_execution_id,
                    "nodeId": "test-task-2",
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
            },
            timeout=5,
        )
        assert second_result_sync.status_code == 200

        # Sync again - should have no more tasks (workflow complete)
        final_sync = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert final_sync.status_code == 200

        final_data = final_sync.json()
        assert final_data["type"] == "no_action", (
            f"Should have no more tasks after workflow completion: {final_data}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_poll_endpoint_no_tasks_returns_null(test_database):
    """Contract test: POST /api/worker/sync returns no_action when no tasks available."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Test contract: POST /api/worker/sync with status="idle" and no tasks
        sync_response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )

        # Contract expectations
        assert sync_response.status_code == 200, (
            f"Sync endpoint should return 200, got {sync_response.status_code}"
        )

        sync_data = sync_response.json()
        assert "type" in sync_data, f"Response should have 'type' field: {sync_data}"
        assert sync_data["type"] == "no_action", (
            f"Should return no_action when no tasks available: {sync_data}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_result_endpoint_rejects_invalid_execution(test_database):
    """Contract test: POST /api/worker/sync rejects results for non-existent execution IDs."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Test contract: POST /api/worker/sync with non-existent execution ID in taskResult
        sync_response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": "exec-456",  # This execution ID doesn't exist
                    "nodeId": "test-node-123",
                    "taskStatus": {
                        "state": "completed",
                        "timestamp": "2025-09-19T10:00:00Z",
                    },
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "name": "test-output",
                            "description": "Test output artifact",
                            "parts": [{"text": "Task completed successfully"}],
                        }
                    ],
                },
            },
            timeout=5,
        )

        # Contract expectations: should reject non-existent execution with error status
        assert sync_response.status_code in [400, 500], (
            f"Should reject invalid execution with 400 or 500, got {sync_response.status_code}: {sync_response.text}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_result_endpoint_validates_required_fields(test_database):
    """Contract test: POST /api/worker/sync validates required taskResult fields."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Test contract: Missing required fields in taskResult should be rejected
        sync_response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "nodeId": "test-node-123",
                    # Missing executionId, taskStatus - required fields
                },
            },
            timeout=5,
        )

        # Contract expectations: should reject incomplete taskResult with validation error
        # Axum/serde returns 422 for deserialization failures
        assert sync_response.status_code == 422, (
            f"Should reject incomplete taskResult with 422, got {sync_response.status_code}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_old_nodes_format_rejected(test_database):
    """Test that workflows using old 'nodes' format are rejected with schema validation error."""
    with scheduler_context(db_url=test_database) as scheduler:
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
            f"{scheduler_url}/api/registry/workflows",
            json={
                "namespace": "test",
                "name": "old-format-test",
                "version": "1.0.0",
                "isLatest": True,
                "workflowYaml": old_format_yaml,
            },
            timeout=10,
        )

        # Should be rejected with 400 status
        assert register_response.status_code == 400, (
            f"Old format should be rejected with 400, got {register_response.status_code}: {register_response.text}"
        )

        # Response should contain schema validation error
        error_data = register_response.json()
        assert "error" in error_data, (
            f"Error response should have error field: {error_data}"
        )

        # Error should mention validation failure (schema validation catches old format)
        error_message = error_data["error"].lower()
        assert "validation" in error_message or "schema" in error_message, (
            f"Error should mention validation/schema failure: {error_data['error']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_scheduler_retry_logic(test_database):
    """Test scheduler retry logic with failing task that eventually succeeds."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Register workflow with retry policy
        workflow_yaml = """
name: retry-test
description: Test workflow with retry configuration
tasks:
  - id: flaky-task
    agent: mock-agent
    execution:
      retry:
        attempts: 3
        backoff: fixed
        delay_seconds: 1
    task:
      message:
        role: user
        messageId: "msg-retry-test"
        kind: message
        parts:
          - kind: text
            text: Execute flaky task
""".strip()

        register_response = requests.post(
            f"{scheduler_url}/api/registry/workflows",
            json={
                "namespace": "test",
                "name": "retry-test",
                "version": "1.0.0",
                "isLatest": True,
                "workflowYaml": workflow_yaml,
            },
            timeout=10,
        )
        assert register_response.status_code == 201
        response_data = register_response.json()
        workflow_ref = response_data["workflowRegistryId"]

        # Get Agent Card to discover proper A2A endpoint (per A2A protocol)
        agent_card_response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json", timeout=5
        )
        assert agent_card_response.status_code == 200, (
            f"Failed to get agent card: {agent_card_response.text}"
        )

        agent_card = agent_card_response.json()
        assert "url" in agent_card, f"Agent card should have url field: {agent_card}"
        a2a_endpoint = agent_card["url"]

        # Start workflow execution via A2A JSON-RPC (following spec properly)
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

        # Sync as idle worker to get first task
        sync_response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert sync_response.status_code == 200

        sync_data = sync_response.json()
        assert sync_data["type"] == "task_assigned", f"Should assign task: {sync_data}"
        assert "task" in sync_data

        task = sync_data["task"]
        execution_id = task["executionId"]
        assert task["nodeId"] == "flaky-task"

        # Simulate first failure
        result1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "flaky-task",
                    "taskStatus": {
                        "state": "failed",
                        "message": {
                            "messageId": str(uuid.uuid4()),
                            "kind": "message",
                            "role": "agent",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "Simulated task failure - attempt 1",
                                }
                            ],
                        },
                        "timestamp": "2025-11-04T10:00:00Z",
                    },
                    "artifacts": [],
                },
            },
            timeout=5,
        )
        assert result1.status_code == 200

        # Poll for retry assignment (scheduler should retry after delay)
        retry_task = None
        for attempt in range(20):  # Up to 4 seconds (20 * 0.2s)
            sync2 = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync2.status_code == 200
            sync2_data = sync2.json()
            if sync2_data.get("type") == "task_assigned":
                retry_task = sync2_data["task"]
                break
            time.sleep(0.2)

        assert retry_task is not None, "Scheduler should retry failed task"
        assert retry_task["nodeId"] == "flaky-task"
        assert retry_task["executionId"] == execution_id

        # Simulate second failure
        result2 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "flaky-task",
                    "taskStatus": {
                        "state": "failed",
                        "message": {
                            "messageId": str(uuid.uuid4()),
                            "kind": "message",
                            "role": "agent",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "Simulated task failure - attempt 2",
                                }
                            ],
                        },
                        "timestamp": "2025-11-04T10:00:05Z",
                    },
                    "artifacts": [],
                },
            },
            timeout=5,
        )
        assert result2.status_code == 200

        # Poll for second retry assignment
        retry_task_2 = None
        for attempt in range(20):
            sync3 = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync3.status_code == 200
            sync3_data = sync3.json()
            if sync3_data.get("type") == "task_assigned":
                retry_task_2 = sync3_data["task"]
                break
            time.sleep(0.2)

        assert retry_task_2 is not None, (
            "Scheduler should retry failed task second time"
        )
        assert retry_task_2["nodeId"] == "flaky-task"

        # Simulate success on third attempt
        result3 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "flaky-task",
                    "taskStatus": {
                        "state": "completed",
                        "timestamp": "2025-11-04T10:00:10Z",
                    },
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "name": "output",
                            "description": "Output",
                            "parts": [{"text": "Success on retry"}],
                        }
                    ],
                },
            },
            timeout=5,
        )
        assert result3.status_code == 200

        # Poll until workflow completes
        workflow_complete = False
        for attempt in range(10):
            sync_final = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync_final.status_code == 200
            if sync_final.json().get("type") == "no_action":
                workflow_complete = True
                break
            time.sleep(0.2)

        assert workflow_complete, (
            "Workflow should complete after task succeeds on retry"
        )
