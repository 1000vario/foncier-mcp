"""Shared helpers for the test-suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from foncier_mcp.config import Settings

REPO = Path(__file__).resolve().parents[1]
RECORDED = REPO / "evals" / "recorded"


def make_settings(tmp_path: Path, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "cache_dir": tmp_path / "cache",
        "cache_ttl_hours": 168,
        "offline": True,
        "require_api_key": True,
        "keys_file": tmp_path / "keys.json",
        "usage_file": tmp_path / "usage.json",
        "free_daily_quota": 3,
        "paid_daily_quota": 10,
        "paid_tier_enabled": False,
    }
    base.update(over)
    return Settings(**base)


class FakeContext:
    """Stands in for mcp Context: only the headers attribute is used by the server."""

    def __init__(self, headers: dict[str, str] | None) -> None:
        self.headers = headers
