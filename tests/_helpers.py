"""Shared bits every suite needs.

The suites write throwaway ``config.json`` files and preview PNGs while they run. Those go to a
temporary directory rather than into the checkout, so running the tests never leaves the working
tree dirty and never touches your real settings in ``%APPDATA%``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: One directory per machine, reused across runs so a suite can be re-run without re-creating it.
_SCRATCH_NAME = "nib-tests"


def project_root() -> Path:
    """The checkout, found relative to this file rather than hard-coded."""
    return Path(__file__).resolve().parent.parent


def scratch() -> Path:
    """A writable directory for the temporary files a suite makes."""
    override = os.environ.get("NIB_TEST_SCRATCH")
    path = Path(override) if override else Path(tempfile.gettempdir()) / _SCRATCH_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def assets() -> Path:
    return project_root() / "assets"
