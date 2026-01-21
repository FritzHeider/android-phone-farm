from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from .models import SelectionState

logger = logging.getLogger(__name__)


class SelectionStore:
    """Persisted selection state shared by GUI/TUI/CLI.

    The selection is intentionally simple: a list of device serials.
    It is stored in the data directory so it survives backend restarts.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "selection.json"
        self._lock = asyncio.Lock()
        self._state = SelectionState(device_serials=[], updated_at=datetime.now(timezone.utc))
        self._load()

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._state = SelectionState.model_validate(raw)
        except Exception:
            logger.exception("Failed to load selection state. Resetting.")
            self._state = SelectionState(device_serials=[], updated_at=datetime.now(timezone.utc))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._state.model_dump_json(indent=2), encoding="utf-8")

    @staticmethod
    def _dedupe_keep_order(items: Iterable[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            s = str(item).strip()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    async def get(self) -> SelectionState:
        async with self._lock:
            return self._state

    async def set(self, device_serials: List[str]) -> SelectionState:
        cleaned = self._dedupe_keep_order(device_serials)
        async with self._lock:
            self._state = SelectionState(device_serials=cleaned, updated_at=datetime.now(timezone.utc))
            self._save()
            return self._state

    async def clear(self) -> SelectionState:
        async with self._lock:
            self._state = SelectionState(device_serials=[], updated_at=datetime.now(timezone.utc))
            self._save()
            return self._state
