# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
from typing import Optional


def write_report(driver: str, mcp_authorization: Optional[str]) -> None:
    path = os.environ.get("AGENTBEACON_MOCK_REPORT_FILE")
    if not path:
        return
    report = {
        "driver": driver,
        "pid": os.getpid(),
        "session_id": os.environ.get("AGENTBEACON_SESSION_ID"),
        "session_token": os.environ.get("AGENTBEACON_SESSION_TOKEN"),
        "worker_token": os.environ.get("AGENTBEACON_WORKER_TOKEN"),
        "username": os.environ.get("AGENTBEACON_USERNAME"),
        "password": os.environ.get("AGENTBEACON_PASSWORD"),
        "mcp_authorization": mcp_authorization,
    }
    with open(path, "w") as f:
        json.dump(report, f)
