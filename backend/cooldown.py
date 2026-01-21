from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .device_registry import DeviceRegistry
from .models import DeviceState
from .scheduler import Scheduler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CooldownEntry:
    serial: str
    until: datetime


class CooldownManager:
    """Tracks temporary cooldown periods for devices.

    Cooldown is an operator concept: stop tasks, mark device as COOLING, then
    automatically return to READY when the timer ends.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._until: Dict[str, datetime] = {}

    async def start_cooldown(
        self,
        *,
        serials: List[str],
        seconds: int,
        registry: DeviceRegistry,
        scheduler: Scheduler,
    ) -> Dict[str, datetime]:
        seconds = int(max(1, min(seconds, 24 * 60 * 60)))
        now = datetime.now(timezone.utc)
        until = now + timedelta(seconds=seconds)

        async with self._lock:
            for s in serials:
                self._until[s] = until

        # Cancel any work and mark cooling.
        for s in serials:
            await scheduler.cancel_current(s)
            await registry.set_state(s, DeviceState.COOLING, reason=f"Cooldown for {seconds}s")

        logger.info("Cooldown started for %d devices (%ds)", len(serials), seconds)
        return await self.status()

    async def stop_cooldown(self, *, serials: List[str], registry: DeviceRegistry) -> Dict[str, datetime]:
        async with self._lock:
            for s in serials:
                self._until.pop(s, None)

        for s in serials:
            await registry.set_state(s, DeviceState.READY, reason="Cooldown cancelled")

        logger.info("Cooldown cancelled for %d devices", len(serials))
        return await self.status()

    async def status(self) -> Dict[str, datetime]:
        async with self._lock:
            return dict(self._until)

    async def tick(self, *, registry: DeviceRegistry) -> None:
        now = datetime.now(timezone.utc)
        to_release: List[str] = []

        async with self._lock:
            for serial, until in list(self._until.items()):
                if now >= until:
                    to_release.append(serial)
                    self._until.pop(serial, None)

        for s in to_release:
            await registry.set_state(s, DeviceState.READY, reason="Cooldown finished")

        if to_release:
            logger.info("Cooldown finished for %d devices", len(to_release))
