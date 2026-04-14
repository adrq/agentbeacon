"""Real SDK smoke tests — validates end-to-end execution with live APIs.

Disabled by default (requires API keys + costs money).
Run selectively:
    uv run pytest -m claude tests/integration/test_sdk_smoke.py -v
    uv run pytest -m copilot tests/integration/test_sdk_smoke.py -v
    uv run pytest -m codex tests/integration/test_sdk_smoke.py -v
    uv run pytest -m 'claude or copilot' tests/integration/test_sdk_smoke.py -v

Uses the cheapest available models for fast, low-cost validation.
"""

import pytest


# TODO: configure cheap/fast model via agent config or env var


@pytest.fixture
def scheduler():
    """Start a scheduler with workers for real SDK testing."""
    raise NotImplementedError("TODO: start scheduler with real SDK workers")


# --- Claude smoke tests ---


@pytest.mark.claude
def test_smoke_claude_single_turn(scheduler):
    """Create an execution with Claude, verify it completes."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_claude_multi_turn(scheduler):
    """Send a follow-up message to a waiting session."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_claude_tool_use(scheduler):
    """Claude invokes a tool and receives the result."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_claude_stop_resume(scheduler):
    """Stop a running session, then resume it."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_claude_cancel(scheduler):
    """Cancel a running execution mid-turn."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_delegation_round_trip(scheduler):
    """Lead agent delegates to a child, child completes."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_escalate_answer(scheduler):
    """Agent escalates a question, user answers via API, agent continues."""
    raise NotImplementedError


@pytest.mark.claude
def test_smoke_wiki_read_write(scheduler):
    """Agent writes to wiki, another agent reads it."""
    raise NotImplementedError


# --- Copilot smoke tests ---


@pytest.mark.copilot
def test_smoke_copilot_single_turn(scheduler):
    """Create an execution with Copilot, verify it completes."""
    raise NotImplementedError


@pytest.mark.copilot
def test_smoke_copilot_multi_turn(scheduler):
    """Send a follow-up message to a waiting Copilot session."""
    raise NotImplementedError


@pytest.mark.copilot
def test_smoke_copilot_tool_use(scheduler):
    """Copilot invokes a tool and receives the result."""
    raise NotImplementedError


@pytest.mark.copilot
def test_smoke_copilot_cancel(scheduler):
    """Cancel a running Copilot execution mid-turn."""
    raise NotImplementedError


# --- Codex smoke tests ---


@pytest.mark.codex
def test_smoke_codex_single_turn(scheduler):
    """Create an execution with Codex, verify it completes."""
    raise NotImplementedError


@pytest.mark.codex
def test_smoke_codex_multi_turn(scheduler):
    """Send a follow-up message to a waiting Codex session."""
    raise NotImplementedError


@pytest.mark.codex
def test_smoke_codex_tool_use(scheduler):
    """Codex invokes a tool and receives the result."""
    raise NotImplementedError


@pytest.mark.codex
def test_smoke_codex_stop_resume(scheduler):
    """Stop a running Codex session, then resume it."""
    raise NotImplementedError


@pytest.mark.codex
def test_smoke_codex_cancel(scheduler):
    """Cancel a running Codex execution mid-turn."""
    raise NotImplementedError


@pytest.mark.codex
def test_smoke_codex_usage_display(scheduler):
    """Verify token usage is reported for Codex sessions."""
    raise NotImplementedError
