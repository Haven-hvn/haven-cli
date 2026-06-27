"""Pytest configuration for Haven CLI tests.

This module:
  1. Mocks optional native dependencies that may not be available in the
     test environment (e.g. ``libtorrent``, ``sklearn``).
  2. Adds the in-repo Python SDK (``packages/python/src``) to ``sys.path``
     so tests can import :mod:`haven_aol.v3` without a separate
     ``pip install -e packages/python`` step. The SDK is pure Python, no
     compiled extension; this matches the cross-stack layout described in
     ``tasking/sprint-3-shared-sdks/01-python-sdk-v3.md`` and the haven-cli
     dev workflow.

The path injection here is intentional. The alternative (a path
dependency in ``pyproject.toml``) would couple every dev install to the
exact monorepo layout; this in-test shim leaves production installs free
to declare ``haven-aol>=0.1`` from PyPI once that publish exists.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# 1) Optional-native mocks (unchanged pre-Sprint-4 behaviour).
sys.modules['libtorrent'] = MagicMock()
sys.modules['sklearn'] = MagicMock()
sys.modules['sklearn.metrics'] = MagicMock()

# 2) Make the in-repo Haven-AOL Python SDK importable as ``haven_aol``.
#    Layout (relative to this conftest.py):
#      haven-cli-main/tests/conftest.py
#      haven-cli-main/                  → parents[1] (haven-cli-main)
#      <repo root>/packages/python/src  → parents[2] / "packages/python/src"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SDK_SRC = _REPO_ROOT / "packages" / "python" / "src"
if _SDK_SRC.is_dir() and str(_SDK_SRC) not in sys.path:
    # Insert at index 0 so an in-tree SDK shadows any older installed
    # ``haven-aol`` wheel — tests must verify the in-tree v3 surface.
    sys.path.insert(0, str(_SDK_SRC))
