"""Python E2E tests for UI workflow monitoring event logging.

These tests verify backend event logging behavior for the UI monitoring feature.
They are written FIRST (TDD approach) and MUST FAIL initially because event
logging is not yet implemented in the scheduler.

Testing approach:
- Start scheduler with test database
- Submit workflows via A2A protocol
- Query execution_events table directly via database connection
- Assert event sequence and metadata
"""

import sqlite3
import time
import uuid

import psycopg2
import pytest
import requests
from psycopg2.extras import RealDictCursor

from tests.testhelpers import scheduler_context, get_a2a_endpoint

pytestmark = pytest.mark.skip(reason="Deferred: DAG model removed")


def get_execution_events(db_url: str, execution_id: str) -> list[dict]:
    """Query execution_events table and return events ordered by timestamp.

    Args:
        db_url: Database URL (sqlite: or postgres:)
        execution_id: Execution ID to filter events

    Returns:
        List of event dictionaries with keys: id, execution_id, event_type,
        task_id, message, metadata, timestamp
    """
    if db_url.startswith("sqlite"):
        # Extract path from SQLite URL (format: sqlite:path?mode=rwc)
        db_path = db_url.split("?")[0].replace("sqlite:", "")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, execution_id, event_type, task_id, message, metadata, timestamp
            FROM execution_events
            WHERE execution_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (execution_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        # Convert Row objects to dicts
        return [dict(row) for row in rows]

    elif db_url.startswith("postgres"):
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            SELECT id, execution_id, event_type, task_id, message, metadata, timestamp
            FROM execution_events
            WHERE execution_id = %s
            ORDER BY timestamp ASC, id ASC
            """,
            (execution_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        # Convert to regular dicts
        return [dict(row) for row in rows]

    else:
        raise ValueError(f"Unsupported database URL: {db_url}")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_event_sequence_happy_path(test_database):
    """Test that all 6 event types are logged in correct order for successful workflow.

    Expected event sequence:
    1. execution_started (task_id=null)
    2. task_assigned (task_id=task1)
    3. task_assigned (task_id=task2)
    4. task_completed (task_id=task1)
    5. task_completed (task_id=task2)
    6. execution_completed (task_id=null)
    """
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Simple 2-task sequential workflow
        workflow_yaml = """
name: test-happy-path
description: Simple 2-task workflow for event logging test
tasks:
  - id: task1
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-task1"
        kind: message
        parts:
          - kind: text
            text: Execute task 1
  - id: task2
    agent: mock-agent
    depends_on: [task1]
    task:
      message:
        role: user
        messageId: "msg-task2"
        kind: message
        parts:
          - kind: text
            text: Execute task 2
""".strip()

        # Register workflow
        register_response = requests.post(
            f"{scheduler_url}/api/registry/workflows",
            json={
                "namespace": "test",
                "name": "test-happy-path",
                "version": "1.0.0",
                "isLatest": True,
                "workflowYaml": workflow_yaml,
            },
            timeout=10,
        )
        assert register_response.status_code == 201
        workflow_ref = register_response.json()["workflowRegistryId"]

        # Submit workflow via A2A protocol
        a2a_endpoint = get_a2a_endpoint(scheduler_url)
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

        execute_response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=10)
        assert execute_response.status_code == 200
        execute_data = execute_response.json()
        assert "result" in execute_data

        # Execute workflow via worker sync polling
        # Task 1: Get assignment
        sync1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert sync1.status_code == 200
        sync1_data = sync1.json()
        assert sync1_data["type"] == "task_assigned"
        task1 = sync1_data["task"]
        assert task1["nodeId"] == "task1"
        execution_id = task1["executionId"]

        # Task 1: Submit completion
        result1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "task1",
                    "taskStatus": {
                        "state": "completed",
                        "timestamp": "2025-11-04T10:00:00Z",
                    },
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "name": "task1-output",
                            "description": "Task 1 output",
                            "parts": [{"text": "Task 1 completed"}],
                        }
                    ],
                },
            },
            timeout=5,
        )
        assert result1.status_code == 200

        # Task 2: Poll for assignment (task1 dependency now satisfied)
        task2 = None
        for attempt in range(10):
            sync2 = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync2.status_code == 200
            sync2_data = sync2.json()
            if sync2_data.get("type") == "task_assigned":
                task2 = sync2_data["task"]
                break
            time.sleep(0.2)

        assert task2 is not None, "Task2 must be assigned after task1 completes"
        assert task2["nodeId"] == "task2"

        # Task 2: Submit completion
        result2 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "task2",
                    "taskStatus": {
                        "state": "completed",
                        "timestamp": "2025-11-04T10:01:00Z",
                    },
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "name": "task2-output",
                            "description": "Task 2 output",
                            "parts": [{"text": "Task 2 completed"}],
                        }
                    ],
                },
            },
            timeout=5,
        )
        assert result2.status_code == 200

        # Poll until workflow completes (no_action response)
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

        assert workflow_complete, "Workflow must complete after both tasks succeed"

        # Allow time for event writes to database
        time.sleep(0.2)

        # Query execution_events table
        events = get_execution_events(test_database, execution_id)

        # Assert exact event sequence (chronological order)
        assert len(events) == 6, f"Expected exactly 6 events, got {len(events)}"

        # Extract ordered event types
        event_types_ordered = [e["event_type"] for e in events]

        # Expected sequence per DAG execution model (task2 depends_on task1)
        # task2 can only be assigned AFTER task1 completes
        expected_sequence = [
            "execution_started",
            "task_assigned",  # task1 (no dependencies, assigned first)
            "task_completed",  # task1 (worker completes it)
            "task_assigned",  # task2 (assigned AFTER task1 completes)
            "task_completed",  # task2 (worker completes it)
            "execution_completed",
        ]

        assert event_types_ordered == expected_sequence, (
            f"Events not in expected chronological order. "
            f"Expected: {expected_sequence}, Got: {event_types_ordered}"
        )

        # Verify event details in chronological order
        # Event 0: execution_started
        assert events[0]["event_type"] == "execution_started"
        assert events[0]["task_id"] is None or events[0]["task_id"] == ""
        assert events[0]["execution_id"] == execution_id

        # Event 1: task_assigned (task1)
        assert events[1]["event_type"] == "task_assigned"
        assert events[1]["task_id"] == "task1"
        assert events[1]["execution_id"] == execution_id

        # Event 2: task_completed (task1)
        assert events[2]["event_type"] == "task_completed"
        assert events[2]["task_id"] == "task1"
        assert events[2]["execution_id"] == execution_id

        # Event 3: task_assigned (task2) - AFTER task1 completes
        assert events[3]["event_type"] == "task_assigned"
        assert events[3]["task_id"] == "task2"
        assert events[3]["execution_id"] == execution_id

        # Event 4: task_completed (task2)
        assert events[4]["event_type"] == "task_completed"
        assert events[4]["task_id"] == "task2"
        assert events[4]["execution_id"] == execution_id

        # Event 5: execution_completed
        assert events[5]["event_type"] == "execution_completed"
        assert events[5]["task_id"] is None or events[5]["task_id"] == ""
        assert events[5]["execution_id"] == execution_id

        # Also verify API endpoint returns same events (frontend contract)
        api_response = requests.get(
            f"{scheduler_url}/api/executions/{execution_id}",
            timeout=5,
        )
        assert api_response.status_code == 200
        api_data = api_response.json()

        # API should return events array matching database in chronological order
        api_events = api_data.get("events", [])
        assert len(api_events) == 6, (
            f"API should return exactly 6 events, got {len(api_events)}"
        )

        # Verify API events are in same chronological order
        api_event_types = [e["event_type"] for e in api_events]
        assert api_event_types == expected_sequence, (
            f"API events not in expected chronological order. "
            f"Expected: {expected_sequence}, Got: {api_event_types}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_event_sequence_with_retries(test_database):
    """Test that task_retrying events are logged with attempt counts.

    Expected events:
    - task_assigned (attempt 0)
    - task_retrying (metadata.attempt=1, metadata.max_retries=3)
    - task_retrying (metadata.attempt=2, metadata.max_retries=3)
    - task_completed (final success)
    """
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Register workflow with retry policy (execution.retry.attempts=3)
        workflow_yaml = """
name: test-retries
description: Workflow to test retry event logging
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
        messageId: "msg-flaky"
        kind: message
        parts:
          - kind: text
            text: Execute flaky task
""".strip()

        # Register workflow
        register_response = requests.post(
            f"{scheduler_url}/api/registry/workflows",
            json={
                "namespace": "test",
                "name": "test-retries",
                "version": "1.0.0",
                "isLatest": True,
                "workflowYaml": workflow_yaml,
            },
            timeout=10,
        )
        assert register_response.status_code == 201
        workflow_ref = register_response.json()["workflowRegistryId"]

        # Submit workflow via A2A
        a2a_endpoint = get_a2a_endpoint(scheduler_url)
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

        execute_response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=10)
        assert execute_response.status_code == 200

        # Get task assignment
        sync1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert sync1.status_code == 200
        task1 = sync1.json()["task"]
        execution_id = task1["executionId"]

        # Simulate task failure (first attempt)
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

        # Poll for retry assignment (attempt 2)
        retry_task_2 = None
        for attempt in range(10):
            sync2 = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync2.status_code == 200
            sync2_data = sync2.json()
            if sync2_data.get("type") == "task_assigned":
                retry_task_2 = sync2_data["task"]
                break
            time.sleep(0.2)

        assert retry_task_2 is not None, (
            "Scheduler must assign retry task after first failure"
        )
        assert retry_task_2["nodeId"] == "flaky-task"

        # Simulate second failure (attempt 2)
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

        # Poll for retry assignment (attempt 3)
        retry_task_3 = None
        for attempt in range(10):
            sync3 = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync3.status_code == 200
            sync3_data = sync3.json()
            if sync3_data.get("type") == "task_assigned":
                retry_task_3 = sync3_data["task"]
                break
            time.sleep(0.2)

        assert retry_task_3 is not None, (
            "Scheduler must assign retry task after second failure"
        )
        assert retry_task_3["nodeId"] == "flaky-task"

        # Simulate success (attempt 3)
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

        # Poll until workflow completes (no_action response)
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

        assert workflow_complete, "Workflow must complete after task success"

        # Allow time for event writes to database
        time.sleep(0.2)

        # Query events
        events = get_execution_events(test_database, execution_id)

        # Assert retry events exist
        retry_events = [e for e in events if e["event_type"] == "task_retrying"]
        assert len(retry_events) == 2, (
            f"Expected exactly 2 task_retrying events, got {len(retry_events)}"
        )

        # Verify first retry event: attempt=1, max_retries=2 (attempts:3 = 1 initial + 2 retries)
        import json

        retry1_metadata = (
            json.loads(retry_events[0]["metadata"])
            if isinstance(retry_events[0]["metadata"], str)
            else retry_events[0]["metadata"]
        )
        assert retry1_metadata.get("attempt") == 1, (
            f"First retry should have attempt=1, got {retry1_metadata.get('attempt')}"
        )
        assert retry1_metadata.get("max_retries") == 2, (
            f"First retry should have max_retries=2 (attempts:3 = 1 initial + 2 retries), got {retry1_metadata.get('max_retries')}"
        )
        assert retry_events[0]["task_id"] == "flaky-task"

        # Verify second retry event: attempt=2, max_retries=2 (attempts:3 = 1 initial + 2 retries)
        retry2_metadata = (
            json.loads(retry_events[1]["metadata"])
            if isinstance(retry_events[1]["metadata"], str)
            else retry_events[1]["metadata"]
        )
        assert retry2_metadata.get("attempt") == 2, (
            f"Second retry should have attempt=2, got {retry2_metadata.get('attempt')}"
        )
        assert retry2_metadata.get("max_retries") == 2, (
            f"Second retry should have max_retries=2 (attempts:3 = 1 initial + 2 retries), got {retry2_metadata.get('max_retries')}"
        )
        assert retry_events[1]["task_id"] == "flaky-task"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_event_sequence_with_failure(test_database):
    """Test that task_failed and execution_failed events are logged.

    Expected events:
    - task_assigned
    - task_failed (metadata.error populated with error message)
    - execution_failed (metadata.failed_task=task_id, metadata.error present)
    """
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Register simple workflow (failure behavior controlled by task result submission)
        workflow_yaml = """
name: test-failure
description: Workflow to test failure event logging
tasks:
  - id: failing-task
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-fail"
        kind: message
        parts:
          - kind: text
            text: Execute failing task
""".strip()

        # Register workflow
        register_response = requests.post(
            f"{scheduler_url}/api/registry/workflows",
            json={
                "namespace": "test",
                "name": "test-failure",
                "version": "1.0.0",
                "isLatest": True,
                "workflowYaml": workflow_yaml,
            },
            timeout=10,
        )
        assert register_response.status_code == 201
        workflow_ref = register_response.json()["workflowRegistryId"]

        # Submit workflow via A2A
        a2a_endpoint = get_a2a_endpoint(scheduler_url)
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

        execute_response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=10)
        assert execute_response.status_code == 200

        # Get task assignment
        sync1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert sync1.status_code == 200
        task1 = sync1.json()["task"]
        execution_id = task1["executionId"]

        # Simulate task failure (no retries)
        result1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "failing-task",
                    "taskStatus": {
                        "state": "failed",
                        "message": {
                            "messageId": str(uuid.uuid4()),
                            "kind": "message",
                            "role": "agent",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "Task execution failed: permanent failure simulated",
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

        # Poll until workflow fails (no_action response - no more tasks)
        workflow_failed = False
        for attempt in range(10):
            sync_final = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync_final.status_code == 200
            if sync_final.json().get("type") == "no_action":
                workflow_failed = True
                break
            time.sleep(0.2)

        assert workflow_failed, "Workflow must reach failed state after task failure"

        # Allow time for event writes to database
        time.sleep(0.2)

        # Query events
        events = get_execution_events(test_database, execution_id)

        # Assert task_failed event exists
        task_failed_events = [e for e in events if e["event_type"] == "task_failed"]
        assert len(task_failed_events) >= 1, (
            f"Expected at least 1 task_failed event, got {len(task_failed_events)}"
        )

        # Check task_failed metadata contains error
        import json

        task_failed = task_failed_events[0]
        assert task_failed["task_id"] == "failing-task"
        metadata = (
            json.loads(task_failed["metadata"])
            if isinstance(task_failed["metadata"], str)
            else task_failed["metadata"]
        )
        assert "error" in metadata, "task_failed event should have error in metadata"
        assert "permanent failure simulated" in metadata["error"].lower(), (
            f"Expected actual error message from worker, got: {metadata['error']}"
        )

        # Assert execution_failed event exists
        execution_failed_events = [
            e for e in events if e["event_type"] == "execution_failed"
        ]
        assert len(execution_failed_events) >= 1, (
            f"Expected at least 1 execution_failed event, got {len(execution_failed_events)}"
        )

        # Check execution_failed metadata
        exec_failed = execution_failed_events[0]
        assert exec_failed["task_id"] is None or exec_failed["task_id"] == ""
        metadata = (
            json.loads(exec_failed["metadata"])
            if isinstance(exec_failed["metadata"], str)
            else exec_failed["metadata"]
        )
        assert "failed_task" in metadata, (
            "execution_failed should have failed_task in metadata"
        )
        assert "error" in metadata, "execution_failed should have error in metadata"
        assert "permanent failure simulated" in metadata["error"].lower(), (
            f"Expected actual error message from worker in execution_failed, got: {metadata['error']}"
        )
        assert metadata["failed_task"] == "failing-task"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_event_sequence_inline_yaml_submission(test_database):
    """Test event logging with INLINE YAML submission (direct format - RECOMMENDED).

    This test demonstrates the correct A2A-compliant format for inline workflow
    submission, as the UI will use.

    Uses direct format: {"kind": "data", "data": {"workflowYaml": "yaml"}}
    (NOT nested format: {"kind": "data", "data": {"data": {"workflowYaml": "..."}}})

    Expected event sequence per DAG execution model (task2 depends_on task1):
    1. execution_started
    2. task_assigned (task1)
    3. task_completed (task1)
    4. task_assigned (task2) - assigned AFTER task1 completes
    5. task_completed (task2)
    6. execution_completed
    """
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Inline workflow YAML (as UI will submit)
        workflow_yaml = """
name: test-inline-yaml
description: Test inline YAML submission with direct format
tasks:
  - id: task1
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-task1"
        kind: message
        parts:
          - kind: text
            text: Execute task 1
  - id: task2
    agent: mock-agent
    depends_on: [task1]
    task:
      message:
        role: user
        messageId: "msg-task2"
        kind: message
        parts:
          - kind: text
            text: Execute task 2
""".strip()

        # Submit workflow via A2A with INLINE YAML (direct format - RECOMMENDED)
        a2a_endpoint = get_a2a_endpoint(scheduler_url)
        message = {
            "role": "user",
            "parts": [
                {
                    "kind": "data",
                    "data": {"workflowYaml": workflow_yaml},
                }  # Direct format ✅
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

        execute_response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=10)
        assert execute_response.status_code == 200
        execute_data = execute_response.json()
        assert "result" in execute_data

        # Execute workflow via worker sync polling
        # Task 1: Get assignment
        sync1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            timeout=5,
        )
        assert sync1.status_code == 200
        sync1_data = sync1.json()
        assert sync1_data["type"] == "task_assigned"
        task1 = sync1_data["task"]
        assert task1["nodeId"] == "task1"
        execution_id = task1["executionId"]

        # Task 1: Submit completion
        result1 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "task1",
                    "taskStatus": {
                        "state": "completed",
                        "timestamp": "2025-11-04T10:00:00Z",
                    },
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "name": "task1-output",
                            "description": "Task 1 output",
                            "parts": [{"text": "Task 1 completed"}],
                        }
                    ],
                },
            },
            timeout=5,
        )
        assert result1.status_code == 200

        # Task 2: Poll for assignment (task1 dependency now satisfied)
        task2 = None
        for attempt in range(10):
            sync2 = requests.post(
                f"{scheduler_url}/api/worker/sync",
                json={"status": "idle"},
                timeout=5,
            )
            assert sync2.status_code == 200
            sync2_data = sync2.json()
            if sync2_data.get("type") == "task_assigned":
                task2 = sync2_data["task"]
                break
            time.sleep(0.2)

        assert task2 is not None, "Task2 must be assigned after task1 completes"
        assert task2["nodeId"] == "task2"

        # Task 2: Submit completion
        result2 = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={
                "status": "idle",
                "taskResult": {
                    "executionId": execution_id,
                    "nodeId": "task2",
                    "taskStatus": {
                        "state": "completed",
                        "timestamp": "2025-11-04T10:01:00Z",
                    },
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "name": "task2-output",
                            "description": "Task 2 output",
                            "parts": [{"text": "Task 2 completed"}],
                        }
                    ],
                },
            },
            timeout=5,
        )
        assert result2.status_code == 200

        # Poll until workflow completes (no_action response)
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

        assert workflow_complete, "Workflow must complete after both tasks succeed"

        # Allow time for event writes to database
        time.sleep(0.2)

        # Query execution_events table
        events = get_execution_events(test_database, execution_id)

        # Assert exact event sequence (chronological order)
        assert len(events) == 6, f"Expected exactly 6 events, got {len(events)}"

        # Extract ordered event types
        event_types_ordered = [e["event_type"] for e in events]

        # Expected sequence per DAG execution model (task2 depends_on task1)
        # task2 can only be assigned AFTER task1 completes
        expected_sequence = [
            "execution_started",
            "task_assigned",  # task1 (no dependencies, assigned first)
            "task_completed",  # task1 (worker completes it)
            "task_assigned",  # task2 (assigned AFTER task1 completes)
            "task_completed",  # task2 (worker completes it)
            "execution_completed",
        ]

        assert event_types_ordered == expected_sequence, (
            f"Events not in expected chronological order. "
            f"Expected: {expected_sequence}, Got: {event_types_ordered}"
        )

        # Verify event details in chronological order
        # Event 0: execution_started
        assert events[0]["event_type"] == "execution_started"
        assert events[0]["task_id"] is None or events[0]["task_id"] == ""
        assert events[0]["execution_id"] == execution_id

        # Event 1: task_assigned (task1)
        assert events[1]["event_type"] == "task_assigned"
        assert events[1]["task_id"] == "task1"
        assert events[1]["execution_id"] == execution_id

        # Event 2: task_completed (task1)
        assert events[2]["event_type"] == "task_completed"
        assert events[2]["task_id"] == "task1"
        assert events[2]["execution_id"] == execution_id

        # Event 3: task_assigned (task2) - AFTER task1 completes
        assert events[3]["event_type"] == "task_assigned"
        assert events[3]["task_id"] == "task2"
        assert events[3]["execution_id"] == execution_id

        # Event 4: task_completed (task2)
        assert events[4]["event_type"] == "task_completed"
        assert events[4]["task_id"] == "task2"
        assert events[4]["execution_id"] == execution_id

        # Event 5: execution_completed
        assert events[5]["event_type"] == "execution_completed"
        assert events[5]["task_id"] is None or events[5]["task_id"] == ""
        assert events[5]["execution_id"] == execution_id

        # Also verify API endpoint returns same events in chronological order
        api_response = requests.get(
            f"{scheduler_url}/api/executions/{execution_id}",
            timeout=5,
        )
        assert api_response.status_code == 200
        api_data = api_response.json()
        api_events = api_data.get("events", [])
        assert len(api_events) == 6, (
            f"API should return exactly 6 events, got {len(api_events)}"
        )

        # Verify API events are in same chronological order
        api_event_types = [e["event_type"] for e in api_events]
        assert api_event_types == expected_sequence, (
            f"API events not in expected chronological order. "
            f"Expected: {expected_sequence}, Got: {api_event_types}"
        )
