from __future__ import annotations

import asyncio
import logging
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Query

from .adb import ADBClient, ADBError
from .campaigns import CampaignLibrary
from .cooldown import CooldownManager
from .device_registry import DeviceRegistry
from .logging_setup import configure_logging
from .models import (
    ActionResult,
    CaptureResponse,
    CooldownRequest,
    CooldownState,
    DeviceSnapshot,
    DeviceState,
    FleetSnapshot,
    ForceStopAppRequest,
    LaunchAppRequest,
    LogTailResponse,
    OpenUrlRequest,
    RunCampaignRequest,
    SelectionState,
    SetSelectionRequest,
)
from .scheduler import Scheduler
from .selection import SelectionStore
from .settings import FarmSettings, load_settings
from .usb_reset import HubPowerCycleConfig, USBResetManager
from .utils import tail_lines
from .watchdog import Watchdog

logger = logging.getLogger(__name__)


class BackendContext:
    def __init__(self, settings: FarmSettings) -> None:
        self.settings = settings
        self.adb = ADBClient(
            adb_path=settings.adb.adb_path,
            default_timeout_sec=settings.adb.default_timeout_sec,
        )
        self.registry = DeviceRegistry(device_overrides=settings.devices)
        self.campaigns = CampaignLibrary(settings.campaigns_dir)
        self.selection = SelectionStore(settings.data_dir)
        self.cooldown = CooldownManager()
        self.usb_reset = USBResetManager(
            adb=self.adb,
            hub_power_cycle=HubPowerCycleConfig(
                enabled=settings.usb.hub_power_cycle.enabled,
                command=settings.usb.hub_power_cycle.command,
            ),
        )
        self.scheduler = Scheduler(
            settings=settings,
            adb=self.adb,
            registry=self.registry,
        )
        self.watchdog = Watchdog(
            settings=settings.watchdog,
            adb=self.adb,
            registry=self.registry,
            usb_reset=self.usb_reset,
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        self._stop_event.clear()
        self.campaigns.reload()
        self.watchdog.start()
        self._tasks.append(asyncio.create_task(self._adb_refresh_loop(), name="adb-refresh"))
        self._tasks.append(asyncio.create_task(self._campaign_reload_loop(), name="campaign-reload"))
        self._tasks.append(asyncio.create_task(self._cooldown_loop(), name="cooldown"))

    async def stop(self) -> None:
        self._stop_event.set()
        await self.watchdog.stop()
        await self.scheduler.stop_all()

        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def refresh_once(self) -> None:
        try:
            records = await self.adb.list_devices()
            parsed = []
            for r in records:
                parsed.append(
                    (
                        r.serial,
                        {
                            "state": r.state,
                            "model": r.model,
                            "product": r.product,
                            "device": r.device,
                        },
                    )
                )
            await self.registry.upsert_from_adb(parsed)
        except ADBError as e:
            logger.warning("ADB unavailable: %s", e)
        except Exception:
            logger.exception("Failed to refresh ADB devices")

    async def _adb_refresh_loop(self) -> None:
        logger.info("ADB refresh loop started")
        while not self._stop_event.is_set():
            await self.refresh_once()
            await asyncio.sleep(self.settings.adb.refresh_interval_sec)
        logger.info("ADB refresh loop stopped")

    async def _campaign_reload_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.campaigns.reload()
            except Exception:
                logger.exception("Failed to reload campaigns")
            await asyncio.sleep(5.0)

    async def _cooldown_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.cooldown.tick(registry=self.registry)
            except Exception:
                logger.exception("Cooldown loop failed")
            await asyncio.sleep(1.0)


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "farm.yaml"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings(_default_config_path())
    configure_logging(settings.logs_dir, verbose=False)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.campaigns_dir.mkdir(parents=True, exist_ok=True)

    ctx = BackendContext(settings)
    app.state.ctx = ctx
    ctx.start()

    yield

    await ctx.stop()


app = FastAPI(
    title="Android Phone Farm Backend",
    version="0.2.0",
    lifespan=lifespan,
)


def _ctx() -> BackendContext:
    return app.state.ctx  # type: ignore[attr-defined]


@app.get("/api/v1/health")
async def health() -> Dict[str, Any]:
    ctx = _ctx()
    return {
        "ok": True,
        "time": datetime.now(timezone.utc).isoformat(),
        "version": app.version,
        "campaigns_dir": str(ctx.settings.campaigns_dir),
    }


@app.get("/api/v1/fleet", response_model=FleetSnapshot)
async def fleet() -> FleetSnapshot:
    return await _ctx().registry.snapshot()


@app.post("/api/v1/refresh", response_model=ActionResult)
async def refresh() -> ActionResult:
    await _ctx().refresh_once()
    return ActionResult(ok=True, message="Refreshed")


@app.get("/api/v1/devices", response_model=list[DeviceSnapshot])
async def devices() -> list[DeviceSnapshot]:
    return await _ctx().registry.list()


@app.get("/api/v1/devices/{serial}", response_model=DeviceSnapshot)
async def device_detail(serial: str) -> DeviceSnapshot:
    snap = await _ctx().registry.get(serial)
    if not snap:
        raise HTTPException(status_code=404, detail="Unknown device")
    return snap


@app.get("/api/v1/selection", response_model=SelectionState)
async def get_selection() -> SelectionState:
    return await _ctx().selection.get()


@app.post("/api/v1/selection", response_model=SelectionState)
async def set_selection(req: SetSelectionRequest) -> SelectionState:
    # Filter to known devices to keep selection meaningful.
    known = {d.meta.serial for d in await _ctx().registry.list()}
    filtered = [s for s in req.device_serials if s in known]
    return await _ctx().selection.set(filtered)


@app.post("/api/v1/selection/clear", response_model=SelectionState)
async def clear_selection() -> SelectionState:
    return await _ctx().selection.clear()


@app.get("/api/v1/cooldown", response_model=CooldownState)
async def cooldown_status() -> CooldownState:
    until = await _ctx().cooldown.status()
    return CooldownState(until={s: t.isoformat() for s, t in until.items()})


@app.post("/api/v1/cooldown/start", response_model=CooldownState)
async def cooldown_start(req: CooldownRequest) -> CooldownState:
    ctx = _ctx()
    known = {d.meta.serial for d in await ctx.registry.list()}
    serials = [s for s in req.device_serials if s in known]
    await ctx.cooldown.start_cooldown(
        serials=serials,
        seconds=req.seconds,
        registry=ctx.registry,
        scheduler=ctx.scheduler,
    )
    until = await ctx.cooldown.status()
    return CooldownState(until={s: t.isoformat() for s, t in until.items()})


@app.post("/api/v1/cooldown/stop", response_model=CooldownState)
async def cooldown_stop(req: SetSelectionRequest) -> CooldownState:
    ctx = _ctx()
    known = {d.meta.serial for d in await ctx.registry.list()}
    serials = [s for s in req.device_serials if s in known]
    await ctx.cooldown.stop_cooldown(serials=serials, registry=ctx.registry)
    until = await ctx.cooldown.status()
    return CooldownState(until={s: t.isoformat() for s, t in until.items()})


@app.get("/api/v1/campaigns")
async def list_campaigns() -> Dict[str, Any]:
    lib = _ctx().campaigns
    return {"campaigns": [c.model_dump() for c in lib.list()]}


@app.post("/api/v1/campaigns/run", response_model=ActionResult)
async def run_campaign(req: RunCampaignRequest) -> ActionResult:
    ctx = _ctx()
    campaign = ctx.campaigns.get(req.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Unknown campaign: {req.campaign_id}")

    known = {d.meta.serial for d in await ctx.registry.list()}
    serials = [s for s in req.device_serials if s in known]
    if not serials:
        return ActionResult(ok=False, message="No valid devices selected")

    task_ids = await ctx.scheduler.run_campaign(campaign=campaign, serials=serials)
    return ActionResult(ok=True, message="Campaign queued", data={"task_ids": task_ids})


@app.post("/api/v1/emergency_stop", response_model=ActionResult)
async def emergency_stop() -> ActionResult:
    await _ctx().scheduler.emergency_stop()
    return ActionResult(ok=True, message="Emergency stop executed")


@app.post("/api/v1/usb/restart_adb", response_model=ActionResult)
async def usb_restart_adb() -> ActionResult:
    await _ctx().usb_reset.restart_adb_server()
    return ActionResult(ok=True, message="ADB server restarted")


@app.post("/api/v1/usb/recover", response_model=ActionResult)
async def usb_recover(hub_id: Optional[str] = None) -> ActionResult:
    await _ctx().usb_reset.best_effort_recover(hub_id=hub_id)
    return ActionResult(ok=True, message="Recovery sequence executed")


@app.post("/api/v1/devices/{serial}/reboot", response_model=ActionResult)
async def device_reboot(serial: str) -> ActionResult:
    ctx = _ctx()
    if not await ctx.registry.get(serial):
        raise HTTPException(status_code=404, detail="Unknown device")

    try:
        await ctx.adb.reboot(serial)
        await ctx.registry.set_state(serial, DeviceState.BOOTING, reason="Reboot requested")
        return ActionResult(ok=True, message="Reboot requested")
    except ADBError as e:
        return ActionResult(ok=False, message="Reboot failed", detail=str(e))


@app.post("/api/v1/devices/{serial}/cancel", response_model=ActionResult)
async def device_cancel(serial: str) -> ActionResult:
    await _ctx().scheduler.cancel_current(serial)
    return ActionResult(ok=True, message="Cancel requested")


@app.post("/api/v1/devices/{serial}/launch_app", response_model=ActionResult)
async def device_launch_app(serial: str, req: LaunchAppRequest) -> ActionResult:
    ctx = _ctx()
    try:
        await ctx.adb.launch_app(serial, req.package)
        return ActionResult(ok=True, message=f"Launched {req.package}")
    except ADBError as e:
        return ActionResult(ok=False, message="Launch failed", detail=str(e))


@app.post("/api/v1/devices/{serial}/force_stop", response_model=ActionResult)
async def device_force_stop(serial: str, req: ForceStopAppRequest) -> ActionResult:
    ctx = _ctx()
    try:
        await ctx.adb.force_stop(serial, req.package)
        return ActionResult(ok=True, message=f"Force-stopped {req.package}")
    except ADBError as e:
        return ActionResult(ok=False, message="Force-stop failed", detail=str(e))


@app.post("/api/v1/devices/{serial}/open_url", response_model=ActionResult)
async def device_open_url(serial: str, req: OpenUrlRequest) -> ActionResult:
    ctx = _ctx()
    try:
        await ctx.adb.open_url(serial, req.url)
        return ActionResult(ok=True, message="Opened URL")
    except ADBError as e:
        return ActionResult(ok=False, message="Open URL failed", detail=str(e))


@app.post("/api/v1/devices/{serial}/start_scrcpy", response_model=ActionResult)
async def device_start_scrcpy(serial: str) -> ActionResult:
    ctx = _ctx()
    if not ctx.settings.scrcpy.enabled:
        return ActionResult(ok=False, message="scrcpy disabled in config")

    try:
        await asyncio.to_thread(
            subprocess.Popen,
            [ctx.settings.scrcpy.scrcpy_path, "-s", serial, "--stay-awake"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ActionResult(ok=True, message="scrcpy started")
    except FileNotFoundError:
        return ActionResult(ok=False, message="scrcpy not found. Install via: brew install scrcpy")
    except Exception as e:
        return ActionResult(ok=False, message="scrcpy failed", detail=str(e))


@app.post("/api/v1/devices/{serial}/screenshot", response_model=CaptureResponse)
async def device_screenshot(serial: str) -> CaptureResponse:
    ctx = _ctx()
    if not await ctx.registry.get(serial):
        raise HTTPException(status_code=404, detail="Unknown device")

    out_dir = ctx.settings.data_dir / "devices" / serial / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = datetime.now(timezone.utc).strftime("shot_%Y%m%dT%H%M%SZ.png")
    path = out_dir / filename

    try:
        await ctx.adb.take_screenshot(serial, path)
        return CaptureResponse(ok=True, path=str(path), message="Screenshot captured")
    except ADBError as e:
        return CaptureResponse(ok=False, path=None, message=str(e))


@app.post("/api/v1/devices/{serial}/screenrecord", response_model=CaptureResponse)
async def device_screenrecord(serial: str, seconds: int = Query(15, ge=1, le=180)) -> CaptureResponse:
    ctx = _ctx()
    if not await ctx.registry.get(serial):
        raise HTTPException(status_code=404, detail="Unknown device")

    out_dir = ctx.settings.data_dir / "devices" / serial / "recordings"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = datetime.now(timezone.utc).strftime("rec_%Y%m%dT%H%M%SZ.mp4")
    path = out_dir / filename

    try:
        await ctx.adb.screenrecord(serial, path, duration_sec=int(seconds))
        return CaptureResponse(ok=True, path=str(path), message="Recording captured")
    except ADBError as e:
        return CaptureResponse(ok=False, path=None, message=str(e))


@app.get("/api/v1/logs/tail", response_model=LogTailResponse)
async def logs_tail(lines: int = Query(200, ge=1, le=2000)) -> LogTailResponse:
    ctx = _ctx()
    log_path = ctx.settings.logs_dir / "farm.log"
    return LogTailResponse(path=str(log_path), lines=tail_lines(log_path, max_lines=int(lines)))
