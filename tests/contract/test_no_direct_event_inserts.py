# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import re

SCHEDULER_SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scheduler", "src")
)

EVENTS_MODULE = os.path.join(SCHEDULER_SRC, "db", "events.rs")

ALLOWED = {os.path.join(SCHEDULER_SRC, "db", "backfill.rs")}

INSERT_RE = re.compile(r"INSERT\s+INTO\s+events", re.IGNORECASE)
CFG_TEST_RE = re.compile(r"^\s*#\[cfg\(test\)\]\s*$")


def _production_source(path: str) -> str:
    lines = path_lines = open(path, encoding="utf-8").read().split("\n")
    out = []
    i = 0
    while i < len(path_lines):
        if (
            CFG_TEST_RE.match(lines[i])
            and i + 1 < len(lines)
            and lines[i + 1].lstrip().startswith("mod ")
        ):
            depth = 0
            i += 1
            while i < len(lines):
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
                if depth <= 0 and "{" in "".join(lines[max(0, i - 1) : i]):
                    break
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _compiled_rust_files() -> list[str]:
    files = []
    for root, _dirs, names in os.walk(SCHEDULER_SRC):
        for name in names:
            if not name.endswith(".rs"):
                continue
            files.append(os.path.join(root, name))
    return files


def test_no_direct_event_inserts_outside_the_events_module():
    offenders = []
    for path in _compiled_rust_files():
        if path == EVENTS_MODULE or path in ALLOWED:
            continue
        if INSERT_RE.search(_production_source(path)):
            offenders.append(os.path.relpath(path, SCHEDULER_SRC))
    assert offenders == [], f"raw event inserts outside db::events: {offenders}"


def test_the_pool_taking_helpers_are_gone():
    source = open(EVENTS_MODULE, encoding="utf-8").read()
    assert "pub async fn insert(" not in source
    assert "pub async fn insert_with_dedup(" not in source
    assert "pub async fn insert_in_tx(" in source
    assert "pub async fn insert_with_dedup_in_tx(" in source
    assert "pub async fn insert_locked(" in source


def test_no_compiled_caller_uses_a_removed_helper():
    for path in _compiled_rust_files():
        source = _production_source(path)
        assert "events::insert(" not in source, path
        assert "events::insert_with_dedup(" not in source, path
