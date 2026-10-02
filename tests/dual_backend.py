# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

BACKENDS = ["sqlite", "postgres"]

DUAL_BACKEND = pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
