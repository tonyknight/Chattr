"""Shared pytest setup for Chattr's tests.

The synthetic fixture family lives in `tests/fixtures/` (standard library only). This conftest
makes that package importable as `fixtures` under pytest's importlib import mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
