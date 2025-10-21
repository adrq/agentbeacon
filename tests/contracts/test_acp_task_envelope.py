from __future__ import annotations


def test_acp_envelope_reuses_a2a_task_schema(load_json_asset, validate_payload) -> None:
    envelope_schema = load_json_asset("acp-task.json")

    assert envelope_schema["properties"]["method"] == {"const": "agent.executeTask"}
    assert (
        envelope_schema["properties"]["params"]["properties"]["task"]["$ref"]
        == "a2a-v0.3.0.schema.json#/definitions/MessageSendParams"
    )

    # Spot-check that a minimal valid payload round-trips against the schema by
    # leveraging the canonical task validator.
    sample_task = {
        "message": {
            "messageId": "example",
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
        }
    }
    validate_payload("message-send-params", sample_task)
