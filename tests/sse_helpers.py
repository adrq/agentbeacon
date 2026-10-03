# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import httpx


def _parse_sse_events(lines: list[str]) -> list[dict]:
    events = []
    current_id = None
    current_event = None
    data_parts = []

    for line in lines:
        if line.startswith("id:"):
            current_id = line[3:].strip()
        elif line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].strip())
        elif line == "":
            if data_parts:
                combined = "\n".join(data_parts)
                try:
                    parsed = json.loads(combined)
                except json.JSONDecodeError:
                    parsed = combined
                events.append(
                    {"id": current_id, "event": current_event, "data": parsed}
                )
                current_id = None
                current_event = None
                data_parts = []

    return events


def _stream_sse(url: str, headers: dict = None, timeout: float = 10.0) -> list[str]:
    lines = []
    try:
        with httpx.stream("GET", url, headers=headers or {}, timeout=timeout) as resp:
            for line in resp.iter_lines():
                lines.append(line)
    except httpx.ReadTimeout:
        pass
    return lines


def _stream_sse_events(
    url: str, headers: dict = None, timeout: float = 10.0
) -> list[dict]:
    lines = _stream_sse(url, headers, timeout)
    return _parse_sse_events(lines)


def _persisted(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event"] is None]


def _position(events: list[dict]) -> dict:
    positions = [e for e in events if e["event"] == "position"]
    assert len(positions) == 1, f"expected exactly one position event, got {positions}"
    return positions[0]["data"]
