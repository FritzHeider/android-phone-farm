from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .adb import ADBClient, ADBError
from .device_registry import DeviceRegistry
from .models import DeviceState
from .settings import WatchdogSettings
from .usb_reset import USBResetManager

logger = logging.getLogger(__name__)


class Watchdog:
    """Periodically checks device health and attempts best-effort recovery.

    The watchdog is conservative by design. When in doubt, it marks a device
    as NEEDS_ATTENTION instead of thrashing the USB bus.
    """

    def __init__(
        self,
        *,
        settings: WatchdogSettings,
        adb: ADBClient,
        registry: DeviceRegistry,
        usb_reset: USBResetManager,
    ) -> None:
        self._settings = settings
        self._adb = adb
        self._registry = registry
        self._usb_reset = usb_reset
        self._runner: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if not self._settings.enabled:
            return
        if self._runner and not self._runner.done():
            return
        self._stop_event.clear()
        self._runner = asyncio.create_task(self._run_loop(), name="watchdog")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._runner:
            await asyncio.wait([self._runner], timeout=5)

    async def _run_loop(self) -> None:
        logger.info("Watchdog started")
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("Watchdog tick failed")
            await asyncio.sleep(self._settings.check_interval_sec)
        logger.info("Watchdog stopped")

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        devices = await self._registry.list()

        for snap in devices:
            serial = snap.meta.serial
            state = snap.runtime.state

            # Skip cleanly disconnected devices.
            if state == DeviceState.DISCONNECTED:
                continue

            last_healthy = snap.runtime.last_healthy
            if not last_healthy:
                continue

            age = (now - last_healthy).total_seconds()
            if age < self._settings.unresponsive_threshold_sec:
                continue

            await self._registry.set_state(
                serial,
                DeviceState.UNRESPONSIVE,
                reason=f"No health signal for {int(age)}s",
            )

            attempt = await self._registry.increment_recovery_attempt(serial)
            logger.warning(
                "%s appears unresponsive (age=%ss). Recovery attempt %d",
                serial,
                int(age),
                attempt,
            )

            if attempt > self._settings.max_recovery_attempts:
                await self._registry.set_state(
                    serial,
                    DeviceState.NEEDS_ATTENTION,
                    reason="Max recovery attempts exceeded",
                )
                continue

            # Recovery tier 1: restart ADB server.
            await self._usb_reset.restart_adb_server()

            # Recovery tier 2: reboot via adb if device is responsive enough.
            try:
                adb_state = await self._adb.get_state(serial)
                if adb_state == "device":
                    await self._adb.reboot(serial)
            except ADBError:
                # If even get_state fails, we'll wait for next tick.
                pass

            await asyncio.sleep(2.0)

            try:
                adb_state2 = await self._adb.get_state(serial)
            except ADBError:
                adb_state2 = ""

            if adb_state2 == "device":
                await self._registry.reset_recovery_attempts(serial)
                await self._registry.set_state(serial, DeviceState.READY, reason="Recovered")
