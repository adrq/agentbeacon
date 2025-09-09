from __future__ import annotations

from typing import Any, Callable

import pytest

from . import schema_helpers


@pytest.fixture(scope="session")
def docs_root() -> str:
    return str(schema_helpers.DOCS_ROOT)


@pytest.fixture(scope="session")
def validate_payload() -> Callable[[str, Any], None]:
    return schema_helpers.validate_payload


@pytest.fixture(scope="session")
def load_json_asset() -> Callable[[str], Any]:
    return schema_helpers.load_json_asset


@pytest.fixture(scope="session")
def schema_validator() -> Callable[[str, Any], None]:
    def _validate(name: str, payload: Any) -> None:
        schema_helpers.validate_payload(name, payload)

    return _validate


@pytest.fixture(scope="session")
def a2a_task_validator() -> Callable[[Any], None]:
    return schema_helpers.schema_validator("a2a-task")


@pytest.fixture(scope="session")
def workflow_schema_validator() -> Callable[[Any], None]:
    return schema_helpers.schema_validator("workflow-schema")
