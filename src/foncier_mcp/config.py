"""Runtime settings, read from environment variables. Nothing here is secret."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(name) or default))


def _env_text(name: str) -> str | None:
    raw = (os.environ.get(name) or "").strip()
    return raw or None


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name) or ""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    cache_ttl_hours: int
    offline: bool
    require_api_key: bool
    keys_file: Path
    usage_file: Path
    free_daily_quota: int
    paid_daily_quota: int
    paid_tier_enabled: bool
    allowed_hosts: tuple[str, ...] = ()
    keys_json: str | None = None
    timeout_seconds: float = 30.0
    user_agent: str = "foncier-mcp/0.1 (+https://github.com/1000vario/foncier-mcp)"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            cache_dir=_env_path("FONCIER_MCP_CACHE_DIR", "~/.cache/foncier-mcp"),
            cache_ttl_hours=_env_int("FONCIER_MCP_CACHE_TTL_HOURS", 168),
            offline=_env_bool("FONCIER_MCP_OFFLINE", False),
            require_api_key=_env_bool("FONCIER_MCP_REQUIRE_API_KEY", True),
            keys_file=_env_path("FONCIER_MCP_KEYS_FILE", "~/.config/foncier-mcp/keys.json"),
            usage_file=_env_path("FONCIER_MCP_USAGE_FILE", "~/.config/foncier-mcp/usage.json"),
            free_daily_quota=_env_int("FONCIER_MCP_FREE_DAILY_QUOTA", 200),
            paid_daily_quota=_env_int("FONCIER_MCP_PAID_DAILY_QUOTA", 5000),
            paid_tier_enabled=_env_bool("PAID_TIER_ENABLED", False),
            allowed_hosts=_env_list("FONCIER_MCP_ALLOWED_HOSTS"),
            keys_json=_env_text("FONCIER_MCP_KEYS_JSON"),
        )
