from __future__ import annotations

import asyncio
import logging
import random
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .adb import ADBClient, sanitize_package
from .campaigns import CampaignDefinition, StepType
from .device_registry import DeviceRegistry
from .models import DeviceState
from .settings import FarmSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    label: str
    campaign_id: str
    created_at: datetime


class DeviceWorker:
    """Single-device worker.

    Every device gets its own worker queue so one slow device never blocks others.
    """

    def __init__(
        self,
        *,
        serial: str,
        settings: FarmSettings,
        adb: ADBClient,
        registry: DeviceRegistry,
    ) -> None:
        self.serial = serial
        self._settings = settings
        self._adb = adb
        self._registry = registry
        self._queue: asyncio.Queue[tuple[ScheduledTask, CampaignDefinition]] = asyncio.Queue()
        self._runner: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._cancel_current = asyncio.Event()

    def start(self) -> None:
        if self._runner and not self._runner.done():
            return
        self._stop_event.clear()
        self._runner = asyncio.create_task(self._run_loop(), name=f"worker:{self.serial}")

    async def stop(self) -> None:
        self._stop_event.set()
        self._cancel_current.set()
        if self._runner:
            await asyncio.wait([self._runner], timeout=5)

    async def enqueue(self, task: ScheduledTask, campaign: CampaignDefinition) -> None:
        self.start()
        await self._queue.put((task, campaign))

    async def cancel_current(self) -> None:
        self._cancel_current.set()

    async def _run_loop(self) -> None:
        logger.info("Worker started for %s", self.serial)
        while not self._stop_event.is_set():
            try:
                scheduled, campaign = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            self._cancel_current.clear()
            await self._registry.set_task(self.serial, task_id=scheduled.id, label=scheduled.label)
            await self._registry.set_state(self.serial, DeviceState.BUSY, reason=f"Task started: {scheduled.label}")

            try:
                await self._execute_campaign(campaign)
                await self._registry.set_state(self.serial, DeviceState.READY, reason=f"Task complete: {scheduled.label}")
                await self._registry.reset_recovery_attempts(self.serial)
                await self._registry.set_error(self.serial, "")
            except asyncio.CancelledError:
                await self._registry.set_state(self.serial, DeviceState.READY, reason="Task cancelled")
            except Exception as e:
                logger.exception("Task failed for %s", self.serial)
                await self._registry.set_error(self.serial, str(e))
                await self._registry.set_state(self.serial, DeviceState.NEEDS_ATTENTION, reason="Task failed")
            finally:
                await self._registry.clear_task(self.serial)

        logger.info("Worker stopped for %s", self.serial)

    async def _execute_campaign(self, campaign: CampaignDefinition) -> None:
        data_root = self._settings.data_dir / "devices" / self.serial
        shots_dir = data_root / "screenshots"
        rec_dir = data_root / "recordings"
        shots_dir.mkdir(parents=True, exist_ok=True)
        rec_dir.mkdir(parents=True, exist_ok=True)

        for idx, step in enumerate(campaign.steps, start=1):
            if self._stop_event.is_set() or self._cancel_current.is_set():
                raise asyncio.CancelledError

            label = step.label or f"{step.type}"
            logger.info("%s: step %d/%d: %s", self.serial, idx, len(campaign.steps), label)

            if step.type == StepType.LAUNCH_APP:
                pkg = sanitize_package(step.package)  # type: ignore[attr-defined]
                await self._adb.launch_app(self.serial, pkg)

            elif step.type == StepType.FORCE_STOP_APP:
                pkg = sanitize_package(step.package)  # type: ignore[attr-defined]
                await self._adb.force_stop(self.serial, pkg)

            elif step.type == StepType.OPEN_URL:
                await self._adb.open_url(self.serial, step.url)  # type: ignore[attr-defined]

            elif step.type == StepType.WAIT:
                mn = float(step.min_seconds)  # type: ignore[attr-defined]
                mx = float(step.max_seconds)  # type: ignore[attr-defined]
                if mx < mn:
                    mn, mx = mx, mn
                await asyncio.sleep(random.uniform(mn, mx))

            elif step.type == StepType.SCREENSHOT:
                filename = str(step.filename)  # type: ignore[attr-defined]
                path = shots_dir / _timestamped_filename(filename)
                await self._adb.take_screenshot(self.serial, path)

            elif step.type == StepType.SCREENRECORD:
                filename = str(step.filename)  # type: ignore[attr-defined]
                duration = int(step.duration_sec)  # type: ignore[attr-defined]
                path = rec_dir / _timestamped_filename(filename)
                await self._adb.screenrecord(self.serial, path, duration_sec=duration)

            elif step.type == StepType.START_SCRCPY:
                await asyncio.to_thread(_start_scrcpy, self._settings.scrcpy.scrcpy_path, self.serial)

            else:
                raise RuntimeError(f"Unsupported step type: {step.type}")


class Scheduler:
    def __init__(
        self,
        *,
        settings: FarmSettings,
        adb: ADBClient,
        registry: DeviceRegistry,
    ) -> None:
        self._settings = settings
        self._adb = adb
        self._registry = registry
        self._workers: Dict[str, DeviceWorker] = {}

    def _worker(self, serial: str) -> DeviceWorker:
        if serial not in self._workers:
            self._workers[serial] = DeviceWorker(
                serial=serial,
                settings=self._settings,
                adb=self._adb,
                registry=self._registry,
            )
        return self._workers[serial]

    async def stop_all(self) -> None:
        await asyncio.gather(*(w.stop() for w in self._workers.values()), return_exceptions=True)

    async def cancel_current(self, serial: str) -> None:
        await self._worker(serial).cancel_current()

    async def emergency_stop(self) -> None:
        for w in self._workers.values():
            await w.cancel_current()
        await self.stop_all()

    async def run_campaign(self, *, campaign: CampaignDefinition, serials: list[str]) -> list[str]:
        task_ids: list[str] = []
        for serial in serials:
            task = ScheduledTask(
                id=_new_task_id(serial, campaign.id),
                label=campaign.title,
                campaign_id=campaign.id,
                created_at=datetime.now(timezone.utc),
            )
            task_ids.append(task.id)
            await self._worker(serial).enqueue(task, campaign)
        return task_ids


def _new_task_id(serial: str, campaign_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{campaign_id}:{serial}:{ts}:{random.randint(1000, 9999)}"


def _timestamped_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in {"-", "_", "."}).strip(".")
    if not safe:
        safe = "artifact"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem, dot, ext = safe.partition(".")
    if ext:
        return f"{stem}_{ts}.{ext}"
    return f"{stem}_{ts}"


def _start_scrcpy(scrcpy_path: str, serial: str) -> None:
    """Start scrcpy detached from backend.

    This is intentionally a one-way action: the user closes the scrcpy window.
    """

    cmd = [scrcpy_path, "-s", serial, "--stay-awake"]
    logger.info("Starting scrcpy: %s", " ".join(cmd))
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.warning("scrcpy not found. Install via Homebrew or set scrcpy.scrcpy_path")
    except Exception:
        logger.exception("Failed to start scrcpy")
