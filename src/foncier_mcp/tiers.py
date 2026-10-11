"""API keys, daily quotas and the two service tiers.

The free tier is live: a key issued by hand (see `foncier-mcp keys issue`), 200 calls a day.
The paid tier is designed here (higher quota, extra features) but stays off behind the
PAID_TIER_ENABLED flag. No payment code exists in this release. On stdio there are no HTTP
headers, so no key is required: the local user already owns the process.

Keys are stored hashed (sha256) in a JSON file; the usage counter is a JSON file reset at the
first call of each UTC day. Both files live outside the repository. A host without a disk gets
its keys read-only from FONCIER_MCP_KEYS_JSON, the output of `foncier-mcp keys export`.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import FeatureDisabled, InvalidInput, QuotaExceeded, Unauthorized

KEY_PREFIX = "fm_"
HEADER = "x-api-key"
PAID_FEATURES = ("pdf_export", "parcel_watch")


@dataclass(frozen=True)
class Tier:
    name: str
    daily_quota: int
    features: tuple[str, ...]
    enabled: bool


def tiers_for(settings: Settings) -> dict[str, Tier]:
    return {
        "free": Tier("free", settings.free_daily_quota, (), True),
        "paid": Tier("paid", settings.paid_daily_quota, PAID_FEATURES, settings.paid_tier_enabled),
    }


@dataclass
class ApiKey:
    id: str
    hash: str
    email: str
    tier: str
    created_at: str
    active: bool = True


@dataclass(frozen=True)
class Access:
    """What one request is allowed to do once its key was checked."""

    tier: str
    key_id: str | None
    used_today: int
    daily_quota: int
    downgraded: bool = False

    @property
    def remaining(self) -> int:
        return max(self.daily_quota - self.used_today, 0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class KeyStore:
    """Hashed keys in a JSON file, or read-only from an inline JSON document (hosted mode)."""

    def __init__(self, path: Path, inline: str | None = None) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._inline: dict[str, dict[str, Any]] | None = None
        if inline and inline.strip():
            self._inline = _parse_inline(inline)

    @property
    def read_only(self) -> bool:
        return self._inline is not None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._inline is not None:
            return dict(self._inline)
        return dict(_read_json(self.path).get("keys") or {})

    def _save(self, keys: dict[str, dict[str, Any]]) -> None:
        _write_json(self.path, {"keys": keys})

    def _require_writable(self) -> None:
        if self.read_only:
            raise InvalidInput(
                "the key store is read-only (FONCIER_MCP_KEYS_JSON is set); "
                "issue or revoke on the file store, then export and redeploy"
            )

    def export(self) -> str:
        """Compact JSON of the store, hashes and emails only, for FONCIER_MCP_KEYS_JSON."""
        return json.dumps({"keys": self._load()}, separators=(",", ":"), sort_keys=True)

    def issue(self, email: str, tier: str, tiers: dict[str, Tier]) -> tuple[str, ApiKey]:
        """Create a key and return (plain key, record). The plain key is shown only once."""
        self._require_writable()
        email = (email or "").strip().lower()
        if "@" not in email or len(email) < 5:
            raise InvalidInput("email must be a valid address")
        if tier not in tiers:
            raise InvalidInput(f"tier must be one of: {', '.join(tiers)}")
        if not tiers[tier].enabled:
            raise FeatureDisabled(
                f"the {tier} tier is designed but disabled (PAID_TIER_ENABLED is false)"
            )
        plain = KEY_PREFIX + secrets.token_urlsafe(24)
        record = ApiKey(
            id=plain[: len(KEY_PREFIX) + 8],
            hash=_hash(plain),
            email=email,
            tier=tier,
            created_at=_utc_now().replace(microsecond=0).isoformat(),
        )
        with self._lock:
            keys = self._load()
            keys[record.hash] = asdict(record)
            self._save(keys)
        return plain, record

    def lookup(self, plain: str) -> ApiKey | None:
        record = self._load().get(_hash(plain))
        return ApiKey(**record) if record else None

    def list(self) -> list[ApiKey]:
        return sorted((ApiKey(**r) for r in self._load().values()), key=lambda k: k.created_at)

    def revoke(self, key_id: str) -> bool:
        self._require_writable()
        with self._lock:
            keys = self._load()
            hit = False
            for record in keys.values():
                if record.get("id") == key_id and record.get("active"):
                    record["active"] = False
                    hit = True
            if hit:
                self._save(keys)
        return hit


def _parse_inline(text: str) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InvalidInput(f"FONCIER_MCP_KEYS_JSON is not valid JSON: {exc}") from exc
    keys = data.get("keys", data) if isinstance(data, dict) else None
    if not isinstance(keys, dict) or not all(isinstance(v, dict) for v in keys.values()):
        raise InvalidInput("FONCIER_MCP_KEYS_JSON must be the output of `foncier-mcp keys export`")
    return dict(keys)


class UsageCounter:
    """Calls per key and per UTC day. A missing or corrupt file counts as zero."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _today(self) -> str:
        return _utc_now().date().isoformat()

    def _load(self) -> dict[str, Any]:
        data = _read_json(self.path)
        if data.get("date") != self._today():
            return {"date": self._today(), "counts": {}}
        data.setdefault("counts", {})
        return data

    def used(self, key_hash: str) -> int:
        return int(self._load()["counts"].get(key_hash, 0))

    def hit(self, key_hash: str) -> int:
        with self._lock:
            data = self._load()
            data["counts"][key_hash] = int(data["counts"].get(key_hash, 0)) + 1
            _write_json(self.path, data)
            return data["counts"][key_hash]


class QuotaGuard:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tiers = tiers_for(settings)
        self.keys = KeyStore(Path(settings.keys_file), inline=settings.keys_json)
        self.usage = UsageCounter(Path(settings.usage_file))

    def check(self, headers: Mapping[str, str] | None) -> Access:
        """Validate the request. No headers means stdio: local process, nothing to check."""
        if headers is None or not self.settings.require_api_key:
            return Access("local", None, 0, 0)
        plain = _header(headers, HEADER)
        if not plain:
            raise Unauthorized(f"missing {HEADER} header; ask for a free key (see README)")
        record = self.keys.lookup(plain.strip())
        if record is None or not record.active:
            raise Unauthorized("unknown or revoked API key")
        tier = self.tiers.get(record.tier) or self.tiers["free"]
        downgraded = False
        if not tier.enabled:
            tier = self.tiers["free"]
            downgraded = True
        used = self.usage.used(record.hash)
        if used >= tier.daily_quota:
            raise QuotaExceeded(
                f"daily quota of {tier.daily_quota} calls reached for this key; "
                "resets at 00:00 UTC",
                details={"tier": tier.name, "used_today": used},
            )
        used = self.usage.hit(record.hash)
        return Access(tier.name, record.id, used, tier.daily_quota, downgraded)

    def feature_allowed(self, access: Access, feature: str) -> bool:
        tier = self.tiers.get(access.tier)
        return bool(tier and tier.enabled and feature in tier.features)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, val in headers.items():
        if key.lower() == lowered:
            return val
    return None
