#!/usr/bin/env python3
"""Flatten A2A v1.0 JSON Schema by rewriting external $ref targets to internal #/definitions/ paths.

The upstream schema uses external-looking filenames as $ref targets
(e.g., "lf.a2a.v1.Part.jsonschema.json") even though all 47 definitions are
inline in the same file. Python's jsonschema library cannot resolve these.

This script rewrites every $ref to #/definitions/<Name> using a deterministic
mapping: strip the .jsonschema.json suffix, take the last dot-segment, then
match against definition keys with spaces removed.

Usage:
    python utils/flatten-a2a-schema.py docs/a2a-v1.0.schema.json docs/a2a-v1.0-flat.schema.json
"""

import json
import re
import sys
from pathlib import Path


def build_ref_map(definitions: dict) -> dict[str, str]:
    """Build mapping from external ref filenames to #/definitions/ paths."""
    key_by_nospace = {}
    for key in definitions:
        normalized = key.replace(" ", "")
        key_by_nospace[normalized] = key

    return key_by_nospace


def rewrite_refs(obj, ref_map: dict[str, str]):
    """Recursively rewrite $ref values from external filenames to internal paths."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str) and not v.startswith("#"):
                stem = v.replace(".jsonschema.json", "")
                short = stem.split(".")[-1]
                def_key = ref_map.get(short)
                if def_key is None:
                    print(
                        f"WARNING: No definition match for $ref '{v}' (short='{short}')",
                        file=sys.stderr,
                    )
                    result[k] = v
                else:
                    result[k] = f"#/definitions/{def_key}"
            else:
                result[k] = rewrite_refs(v, ref_map)
        return result
    elif isinstance(obj, list):
        return [rewrite_refs(item, ref_map) for item in obj]
    else:
        return obj


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.json> <output.json>", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    with input_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    definitions = schema.get("definitions", {})
    ref_map = build_ref_map(definitions)

    flattened = rewrite_refs(schema, ref_map)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(flattened, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Verify no external refs remain
    content = json.dumps(flattened)
    remaining = [
        m
        for m in re.findall(r'"\$ref"\s*:\s*"([^"]+)"', content)
        if not m.startswith("#")
    ]
    if remaining:
        print(
            f"ERROR: {len(remaining)} unresolved external refs remain: {remaining[:5]}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Flattened {len(definitions)} definitions, rewrote all $ref targets to #/definitions/"
    )


if __name__ == "__main__":
    main()
