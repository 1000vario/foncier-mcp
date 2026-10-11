"""Dated file cache for upstream HTTP responses.

One JSON file per request, keyed by the SHA-256 of the full URL. The file keeps the fetch date,
the status, a few headers (including Last-Modified when the source sends it) and the body text.
The same format is used for recorded test fixtures and for the offline evals. An entry may be
gzip-compressed in place (see compress) to keep recorded fixtures small; reads accept both forms.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

KEPT_HEADERS = ("content-type", "last-modified", "content-length", "etag")
GZIP_MAGIC = b"\x1f\x8b"


@dataclass
class CachedResponse:
    url: str
    status: int
    fetched_at: str
    headers: dict[str, str]
    text: str

    @property
    def fetched_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.fetched_at)


class ResponseCache:
    def __init__(self, directory: Path, ttl_hours: int) -> None:
        self.directory = Path(directory)
        self.ttl = timedelta(hours=ttl_hours)

    @staticmethod
    def key_for(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def path_for(self, url: str) -> Path:
        return self.directory / f"{self.key_for(url)}.json"

    def get(self, url: str, *, ignore_ttl: bool = False) -> CachedResponse | None:
        path = self.path_for(url)
        if not path.exists():
            return None
        try:
            blob = path.read_bytes()
            if blob[:2] == GZIP_MAGIC:
                blob = gzip.decompress(blob)
            entry = CachedResponse(**json.loads(blob.decode("utf-8")))
        except (OSError, ValueError, TypeError, EOFError):
            return None
        if not ignore_ttl and datetime.now(UTC) - entry.fetched_at_dt > self.ttl:
            return None
        return entry

    def put(self, url: str, status: int, headers: dict[str, str], text: str) -> CachedResponse:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = CachedResponse(
            url=url,
            status=status,
            fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            headers={k.lower(): v for k, v in headers.items() if k.lower() in KEPT_HEADERS},
            text=text,
        )
        target = self.path_for(url)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(entry), ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return entry

    def compress(self) -> int:
        """Gzip every plain entry of the directory in place. Returns the number compressed."""
        done = 0
        for path in self.directory.glob("*.json"):
            blob = path.read_bytes()
            if blob[:2] == GZIP_MAGIC:
                continue
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(blob, 9, mtime=0))
            tmp.replace(path)
            done += 1
        return done
