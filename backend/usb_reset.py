from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .adb import ADBClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HubPowerCycleConfig:
    enabled: bool
    command: List[str]


class USBResetManager:
    """Best-effort recovery actions for USB/ADB issues.

    Without a microcontroller or a programmable hub, macOS cannot reliably
    power-cycle individual USB ports.

    We implement a tiered approach:
      1) restart ADB server
      2) optional: call a user-provided hub power-cycle command

    The hub power-cycle command is intentionally "no-shell": it's a list of tokens
    executed with subprocess (prevents shell injection).
    """

    def __init__(self, *, adb: ADBClient, hub_power_cycle: HubPowerCycleConfig) -> None:
        self._adb = adb
        self._hub_power_cycle = hub_power_cycle

    async def restart_adb_server(self) -> None:
        logger.info("USB recovery: restarting ADB server")
        await self._adb.kill_server()
        await asyncio.sleep(0.5)
        await self._adb.ensure_server()

    async def cycle_hub_power(self, hub_id: str) -> bool:
        cfg = self._hub_power_cycle
        if not cfg.enabled:
            logger.warning("Hub power cycle requested but disabled in config")
            return False
        if not cfg.command:
            logger.warning("Hub power cycle requested but no command configured")
            return False

        cmd = [t.replace("{hub_id}", hub_id) for t in cfg.command]
        logger.info("USB recovery: cycling hub power via: %s", shlex.join(cmd))

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            logger.exception("Hub power cycle command not found: %s", cmd[0])
            return False
        except subprocess.TimeoutExpired:
            logger.exception("Hub power cycle command timed out")
            return False

        if proc.returncode != 0:
            logger.warning(
                "Hub power cycle command failed (code %s). stdout=%r stderr=%r",
                proc.returncode,
                proc.stdout,
                proc.stderr,
            )
            return False

        await asyncio.sleep(2.0)
        return True

    async def best_effort_recover(self, *, hub_id: Optional[str] = None) -> None:
        """Attempt to restore device connectivity.

        This is a coarse-grained recovery method for situations like:
        - many devices suddenly show as offline
        - ADB server wedged

        If hub_id is provided and hub cycling is configured, we attempt it.
        """
        await self.restart_adb_server()
        if hub_id:
            await self.cycle_hub_power(hub_id)
            await self.restart_adb_server()
