"""On-disk cache of validated extractions, keyed by image hash + provider + model + prompt version."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def cache_key(image_sha256: str, provider: str, model: str, prompt_version: str) -> str:
    raw = "|".join((image_sha256, provider, model, prompt_version))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ExtractionCache:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable cache entry %s: %s", path, exc)
            return None

    def put(self, key: str, payload: dict) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path
