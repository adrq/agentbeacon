"""AgentBeacon -- AI agent orchestration platform."""

from agentbeacon._find_binary import _find_binary


def find_agentbeacon_bin() -> str:
    """Return the absolute path to the agentbeacon binary."""
    return _find_binary("agentbeacon")


def find_agentbeacon_worker_bin() -> str:
    """Return the absolute path to the agentbeacon-worker binary."""
    return _find_binary("agentbeacon-worker")
