from __future__ import annotations


def test_acp_envelope_reuses_a2a_task_schema(load_json_asset, validate_payload) -> None:
    envelope_schema = load_json_asset("acp-task.json")

    assert envelope_schema["method"] == {"const": "agent.executeTask"}
    assert envelope_schema["properties"]["params"]["properties"]["task"][
        "$ref"
    ].endswith("/docs/a2a-task.schema.json")

    # Spot-check that a minimal valid payload round-trips against the schema by
    # leveraging the canonical task validator.
    sample_task = {
        "history": [
            {
                "messageId": "example",
                "kind": "message",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello"}],
            }
        ]
    }
    validate_payload("a2a-task", sample_task)
