"""Shared test setup. Tests never touch the real vault, OneDrive, AI or network."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_peer_settings(tmp_path, monkeypatch):
    """peer.py's settings (token, cached share) go to a temp folder, and no share location leaks in from .env."""
    monkeypatch.setenv("PEER_HOME", str(tmp_path / "peer_home"))
    for var in ("SHARE_PATH", "SHARE_INBOX"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mock_md() -> tuple[str, str]:
    d = FIXTURES / "share"
    return (d / "mock_exam_1.md").read_text("utf-8"), (d / "mock_exam_1_solutions.md").read_text("utf-8")
