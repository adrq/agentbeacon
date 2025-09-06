"""Task store with A2A SDK types for managing task state in mock agent."""

import threading
from datetime import datetime
from typing import Dict, Optional

from a2a.types import Task, TaskState, Message, Artifact
from a2a.utils import new_task


class TaskStore:
    """Thread-safe task storage using A2A SDK types."""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()

    def create_task_from_message(self, message: Message) -> Task:
        """Create a new task from initial user message using a2a-sdk utilities."""
        with self._lock:
            task = new_task(message)
            # Ensure artifacts list is initialized
            if task.artifacts is None:
                task.artifacts = []
            self._tasks[task.id] = task
            return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def update_task_status(self, task_id: str, state: TaskState) -> Optional[Task]:
        """Update task status and return updated task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            task.status.state = state
            task.status.timestamp = datetime.utcnow().isoformat() + "Z"
            return task

    def add_task_artifact(self, task_id: str, artifact: Artifact) -> Optional[Task]:
        """Add artifact to task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            task.artifacts.append(artifact)
            return task

    def cancel_task(self, task_id: str) -> Optional[Task]:
        """Cancel task and return updated task."""
        return self.update_task_status(task_id, TaskState.canceled)

    def complete_task(self, task_id: str) -> Optional[Task]:
        """Mark task as completed."""
        return self.update_task_status(task_id, TaskState.completed)

    def fail_task(self, task_id: str) -> Optional[Task]:
        """Mark task as failed."""
        return self.update_task_status(task_id, TaskState.failed)

    def set_task_working(self, task_id: str) -> Optional[Task]:
        """Mark task as working."""
        return self.update_task_status(task_id, TaskState.working)
