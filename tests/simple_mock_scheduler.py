# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional, List, Literal
from pydantic import BaseModel

app = FastAPI()


class ExecutorReport(BaseModel):
    session_id: str
    executor_state: Literal["running", "idle", "crashed"]
    agent_session_id: Optional[str] = None

    class Config:
        extra = "allow"


class TurnMessage(BaseModel):
    msg_seq: int

    class Config:
        extra = "allow"


class TurnResult(BaseModel):
    session_id: str
    messages: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None
    stderr: Optional[str] = None

    class Config:
        extra = "allow"


class WorkerSyncRequest(BaseModel):
    worker_id: str
    executor_report: Optional[ExecutorReport] = None
    turn_result: Optional[TurnResult] = None
    command_ack: Optional[str] = None

    class Config:
        extra = "forbid"


class NoActionResponse(BaseModel):
    type: Literal["no_action"] = "no_action"

    class Config:
        extra = "forbid"


class AssignAction(BaseModel):
    type: Literal["assign"] = "assign"
    session_id: str
    execution_id: str = ""
    payload: Optional[Any] = None
    resume: bool = False
    cwd: Optional[str] = None
    driver: Any = None
    agent_session_id: Optional[str] = None
    session_token: str

    class Config:
        extra = "allow"


class FeedTurnAction(BaseModel):
    type: Literal["feed_turn"] = "feed_turn"
    session_id: str
    payload: Any = None

    class Config:
        extra = "allow"


class StopTurnAction(BaseModel):
    type: Literal["stop_turn"] = "stop_turn"
    session_id: str

    class Config:
        extra = "allow"


class CancelAction(BaseModel):
    type: Literal["cancel"] = "cancel"
    session_id: str

    class Config:
        extra = "allow"


class CommandResponse(BaseModel):
    type: Literal["command"] = "command"
    token: str
    action: Any

    class Config:
        extra = "forbid"


WorkerSyncResponse = NoActionResponse | CommandResponse


class EnqueueSessionRequest(BaseModel):
    sessionId: str
    executionId: str
    taskPayload: Any
    parent_name: Optional[str] = None

    class Config:
        extra = "forbid"


class EnqueuePromptRequest(BaseModel):
    sessionId: str
    executionId: str
    taskPayload: Any

    class Config:
        extra = "forbid"


class MarkCompleteRequest(BaseModel):
    sessionId: str

    class Config:
        extra = "forbid"


class SendCommandRequest(BaseModel):
    command: Literal["cancel", "shutdown", "stop_turn"]

    class Config:
        extra = "forbid"


class StatusResponse(BaseModel):
    status: str

    class Config:
        extra = "forbid"


class HealthResponse(BaseModel):
    status: str

    class Config:
        extra = "forbid"


session_queue: List[Dict[str, Any]] = []
prompt_queues: Dict[str, List[Dict[str, Any]]] = {}
complete_sessions: set[str] = set()
command_queue: List[Dict[str, str]] = []
results: List[Dict[str, Any]] = []
sync_log: List[Dict[str, Any]] = []
worker_events: List[Dict[str, Any]] = []
captured_messages: List[Dict[str, Any]] = []
execution_sessions: Dict[str, List[Dict[str, str]]] = {}
cancel_tokens: set[str] = set()
raw_sync_log: List[Dict[str, Any]] = []
authorization_log: List[Dict[str, Any]] = []
forced_status: Dict[str, List[Any]] = {}
raw_sync_responses: List[Dict[str, Any]] = []
issued_cancel_tokens: List[str] = []
forced_log: List[Dict[str, Any]] = []


def _make_token() -> str:
    return str(uuid.uuid4())


def _make_session_token() -> str:
    return f"mock-session-token-{uuid.uuid4().hex}"


def _record_authorization(route: str, request: Request) -> None:
    authorization_log.append(
        {"route": route, "authorization": request.headers.get("authorization")}
    )


def _sync_kind(sync_request: "WorkerSyncRequest") -> str:
    if sync_request.turn_result is not None:
        return "sync_turn_result"
    if sync_request.command_ack is not None:
        return "sync_ack"
    if sync_request.executor_report is not None:
        return "sync_report"
    return "sync_idle"


def _forced(kind: str, command_ack: str | None = None):
    entry = forced_status.get(kind)
    if entry is None:
        return None
    status, remaining = entry
    if remaining is not None:
        if remaining <= 1:
            del forced_status[kind]
        else:
            entry[1] = remaining - 1
    forced_log.append({"kind": kind, "command_ack": command_ack, "status": status})
    return JSONResponse(status_code=status, content={"code": "forced"})


def _forced_sync(sync_request: "WorkerSyncRequest"):
    ack = sync_request.command_ack
    if ack is not None and ack in issued_cancel_tokens:
        forced = _forced("cancel_ack", ack)
        if forced is not None:
            return forced
    return _forced(_sync_kind(sync_request), ack)


def build_assign_action(session_data: Dict[str, Any]) -> AssignAction:
    sid = session_data["sessionId"]
    eid = session_data.get("executionId", "")
    task = session_data["taskPayload"]

    base_driver = task.get("driver", {"platform": "acp", "config": {}})
    platform = base_driver.get("platform", "acp")

    if platform.endswith("_sdk"):
        sandbox_config = base_driver.get("config") or {"fs_level": "unrestricted"}
        driver = {"platform": platform, "config": sandbox_config}
        return AssignAction(
            session_id=sid,
            execution_id=eid,
            payload=task,
            resume=False,
            cwd=task.get("cwd"),
            driver=driver,
            agent_session_id=None,
            agent_config=task.get("agent_config", {}),
            session_token=_make_session_token(),
        )

    agent_cfg = task.get("agent_config", base_driver.get("config", {}))
    driver = {"platform": platform, "config": agent_cfg}
    return AssignAction(
        session_id=sid,
        execution_id=eid,
        payload=task,
        resume=False,
        cwd=task.get("cwd"),
        driver=driver,
        agent_session_id=None,
        session_token=_make_session_token(),
    )


def _summarize_result(
    turn_result: TurnResult, agent_session_id: Optional[str]
) -> Dict[str, Any]:
    return {
        "sessionId": turn_result.session_id,
        "agentSessionId": agent_session_id,
        "turnMessages": turn_result.messages,
        "error": turn_result.error,
        "errorKind": turn_result.error_kind,
        "stderr": turn_result.stderr,
    }


def _summarize_sync(sync_request: WorkerSyncRequest) -> Dict[str, Any]:
    entry: Dict[str, Any] = {}

    if sync_request.executor_report:
        report = sync_request.executor_report
        state = report.executor_state

        if sync_request.turn_result:
            entry["sessionResult"] = _summarize_result(
                sync_request.turn_result, report.agent_session_id
            )
            entry["sessionState"] = {
                "sessionId": report.session_id,
                "status": "running",
                "agentSessionId": report.agent_session_id,
            }
        elif state == "running":
            entry["sessionState"] = {
                "sessionId": report.session_id,
                "status": "running",
                "agentSessionId": report.agent_session_id,
            }
        elif state in ("idle", "crashed"):
            entry["sessionState"] = {
                "sessionId": report.session_id,
                "status": "waiting_for_event",
                "agentSessionId": report.agent_session_id,
            }

    return entry


@app.post("/api/worker/token")
def rotate_worker_token(request: Request) -> Dict[str, str]:
    _record_authorization("token", request)
    return {"token": f"mock-worker-token-{uuid.uuid4().hex}"}


@app.post("/api/worker/sync", response_model=None)
def worker_sync(sync_request: WorkerSyncRequest, request: Request):
    _record_authorization("sync", request)
    forced = _forced_sync(sync_request)
    if forced is not None:
        sync_log.append({**_summarize_sync(sync_request), "forced": forced.status_code})
        return forced
    if raw_sync_responses:
        return JSONResponse(content=raw_sync_responses.pop(0))
    sync_log.append(_summarize_sync(sync_request))
    raw_sync_log.append(sync_request.model_dump())

    report = sync_request.executor_report
    session_id = report.session_id if report else None

    if sync_request.turn_result:
        tr = sync_request.turn_result
        ack = sync_request.command_ack
        is_cancel_ack = (
            ack is not None
            and ack in cancel_tokens
            and not bool(tr.messages)
            and tr.error is None
        )
        if is_cancel_ack:
            cancel_tokens.discard(ack)
        else:
            agent_session_id = report.agent_session_id if report else None
            results.append(
                _summarize_result(sync_request.turn_result, agent_session_id)
            )
    elif report and report.executor_state == "crashed":
        results.append(
            {
                "sessionId": session_id,
                "agentSessionId": report.agent_session_id,
                "turnMessages": None,
                "error": "executor crashed before producing a result",
                "errorKind": "executor_failed",
                "stderr": None,
            }
        )

    if session_id:
        if session_id in prompt_queues and prompt_queues[session_id]:
            prompt_data = prompt_queues[session_id].pop(0)
            return CommandResponse(
                token=_make_token(),
                action=FeedTurnAction(
                    session_id=session_id,
                    payload=prompt_data["taskPayload"],
                ),
            )

        if session_id in complete_sessions:
            complete_sessions.remove(session_id)
            token = _make_token()
            cancel_tokens.add(token)
            issued_cancel_tokens.append(token)
            return CommandResponse(
                token=token,
                action=CancelAction(session_id=session_id),
            )

        if command_queue:
            cmd = command_queue.pop(0)
            if cmd["type"] == "cancel":
                token = _make_token()
                cancel_tokens.add(token)
                issued_cancel_tokens.append(token)
                return CommandResponse(
                    token=token,
                    action=CancelAction(session_id=session_id),
                )
            if cmd["type"] == "stop_turn":
                return CommandResponse(
                    token=_make_token(),
                    action=StopTurnAction(session_id=session_id),
                )

        return NoActionResponse()

    if session_queue:
        session_data = session_queue.pop(0)
        return CommandResponse(
            token=_make_token(),
            action=build_assign_action(session_data),
        )

    if command_queue:
        command_queue.pop(0)

    return NoActionResponse()


@app.post("/api/worker/events", response_model=None)
def worker_event(body: Dict[str, Any], request: Request):
    _record_authorization("events", request)
    forced = _forced("events")
    if forced is not None:
        return forced
    worker_events.append(body)
    return StatusResponse(status="event received")


class ForceStatusRequest(BaseModel):
    kind: Literal[
        "events",
        "sync_turn_result",
        "sync_ack",
        "sync_report",
        "sync_idle",
        "cancel_ack",
    ]
    status: int
    count: Optional[int] = None

    class Config:
        extra = "forbid"


@app.post("/test/force_status")
def force_status(request: ForceStatusRequest) -> StatusResponse:
    forced_status[request.kind] = [request.status, request.count]
    return StatusResponse(status="forced")


@app.get("/test/raw_sync_queue")
def get_raw_sync_queue() -> List[Dict[str, Any]]:
    return raw_sync_responses


@app.post("/test/raw_sync_response")
def queue_raw_sync_response(body: Dict[str, Any]) -> StatusResponse:
    raw_sync_responses.append(body)
    return StatusResponse(status="queued")


@app.get("/test/authorization_log")
def get_authorization_log() -> List[Dict[str, Any]]:
    return authorization_log


@app.get("/api/v1/executions/{execution_id}/agents")
def get_execution_agents(execution_id: str) -> List[Dict[str, Any]]:
    return execution_sessions.get(execution_id, [])


@app.get("/api/v1/executions/{execution_id}/sessions")
def get_execution_sessions(execution_id: str) -> List[Dict[str, Any]]:
    return execution_sessions.get(execution_id, [])


@app.post("/api/v1/messages")
def receive_agent_message(request: Dict[str, Any]) -> StatusResponse:
    captured_messages.append(request)
    return StatusResponse(status="message received")


@app.post("/test/enqueue_session")
def enqueue_session(request: EnqueueSessionRequest) -> StatusResponse:
    session_queue.append(
        {
            "sessionId": request.sessionId,
            "executionId": request.executionId,
            "taskPayload": request.taskPayload,
        }
    )
    if request.executionId not in execution_sessions:
        execution_sessions[request.executionId] = []
    execution_sessions[request.executionId].append(
        {
            "session_id": request.sessionId,
            "parent_name": request.parent_name,
        }
    )
    return StatusResponse(status="session enqueued")


@app.post("/test/enqueue_prompt")
def enqueue_prompt(request: EnqueuePromptRequest) -> StatusResponse:
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
    complete_sessions.add(request.sessionId)
    return StatusResponse(status="session marked complete")


@app.post("/test/send_command")
def send_command(request: SendCommandRequest) -> StatusResponse:
    command_queue.append({"type": request.command})
    return StatusResponse(status="command queued")


@app.get("/test/issued_cancels")
def get_issued_cancels() -> List[str]:
    return issued_cancel_tokens


@app.get("/test/forced_log")
def get_forced_log() -> List[Dict[str, Any]]:
    return forced_log


@app.get("/test/results")
def get_results() -> List[Dict[str, Any]]:
    return results


@app.get("/test/sync_log")
def get_sync_log() -> List[Dict[str, Any]]:
    return sync_log


@app.get("/test/events")
def get_worker_events() -> List[Dict[str, Any]]:
    return worker_events


@app.get("/test/messages")
def get_captured_messages() -> List[Dict[str, Any]]:
    return captured_messages


@app.get("/test/raw_sync_log")
def get_raw_sync_log() -> List[Dict[str, Any]]:
    return raw_sync_log


@app.get("/api/health")
def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/test/clear")
def clear_all() -> StatusResponse:
    session_queue.clear()
    prompt_queues.clear()
    complete_sessions.clear()
    command_queue.clear()
    results.clear()
    sync_log.clear()
    worker_events.clear()
    captured_messages.clear()
    execution_sessions.clear()
    cancel_tokens.clear()
    raw_sync_log.clear()
    authorization_log.clear()
    forced_status.clear()
    raw_sync_responses.clear()
    issued_cancel_tokens.clear()
    forced_log.clear()
    return StatusResponse(status="cleared")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9456)
