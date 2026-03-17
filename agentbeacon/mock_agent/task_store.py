"""Task store with hand-rolled v1.0 dicts for managing task state in mock agent.

Uses plain dicts instead of SDK types to produce A2A v1.0 format directly.
"""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _new_task_status(state: str = "TASK_STATE_SUBMITTED") -> Dict[str, Any]:
    return {"state": state, "timestamp": _now_iso()}


def new_task_from_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new v1.0 task dict from a message dict."""
    return {
        "id": str(uuid.uuid4()),
        "contextId": message.get("contextId", str(uuid.uuid4())),
        "status": _new_task_status(),
        "history": [message],
        "artifacts": [],
    }


def new_text_artifact(text: str, name: str = "agent-output") -> Dict[str, Any]:
    """Create a v1.0 artifact dict with a text part."""
    return {
        "artifactId": str(uuid.uuid4()),
        "name": name,
        "parts": [{"text": text}],
    }


class TaskStore:
    """Thread-safe task storage using plain dicts (v1.0 format)."""

    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create_task_from_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new task from initial user message."""
        with self._lock:
            task = new_task_from_message(message)
            self._tasks[task["id"]] = task
            return task

    def append_message_to_task(
        self, task_id: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Append a message to an existing task's history.

        Returns None if task not found or in terminal state.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            if self._is_terminal_state(task["status"]["state"]):
                return None

            task["history"].append(message)
            return task

    def _is_terminal_state(self, state: str) -> bool:
        return state in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_REJECTED",
        )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def update_task_status(self, task_id: str, state: str) -> Optional[Dict[str, Any]]:
        """Update task status and return updated task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task["status"]["state"] = state
            task["status"]["timestamp"] = _now_iso()
            return task

    def add_task_artifact(
        self, task_id: str, artifact: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Add artifact to task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task["artifacts"].append(artifact)
            return task

    def cancel_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.update_task_status(task_id, "TASK_STATE_CANCELED")

    def complete_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.update_task_status(task_id, "TASK_STATE_COMPLETED")

    def fail_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.update_task_status(task_id, "TASK_STATE_FAILED")

    def set_task_working(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.update_task_status(task_id, "TASK_STATE_WORKING")
