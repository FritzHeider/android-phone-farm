from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .models import DeviceMeta, DeviceRuntime, DeviceSnapshot, DeviceState, FleetSnapshot
from .settings import DeviceOverride
from .state_machine import DeviceStateMachine

logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    meta: DeviceMeta
    runtime: DeviceRuntime
    sm: DeviceStateMachine


class DeviceRegistry:
    """In-memory registry of devices and their runtime state.

    The registry is the backend's single source of truth.
    """

    def __init__(self, device_overrides: Dict[str, DeviceOverride]) -> None:
        self._lock = asyncio.Lock()
        self._entries: Dict[str, _Entry] = {}
        self._device_overrides = device_overrides

    async def upsert_from_adb(self, records: Iterable[tuple[str, dict]]) -> None:
        now = datetime.now(timezone.utc)
        incoming_serials = set()

        async with self._lock:
            for serial, fields in records:
                incoming_serials.add(serial)

                override = self._device_overrides.get(serial)
                name = override.name if override else serial
                tags = list(override.tags) if override else []

                if serial not in self._entries:
                    meta = DeviceMeta(
                        serial=serial,
                        name=name,
                        tags=tags,
                        model=fields.get("model"),
                        product=fields.get("product"),
                        device=fields.get("device"),
                    )
                    initial_state = DeviceState.BOOTING if fields.get("state") == "device" else DeviceState.DISCONNECTED
                    runtime = DeviceRuntime(
                        state=initial_state,
                        last_seen=now,
                        last_healthy=now if fields.get("state") == "device" else None,
                    )
                    sm = DeviceStateMachine(initial=runtime.state)
                    self._entries[serial] = _Entry(meta=meta, runtime=runtime, sm=sm)
                    logger.info("Discovered device %s (%s)", name, serial)
                else:
                    entry = self._entries[serial]
                    entry.meta.name = name
                    entry.meta.tags = tags
                    entry.meta.model = fields.get("model") or entry.meta.model
                    entry.meta.product = fields.get("product") or entry.meta.product
                    entry.meta.device = fields.get("device") or entry.meta.device
                    entry.runtime.last_seen = now

                # state updates based on adb state
                entry2 = self._entries[serial]
                adb_state = fields.get("state")

                if adb_state == "device":
                    entry2.runtime.last_healthy = now
                    if entry2.runtime.state in {DeviceState.DISCONNECTED, DeviceState.BOOTING, DeviceState.UNRESPONSIVE, DeviceState.NEEDS_ATTENTION}:
                        entry2.sm.safe_transition(DeviceState.READY, reason="ADB reports device")
                        entry2.runtime.state = entry2.sm.state
                elif adb_state in {"offline", "unauthorized"}:
                    entry2.sm.safe_transition(DeviceState.UNRESPONSIVE, reason=f"ADB state {adb_state}")
                    entry2.runtime.state = entry2.sm.state

            # mark missing devices as disconnected
            for serial, entry in list(self._entries.items()):
                if serial not in incoming_serials:
                    if entry.runtime.state != DeviceState.DISCONNECTED:
                        entry.sm.safe_transition(DeviceState.DISCONNECTED, reason="Not present in ADB list")
                        entry.runtime.state = entry.sm.state
                    entry.runtime.last_seen = now

    async def set_state(self, serial: str, state: DeviceState, *, reason: str) -> None:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return
            entry.sm.safe_transition(state, reason=reason)
            entry.runtime.state = entry.sm.state

    async def set_last_healthy(self, serial: str, when: datetime) -> None:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return
            entry.runtime.last_healthy = when

    async def set_error(self, serial: str, message: str) -> None:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return
            entry.runtime.last_error = message or None

    async def set_task(self, serial: str, *, task_id: str, label: str) -> None:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return
            entry.runtime.current_task_id = task_id
            entry.runtime.current_task_label = label

    async def clear_task(self, serial: str) -> None:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return
            entry.runtime.current_task_id = None
            entry.runtime.current_task_label = None

    async def increment_recovery_attempt(self, serial: str) -> int:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return 0
            entry.runtime.recovery_attempts += 1
            return entry.runtime.recovery_attempts

    async def reset_recovery_attempts(self, serial: str) -> None:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return
            entry.runtime.recovery_attempts = 0

    async def get(self, serial: str) -> Optional[DeviceSnapshot]:
        async with self._lock:
            entry = self._entries.get(serial)
            if not entry:
                return None
            return DeviceSnapshot(meta=entry.meta, runtime=entry.runtime)

    async def list(self) -> List[DeviceSnapshot]:
        async with self._lock:
            return [DeviceSnapshot(meta=e.meta, runtime=e.runtime) for e in self._entries.values()]

    async def snapshot(self) -> FleetSnapshot:
        return FleetSnapshot(generated_at=datetime.now(timezone.utc), devices=await self.list())
