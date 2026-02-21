"""Simple FastAPI mock scheduler for AgentBeacon worker testing.

Implements the new session-based sync protocol for worker-in-isolation tests.
"""

from fastapi import FastAPI
from typing import Dict, Any, Optional, List, Literal
from pydantic import BaseModel

app = FastAPI()

# Pydantic Models for session-based sync protocol


class SessionResult(BaseModel):
    """Worker-reported session result."""

    sessionId: str
    agentSessionId: Optional[str] = None
    output: Optional[Any] = None
    error: Optional[str] = None
    errorKind: Optional[str] = None
    stderr: Optional[str] = None

    class Config:
        extra = "forbid"


class SessionState(BaseModel):
    """Worker-reported session state."""

    sessionId: str
    status: Literal["running", "waiting_for_event"]
    agentSessionId: Optional[str] = None

    class Config:
        extra = "forbid"


class WorkerSyncRequest(BaseModel):
    """Worker sync request - can contain sessionResult and/or sessionState."""

    sessionResult: Optional[SessionResult] = None
    sessionState: Optional[SessionState] = None

    class Config:
        extra = "forbid"


class TaskPayload(BaseModel):
    """Task payload for session assignment or prompt delivery."""

    executionId: str
    sessionId: str
    taskPayload: Any

    class Config:
        extra = "allow"


class NoActionResponse(BaseModel):
    """No action response."""

    type: Literal["no_action"] = "no_action"

    class Config:
        extra = "forbid"


class SessionAssignedResponse(BaseModel):
    """Session assigned response."""

    type: Literal["session_assigned"] = "session_assigned"
    sessionId: str
    task: TaskPayload

    class Config:
        extra = "forbid"


class PromptDeliveryResponse(BaseModel):
    """Prompt delivery response."""

    type: Literal["prompt_delivery"] = "prompt_delivery"
    sessionId: str
    task: TaskPayload

    class Config:
        extra = "forbid"


class SessionCompleteResponse(BaseModel):
    """Session complete response."""

    type: Literal["session_complete"] = "session_complete"
    sessionId: str

    class Config:
        extra = "forbid"


class CommandResponse(BaseModel):
    """Command response."""

    type: Literal["command"] = "command"
    command: Literal["cancel", "shutdown"]

    class Config:
        extra = "forbid"


WorkerSyncResponse = (
    NoActionResponse
    | SessionAssignedResponse
    | PromptDeliveryResponse
    | SessionCompleteResponse
    | CommandResponse
)


class EnqueueSessionRequest(BaseModel):
    """Test endpoint: enqueue session assignment."""

    sessionId: str
    executionId: str
    taskPayload: Any

    class Config:
        extra = "forbid"


class EnqueuePromptRequest(BaseModel):
    """Test endpoint: enqueue prompt delivery."""

    sessionId: str
    executionId: str
    taskPayload: Any

    class Config:
        extra = "forbid"


class MarkCompleteRequest(BaseModel):
    """Test endpoint: mark session as complete."""

    sessionId: str

    class Config:
        extra = "forbid"


class SendCommandRequest(BaseModel):
    """Test endpoint: send command."""

    command: Literal["cancel", "shutdown"]

    class Config:
        extra = "forbid"


class StatusResponse(BaseModel):
    """Generic status response."""

    status: str

    class Config:
        extra = "forbid"


class HealthResponse(BaseModel):
    """Health check response."""

    status: str

    class Config:
        extra = "forbid"


# In-memory state
session_queue: List[Dict[str, Any]] = []
prompt_queues: Dict[str, List[Dict[str, Any]]] = {}
complete_sessions: set[str] = set()
command_queue: List[str] = []
results: List[Dict[str, Any]] = []
sync_log: List[Dict[str, Any]] = []
worker_events: List[Dict[str, Any]] = []


@app.post("/api/worker/sync")
def worker_sync(sync_request: WorkerSyncRequest) -> WorkerSyncResponse:
    """Main worker sync endpoint implementing the session-based state machine."""
    sync_log.append(sync_request.dict(exclude_none=True))

    if sync_request.sessionResult:
        results.append(sync_request.sessionResult.dict())

    if sync_request.sessionState:
        session_state = sync_request.sessionState
        session_id = session_state.sessionId

        if session_state.status == "waiting_for_event":
            if session_id in prompt_queues and prompt_queues[session_id]:
                prompt_data = prompt_queues[session_id].pop(0)
                return PromptDeliveryResponse(
                    sessionId=session_id,
                    task=TaskPayload(
                        executionId=prompt_data["executionId"],
                        sessionId=session_id,
                        taskPayload=prompt_data["taskPayload"],
                    ),
                )

            if session_id in complete_sessions:
                complete_sessions.remove(session_id)
                return SessionCompleteResponse(sessionId=session_id)

            if command_queue:
                command = command_queue.pop(0)
                return CommandResponse(command=command)

            return NoActionResponse()

        elif session_state.status == "running":
            # Section 5a: return queued prompt immediately when result is reported
            if sync_request.sessionResult:
                if session_id in prompt_queues and prompt_queues[session_id]:
                    prompt_data = prompt_queues[session_id].pop(0)
                    return PromptDeliveryResponse(
                        sessionId=session_id,
                        task=TaskPayload(
                            executionId=prompt_data["executionId"],
                            sessionId=session_id,
                            taskPayload=prompt_data["taskPayload"],
                        ),
                    )

            if command_queue:
                command = command_queue.pop(0)
                return CommandResponse(command=command)

            return NoActionResponse()

    if sync_request.sessionResult and not sync_request.sessionState:
        session_id = sync_request.sessionResult.sessionId

        if session_id in prompt_queues and prompt_queues[session_id]:
            prompt_data = prompt_queues[session_id].pop(0)
            return PromptDeliveryResponse(
                sessionId=session_id,
                task=TaskPayload(
                    executionId=prompt_data["executionId"],
                    sessionId=session_id,
                    taskPayload=prompt_data["taskPayload"],
                ),
            )

        if session_id in complete_sessions:
            complete_sessions.remove(session_id)
            return SessionCompleteResponse(sessionId=session_id)

    if session_queue:
        session_data = session_queue.pop(0)
        return SessionAssignedResponse(
            sessionId=session_data["sessionId"],
            task=TaskPayload(
                executionId=session_data["executionId"],
                sessionId=session_data["sessionId"],
                taskPayload=session_data["taskPayload"],
            ),
        )

    if command_queue:
        command = command_queue.pop(0)
        return CommandResponse(command=command)

    return NoActionResponse()


@app.post("/api/worker/events")
def worker_event(request: Dict[str, Any]) -> StatusResponse:
    """Accept mid-turn message events from the worker."""
    worker_events.append(request)
    return StatusResponse(status="event received")


@app.post("/test/enqueue_session")
def enqueue_session(request: EnqueueSessionRequest) -> StatusResponse:
    """Test endpoint: add a session assignment to the queue."""
    session_queue.append(
        {
            "sessionId": request.sessionId,
            "executionId": request.executionId,
            "taskPayload": request.taskPayload,
        }
    )
    return StatusResponse(status="session enqueued")


@app.post("/test/enqueue_prompt")
def enqueue_prompt(request: EnqueuePromptRequest) -> StatusResponse:
    """Test endpoint: add a prompt delivery for a session."""
    if request.sessionId not in prompt_queues:
        prompt_queues[request.sessionId] = []
    prompt_queues[request.sessionId].append(
        {
            "executionId": request.executionId,
            "taskPayload": request.taskPayload,
        }
    )
    return StatusResponse(status="prompt enqueued")


@app.post("/test/mark_complete")
def mark_complete(request: MarkCompleteRequest) -> StatusResponse:
    """Test endpoint: mark a session as complete."""
    complete_sessions.add(request.sessionId)
    return StatusResponse(status="session marked complete")


@app.post("/test/send_command")
def send_command(request: SendCommandRequest) -> StatusResponse:
    """Test endpoint: queue a command."""
    command_queue.append(request.command)
    return StatusResponse(status="command queued")


@app.get("/test/results")
def get_results() -> List[Dict[str, Any]]:
    """Test endpoint: retrieve reported sessionResult payloads."""
    return results


@app.get("/test/sync_log")
def get_sync_log() -> List[Dict[str, Any]]:
    """Test endpoint: retrieve all sync requests received."""
    return sync_log


@app.get("/test/events")
def get_worker_events() -> List[Dict[str, Any]]:
    """Test endpoint: retrieve all worker message events."""
    return worker_events


@app.get("/api/health")
def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok")


@app.post("/test/clear")
def clear_all() -> StatusResponse:
    """Test endpoint: clear all state for testing."""
    session_queue.clear()
    prompt_queues.clear()
    complete_sessions.clear()
    command_queue.clear()
    results.clear()
    sync_log.clear()
    worker_events.clear()
    return StatusResponse(status="cleared")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9456)
