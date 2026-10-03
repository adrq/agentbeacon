# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from pathlib import Path

from tests.testhelpers import scheduler_context

SENTINEL_BODY = "not the scheduler's data"


def find_sentinel(root: Path, name: str) -> list[Path]:
    return sorted(root.rglob(name))


def test_forced_rebuild_does_not_delete_foreign_data_under_the_index_dir(tmp_path):
    root = tmp_path / "operator-chosen-root"
    stale = root / "index"
    stale.mkdir(parents=True)

    (stale / "meta.json").write_text("this is not a tantivy index")
    (stale / "someone-elses-data.txt").write_text(SENTINEL_BODY)

    with scheduler_context(env={"AGENTBEACON_WIKI_INDEX_DIR": str(root)}) as ctx:
        assert ctx["process"].poll() is None

    survivors = find_sentinel(root, "someone-elses-data.txt")
    assert len(survivors) == 1, (
        f"foreign file was destroyed by the index rebuild; "
        f"root now holds: {sorted(str(p.relative_to(root)) for p in root.rglob('*'))}"
    )
    assert survivors[0].read_text() == SENTINEL_BODY

    moved_to = survivors[0].relative_to(root).parts[0]
    assert moved_to.startswith(".wiki-index-old-"), moved_to


def test_forced_rebuild_leaves_the_root_itself_alone(tmp_path):
    root = tmp_path / "operator-chosen-root"
    stale = root / "index"
    stale.mkdir(parents=True)
    (stale / "meta.json").write_text("this is not a tantivy index")
    (root / "unrelated.txt").write_text(SENTINEL_BODY)

    with scheduler_context(env={"AGENTBEACON_WIKI_INDEX_DIR": str(root)}) as ctx:
        assert ctx["process"].poll() is None

    assert (root / "unrelated.txt").read_text() == SENTINEL_BODY
    assert os.path.isdir(root / "index")
