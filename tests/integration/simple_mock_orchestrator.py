"""Simple FastAPI mock orchestrator for AgentMaestro worker testing.

Based on FastAPI basic example pattern: https://fastapi.tiangolo.com/#create-it
"""

from fastapi import FastAPI, HTTPException
from typing import Dict, Any, Optional

app = FastAPI()

# Simple in-memory task queue and results storage
task_queue = []
results = []
simulate_downtime = False
poll_count = 0

@app.get("/api/worker/poll")
def poll_for_task() -> Dict[str, Any]:
    """Poll for available tasks."""
    global poll_count, simulate_downtime
    poll_count += 1

    if simulate_downtime:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if task_queue:
        task = task_queue.pop(0)  # FIFO
        return {"task": task}
    return {"task": None}

@app.post("/api/worker/result")
def submit_result(result: Dict[str, Any]) -> Dict[str, bool]:
    """Accept task results."""
    global simulate_downtime
    if simulate_downtime:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Store results for testing
    results.append(result)
    return {"accepted": True}

@app.post("/add_task")
def add_task_endpoint(task: Dict[str, Any]) -> Dict[str, str]:
    """Add a task to the queue for testing."""
    task_queue.append(task)
    return {"status": "added"}

@app.get("/results")
def get_results() -> list:
    """Get all submitted results for testing."""
    return results

@app.post("/clear")
def clear_all() -> Dict[str, str]:
    """Clear tasks and results for testing."""
    task_queue.clear()
    results.clear()
    return {"status": "cleared"}

@app.post("/simulate_downtime")
def set_downtime_simulation(downtime: Dict[str, bool]) -> Dict[str, str]:
    """Enable or disable downtime simulation."""
    global simulate_downtime
    simulate_downtime = downtime.get("enabled", False)
    return {"status": f"downtime simulation {'enabled' if simulate_downtime else 'disabled'}"}

@app.get("/poll_count")
def get_poll_count() -> Dict[str, int]:
    """Get the number of poll requests received."""
    return {"count": poll_count}

@app.get("/api/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    global simulate_downtime
    if simulate_downtime:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return {"status": "ok"}


def clear_tasks() -> None:
    """Clear all tasks from the queue."""
    task_queue.clear()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9456)
