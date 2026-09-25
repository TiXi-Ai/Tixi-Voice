"""Shared fixtures: an isolated data directory for every test session."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Point every XDG folder at a temporary directory before tixi is imported."""
    base = tmp_path_factory.mktemp("tixi-data")
    os.environ["XDG_DATA_HOME"] = str(base / "share")
    os.environ["XDG_CONFIG_HOME"] = str(base / "config")
    os.environ["TIXI_EXPORT_DIR"] = str(base / "exports")
    return base


@pytest.fixture()
def context(isolated_data_dir: Path):
    """A fully wired AppContext built against the temporary data directory."""
    from tixi.app.bootstrap import build_context

    ctx = build_context(offline=True)
    yield ctx
    ctx.shutdown()
