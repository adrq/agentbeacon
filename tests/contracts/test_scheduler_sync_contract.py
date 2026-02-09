from __future__ import annotations

import pytest


@pytest.mark.skip(reason="Phase 2a: replaced by session-based sync protocol")
@pytest.mark.usefixtures("validate_payload", "load_json_asset")
def test_scheduler_sync_contract(load_json_asset, validate_payload) -> None:
    payload = load_json_asset("scheduler-sync-response.md")

    expected_keys = {
        "workflowRegistryId",
        "workflowVersion",
        "workflowRef",
        "agent",
        "task",
    }
    assert expected_keys.issubset(payload.keys())

    protocol_metadata = payload.get("protocolMetadata")
    assert isinstance(protocol_metadata, dict), (
        "protocolMetadata must be an object (may be empty)"
    )

    validate_payload("message-send-params", payload["task"])

    assert payload["agent"], "agent binding must be provided"
    assert payload["workflowRegistryId"], "workflowRegistryId must be present"
    assert payload["workflowVersion"], "workflowVersion must be present"
    assert payload["workflowRef"], "workflowRef must be present"
