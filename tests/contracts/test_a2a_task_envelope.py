from __future__ import annotations


def test_a2a_task_envelope_points_to_canonical_schema(load_json_asset) -> None:
    payload = load_json_asset("a2a-task.json")

    assert payload["$schema"].startswith("http://json-schema.org/"), (
        "$schema should declare draft version"
    )
    assert payload["$ref"].endswith("/docs/a2a-task.schema.json"), (
        "$ref must point at vendored schema"
    )

    # Ensure the envelope descriptor does not introduce additional fields that could
    # diverge from the canonical task schema.
    assert set(payload.keys()) == {"$schema", "$ref"}
