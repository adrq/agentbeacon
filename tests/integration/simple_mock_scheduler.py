"""Simple FastAPI mock orchestrator for AgentMaestro worker testing.

Based on FastAPI basic example pattern: https://fastapi.tiangolo.com/#create-it
"""

from fastapi import FastAPI, HTTPException, Request
from typing import Dict, Any, Optional, List, Literal
from pydantic import BaseModel
import datetime

app = FastAPI()

# Pydantic Models for API validation


class MessagePart(BaseModel):
    kind: Literal["text", "image", "audio"]
    text: Optional[str] = None
    data: Optional[str] = None  # base64 encoded


class A2AMessage(BaseModel):
    role: Literal["user", "agent"]
    parts: List[MessagePart]


class A2ATaskStatus(BaseModel):
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    message: Optional[A2AMessage] = None
    timestamp: str


class A2AArtifact(BaseModel):
    artifactId: str
    name: str
    description: Optional[str] = None
    parts: List[MessagePart]


class CurrentTask(BaseModel):
    nodeId: str
    executionId: str


class TaskResult(BaseModel):
    nodeId: str
    executionId: str
    taskStatus: A2ATaskStatus
    artifacts: Optional[List[A2AArtifact]] = None


class SyncRequest(BaseModel):
    status: Literal["idle", "working", "failed"]
    timestamp: str
    currentTask: Optional[CurrentTask] = None
    taskResult: Optional[TaskResult] = None


class WorkerCommand(BaseModel):
    action: Literal["cancel", "fail"]
    nodeId: Optional[str] = None
    executionId: Optional[str] = None
    reason: Optional[str] = None


class TaskAssignment(BaseModel):
    nodeId: str
    executionId: str
    prompt: str
    agentType: Optional[str] = None  # Allow missing agent for validation testing
    config: Optional[Dict[str, Any]] = None


class SyncResponse(BaseModel):
    type: Literal["no_action", "task_assigned", "command"]
    timestamp: str
    task: Optional[TaskAssignment] = None
    command: Optional[WorkerCommand] = None


class AddCommandRequest(BaseModel):
    executionId: str
    nodeId: str
    command: WorkerCommand


class DowntimeRequest(BaseModel):
    enabled: bool


class StatusResponse(BaseModel):
    status: str


class CountResponse(BaseModel):
    count: int


class SyncStatusResponse(BaseModel):
    commandQueue: Dict[str, int]
    syncCount: int
    taskQueue: int
    resultsCount: int


class HealthResponse(BaseModel):
    status: str


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


@app.post("/api/worker/sync")
def worker_sync(sync_request: SyncRequest, request: Request) -> SyncResponse:
    """Unified worker sync endpoint for bidirectional communication."""
    global sync_count, simulate_downtime
    sync_count += 1

    if simulate_downtime:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    status = sync_request.status

    # No worker tracking needed - workers are truly anonymous

    # Process task result if present
    task_is_final = False
    if sync_request.taskResult:
        task_result = sync_request.taskResult
        # Store the A2A-compliant result directly
        results.append(task_result.dict())

        # Check if task is in final state
        task_state = task_result.taskStatus.state
        task_is_final = task_state in ["completed", "failed", "cancelled"]

    # Check for pending commands based on current task (only if task not final)
    if not task_is_final and sync_request.currentTask:
        current_task = sync_request.currentTask
        task_key = f"{current_task.executionId}:{current_task.nodeId}"
        if task_key in command_queue and command_queue[task_key]:
            command = command_queue[task_key].pop(0)
            return SyncResponse(
                type="command",
                timestamp=datetime.datetime.now().isoformat(),
                command=command,
            )

    # Check for available tasks when worker is idle
    if status == "idle" and task_queue:
        task = task_queue.pop(0)  # FIFO
        task_assignment = TaskAssignment(
            nodeId=task["id"],
            executionId=task.get("executionId", "test-exec-1"),
            prompt=task.get(
                "prompt",
                task.get("request", {}).get(
                    "prompt", task.get("request", {}).get("input", "default task")
                ),
            ),
            agentType=task.get("agent"),
            config=task.get("config", {}),
        )
        return SyncResponse(
            type="task_assigned",
            timestamp=datetime.datetime.now().isoformat(),
            task=task_assignment,
        )

    # Default response - no action needed
    return SyncResponse(type="no_action", timestamp=datetime.datetime.now().isoformat())


@app.post("/add_task")
def add_task_endpoint(task: Dict[str, Any]) -> StatusResponse:
    """Add a task to the queue for testing."""
    task_queue.append(task)
    return StatusResponse(status="added")


@app.get("/results")
def get_results() -> list:
    """Get all submitted results for testing."""
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
