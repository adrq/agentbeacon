from __future__ import annotations

import pytest


@pytest.mark.skip(reason="Temporarily skipped per request: failing stdio contract test")
def test_stdio_contract_exposes_prompt_and_task(
    load_json_asset, validate_payload
) -> None:
    envelope = load_json_asset("stdio-task.md")

    assert envelope["type"] == "task"
    assert envelope["body"]["prompt"], "Derived prompt must be present"
    task_payload = envelope["body"]["task"]

    # Ensure identifiers make it through to the stdio body for logging/debugging.
    assert envelope["body"]["workflowRegistryId"].startswith("team/")
    assert envelope["body"]["workflowRef"].endswith(":latest")

    validate_payload("message-send-params", task_payload)
