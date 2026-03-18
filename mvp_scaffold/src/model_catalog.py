"""Runtime Codex model catalog discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
from typing import Sequence


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelCatalogSnapshot:
    models: tuple[str, ...]
    fetched_at: datetime | None


class ModelCatalogService:
    """Discovers available Codex models from Codex runtime cache with fallback."""

    def __init__(
        self,
        *,
        codex_home: Path,
        fallback_models: Sequence[str] = (),
    ) -> None:
        self._cache_path = codex_home / "models_cache.json"
        self._fallback_models = tuple(dict.fromkeys(m.strip() for m in fallback_models if m and m.strip()))
        self._lock = threading.Lock()
        self._cached_snapshot: ModelCatalogSnapshot | None = None
        self._cached_mtime_ns: int | None = None

    def list_models(self) -> list[str]:
        snapshot = self.snapshot()
        return list(snapshot.models)

    def snapshot(self) -> ModelCatalogSnapshot:
        with self._lock:
            mtime_ns = self._cache_file_mtime_ns()
            if (
                self._cached_snapshot is not None
                and self._cached_mtime_ns is not None
                and mtime_ns is not None
                and self._cached_mtime_ns == mtime_ns
            ):
                return self._cached_snapshot

            snapshot = self._load_snapshot()
            self._cached_snapshot = snapshot
            self._cached_mtime_ns = mtime_ns
            return snapshot

    def _cache_file_mtime_ns(self) -> int | None:
        try:
            return self._cache_path.stat().st_mtime_ns
        except OSError:
            return None

    def _load_snapshot(self) -> ModelCatalogSnapshot:
        models: tuple[str, ...] = self._fallback_models
        fetched_at: datetime | None = None
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ModelCatalogSnapshot(models=models, fetched_at=fetched_at)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Failed to read Codex models cache: %s", exc)
            return ModelCatalogSnapshot(models=models, fetched_at=fetched_at)

        raw_models = payload.get("models")
        if isinstance(raw_models, list):
            discovered: list[str] = []
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                slug = str(item.get("slug") or "").strip()
                if slug:
                    discovered.append(slug)
            if discovered:
                models = tuple(dict.fromkeys(discovered))

        raw_fetched_at = payload.get("fetched_at")
        if isinstance(raw_fetched_at, str) and raw_fetched_at.strip():
            parsed = self._parse_utc_timestamp(raw_fetched_at.strip())
            if parsed is not None:
                fetched_at = parsed

        return ModelCatalogSnapshot(models=models, fetched_at=fetched_at)

    def _parse_utc_timestamp(self, value: str) -> datetime | None:
        normalized = value
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
