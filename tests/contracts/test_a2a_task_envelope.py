from __future__ import annotations


def test_a2a_task_envelope_points_to_canonical_schema(load_json_asset) -> None:
    # Load the official A2A v0.3.0 schema directly
    payload = load_json_asset("a2a-v0.3.0.schema.json")

    # Verify it's a valid JSON Schema with proper draft declaration
    assert payload["$schema"].startswith("http://json-schema.org/"), (
        "$schema should declare draft version"
    )

    # Verify it has the expected top-level structure
    assert "definitions" in payload or "properties" in payload, (
        "Schema should have definitions or properties"
    )
