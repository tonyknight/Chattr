"""Shared pytest setup for Chattr's tests.

The synthetic fixture family lives in `tests/fixtures/` (standard library only). This conftest
makes that package importable as `fixtures` under pytest's importlib import mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures import FamilyExports, build_all  # noqa: E402  (needs the path above)


@pytest.fixture(scope="session")
def fixture_family(tmp_path_factory: pytest.TempPathFactory) -> FamilyExports:
    """Every synthetic export, built once per test session."""
    return build_all(tmp_path_factory.mktemp("family"))
