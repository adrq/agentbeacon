"""Simple FastAPI mock orchestrator for AgentMaestro worker testing.

Based on FastAPI basic example pattern: https://fastapi.tiangolo.com/#create-it
"""

from fastapi import FastAPI, HTTPException, Request
from typing import Dict, Any, Optional, List, Literal, Union
from pydantic import BaseModel

app = FastAPI()

# Pydantic Models for API validation with A2A protocol compliance
# Note: A2A protocol types allow extensions, so extra='forbid' is NOT used


class TextPart(BaseModel):
    """A2A TextPart - allows extension fields via metadata."""

    kind: Literal["text"]
    text: str
    metadata: Optional[Dict[str, Any]] = None


class FilePart(BaseModel):
    """A2A FilePart - allows extension fields via metadata."""

    kind: Literal["file"]
    file: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class DataPart(BaseModel):
    """A2A DataPart - allows extension fields via metadata."""

    kind: Literal["data"]
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


Part = Union[TextPart, FilePart, DataPart]


class A2AMessage(BaseModel):
    """A2A Message - allows extension fields per A2A protocol spec."""

    messageId: str
    kind: Literal["message"] = "message"
    role: Literal["user", "agent"]
    parts: List[Part]
    metadata: Optional[Dict[str, Any]] = None
    taskId: Optional[str] = None
    contextId: Optional[str] = None
    extensions: Optional[List[str]] = None
    referenceTaskIds: Optional[List[str]] = None


# class A2ATaskStatus(BaseModel):
#     """A2A TaskStatus - allows extension fields per A2A protocol spec."""

#     state: Literal["pending", "running", "completed", "failed", "cancelled"]
#     message: Optional[A2AMessage] = None
#     timestamp: str


# class A2AArtifact(BaseModel):
#     """A2A Artifact - allows extension fields per A2A protocol spec."""

#     artifactId: str
#     name: Optional[str] = None
#     description: Optional[str] = None
#     parts: List[Part]
#     metadata: Optional[Dict[str, Any]] = None
#     extensions: Optional[List[str]] = None


class CurrentTask(BaseModel):
    executionId: str
    nodeId: str

    class Config:
        extra = "forbid"


class Part(BaseModel):
    """A2A Protocol Part union type."""

    kind: Literal["text", "file", "data"]
    text: Optional[str] = None
    data: Optional[str] = None
    mimeType: Optional[str] = None

    class Config:
        extra = "forbid"


class Message(BaseModel):
    """A2A Protocol Message structure."""

    messageId: str
    kind: Literal["message"]
    role: Literal["user", "agent"]
    parts: List[Part]

    class Config:
        extra = "forbid"


class A2ATaskStatus(BaseModel):
    """A2A Protocol TaskStatus structure."""

    state: Literal["completed", "failed", "canceled", "rejected"]
    message: Optional[Message] = None
    timestamp: Optional[str] = None

    class Config:
        extra = "forbid"


class A2AArtifact(BaseModel):
    """A2A Protocol Artifact structure."""

    artifactId: str
    name: str
    description: Optional[str] = None
    parts: List[Part]

    class Config:
        extra = "forbid"


class TaskResult(BaseModel):
    """Worker TaskResult with A2A types."""

    executionId: str
    nodeId: str
    taskStatus: A2ATaskStatus
    artifacts: Optional[List[A2AArtifact]] = None

    class Config:
        extra = "forbid"


class SyncRequest(BaseModel):
    status: Literal["idle", "working"]
    currentTask: Optional[CurrentTask] = None
    taskResult: Optional[TaskResult] = None

    class Config:
        extra = "forbid"


class TaskAssignment(BaseModel):
    executionId: str
    nodeId: str
    agent: str
    task: Dict[str, Any]
    workflowRegistryId: Optional[str] = None
    workflowVersion: Optional[str] = None
    workflowRef: Optional[str] = None
    protocolMetadata: Optional[Dict[str, Any]] = None

    class Config:
        extra = "allow"  # Schema says additionalProperties: true


class SyncResponse(BaseModel):
    type: Literal["no_action", "task_assigned", "command"]
    task: Optional[TaskAssignment] = None
    command: Optional[Literal["cancel", "shutdown"]] = None

    class Config:
        extra = "forbid"
        # Skip null values to match canonical schema's additionalProperties: false
        exclude_none = True


class AddCommandRequest(BaseModel):
    executionId: str
    nodeId: str
    command: Literal["cancel", "shutdown"]

    class Config:
        extra = "forbid"


class DowntimeRequest(BaseModel):
    enabled: bool

    class Config:
        extra = "forbid"


class StatusResponse(BaseModel):
    status: str

    class Config:
        extra = "forbid"


class CountResponse(BaseModel):
    count: int

    class Config:
        extra = "forbid"


class SyncStatusResponse(BaseModel):
    commandQueue: Dict[str, int]
    syncCount: int
    taskQueue: int
    resultsCount: int

    class Config:
        extra = "forbid"


class HealthResponse(BaseModel):
    status: str

    class Config:
        extra = "forbid"


# Simple in-memory task queue and results storage
task_queue = []
results = []
simulate_downtime = False
poll_count = 0

# Worker sync endpoint storage (no worker tracking needed for anonymous workers)
command_queue = {}  # executionId+nodeId -> list of commands
sync_count = 0

# @app.get("/api/worker/poll")
# def poll_for_task() -> Dict[str, Any]:
#     """Poll for available tasks."""
#     global poll_count, simulate_downtime
#     poll_count += 1

#     if simulate_downtime:
#         raise HTTPException(status_code=503, detail="Service temporarily unavailable")

#     if task_queue:
#         task = task_queue.pop(0)  # FIFO
#         return {"task": task}
#     return {"task": None}

# @app.post("/api/worker/result")
# def submit_result(result: Dict[str, Any]) -> Dict[str, bool]:
#     """Accept task results."""
#     global simulate_downtime
#     if simulate_downtime:
#         raise HTTPException(status_code=503, detail="Service temporarily unavailable")

#     # Store results for testing
#     results.append(result)
#     return {"accepted": True}


@app.post("/api/worker/sync", response_model_exclude_none=True)
def worker_sync(sync_request: SyncRequest, request: Request) -> SyncResponse:
    """Unified worker sync endpoint for bidirectional communication."""
    global sync_count, simulate_downtime
    sync_count += 1

    if simulate_downtime:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    status = sync_request.status

    # No worker tracking needed - workers are truly anonymous

    # Process task result if present
    if sync_request.taskResult:
        task_result = sync_request.taskResult
        # Store the result
        results.append(task_result.dict())
        # Per worker-sync-protocol.md: acknowledge result with no_action
        # Worker expects acknowledgment before receiving new tasks
        return SyncResponse(type="no_action")

    # Check for pending commands based on current task
    if sync_request.currentTask:
        current_task = sync_request.currentTask
        task_key = f"{current_task.executionId}:{current_task.nodeId}"
        if task_key in command_queue and command_queue[task_key]:
            command = command_queue[task_key].pop(0)
            return SyncResponse(
                type="command",
                command=command,
            )

    # Check for available tasks when worker is idle
    if status == "idle" and task_queue:
        task_payload = task_queue.pop(0)  # FIFO
        task_assignment = TaskAssignment(
            executionId=task_payload.get("executionId", "test-exec-1"),
            nodeId=task_payload["nodeId"],
            agent=task_payload["agent"],
            task=task_payload["task"],
            workflowRegistryId=task_payload.get("workflowRegistryId"),
            workflowVersion=task_payload.get("workflowVersion"),
            workflowRef=task_payload.get("workflowRef"),
            protocolMetadata=task_payload.get("protocolMetadata"),
        )
        return SyncResponse(
            type="task_assigned",
            task=task_assignment,
        )

    # Default response - no action needed
    return SyncResponse(type="no_action")


@app.post("/add_task")
def add_task_endpoint(task: Dict[str, Any]) -> StatusResponse:
    """Add a task to the queue for testing."""
    required_fields = {
        "nodeId",
        "executionId",
        "workflowRegistryId",
        "workflowVersion",
        "workflowRef",
        "agent",
        "task",
    }
    missing = sorted(required_fields - task.keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error": f"Missing required fields: {', '.join(missing)}"},
        )

    if not isinstance(task["task"], dict):
        raise HTTPException(
            status_code=422, detail={"error": "task payload must be an object"}
        )

    task_queue.append(task)
    return StatusResponse(status="added")


@app.get("/results")
def get_results() -> list:
    """Get all submitted results for testing.

    Worker now sends A2A-compliant TaskResult format directly.
    """
    return results


@app.post("/clear")
def clear_all() -> StatusResponse:
    """Clear tasks and results for testing."""
    task_queue.clear()
    results.clear()
    command_queue.clear()
    return StatusResponse(status="cleared")


@app.post("/test/add_command")
def add_command_for_testing(command_data: AddCommandRequest) -> StatusResponse:
    """Queue a command for a specific execution/node for testing."""
    task_key = f"{command_data.executionId}:{command_data.nodeId}"
    if task_key not in command_queue:
        command_queue[task_key] = []
    command_queue[task_key].append(command_data.command)

    return StatusResponse(status="command queued")


@app.get("/test/sync_status")
def get_sync_status() -> SyncStatusResponse:
    """Get current sync endpoint status for testing."""
    return SyncStatusResponse(
        commandQueue={k: len(v) for k, v in command_queue.items()},
        syncCount=sync_count,
        taskQueue=len(task_queue),
        resultsCount=len(results),
    )


@app.post("/simulate_downtime")
def set_downtime_simulation(downtime: DowntimeRequest) -> StatusResponse:
    """Enable or disable downtime simulation."""
    global simulate_downtime
    simulate_downtime = downtime.enabled
    return StatusResponse(
        status=f"downtime simulation {'enabled' if simulate_downtime else 'disabled'}"
    )


@app.get("/poll_count")
def get_poll_count() -> CountResponse:
    """Get the number of poll requests received."""
    return CountResponse(count=poll_count)


@app.get("/sync_count")
def get_sync_count() -> CountResponse:
    """Get the number of sync requests received."""
    return CountResponse(count=sync_count)


@app.get("/api/health")
def health_check() -> HealthResponse:
    """Health check endpoint."""
    global simulate_downtime
    if simulate_downtime:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return HealthResponse(status="ok")


def clear_tasks() -> None:
    """Clear all tasks from the queue."""
    task_queue.clear()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9456)
