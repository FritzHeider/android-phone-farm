from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
from pathlib import Path
from typing import List, Optional

from .models import AdbDeviceRecord

logger = logging.getLogger(__name__)


class ADBError(RuntimeError):
    """Raised when an adb command fails or times out."""


class ADBClient:
    def __init__(self, *, adb_path: str = "adb", default_timeout_sec: int = 20) -> None:
        self.adb_path = adb_path
        self.default_timeout_sec = default_timeout_sec

    def _run(
        self, args: List[str], *, timeout_sec: Optional[int] = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        timeout = timeout_sec if timeout_sec is not None else self.default_timeout_sec
        cmd = [self.adb_path] + args
        logger.debug("Running adb: %s", shlex.join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except FileNotFoundError as e:
            raise ADBError(
                f"adb not found. Install Android Platform Tools and/or set adb.adb_path. ({e})"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ADBError(f"adb command timed out after {timeout}s: {shlex.join(cmd)}") from e

        if check and proc.returncode != 0:
            raise ADBError(
                f"adb failed (code {proc.returncode}): {shlex.join(cmd)}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc

    async def _run_async(
        self, args: List[str], *, timeout_sec: Optional[int] = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(self._run, args, timeout_sec=timeout_sec, check=check)

    async def ensure_server(self) -> None:
        await self._run_async(["start-server"], timeout_sec=15, check=False)

    async def kill_server(self) -> None:
        await self._run_async(["kill-server"], timeout_sec=15, check=False)

    async def list_devices(self) -> List[AdbDeviceRecord]:
        """Return parsed results of `adb devices -l`."""
        await self.ensure_server()
        proc = await self._run_async(["devices", "-l"], timeout_sec=20, check=False)
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        records: List[AdbDeviceRecord] = []

        for ln in lines:
            if ln.lower().startswith("list of devices"):
                continue

            parts = ln.split()
            if len(parts) < 2:
                continue

            serial = parts[0]
            state = parts[1]
            kv = {}
            for p in parts[2:]:
                if ":" in p:
                    k, v = p.split(":", 1)
                    kv[k] = v

            records.append(
                AdbDeviceRecord(
                    serial=serial,
                    state=state,
                    model=kv.get("model"),
                    device=kv.get("device"),
                    product=kv.get("product"),
                    transport_id=kv.get("transport_id"),
                )
            )

        return records

    async def get_state(self, serial: str) -> str:
        proc = await self._run_async(["-s", serial, "get-state"], timeout_sec=10, check=False)
        return (proc.stdout or "").strip()

    async def reboot(self, serial: str) -> None:
        await self._run_async(["-s", serial, "reboot"], timeout_sec=10, check=False)

    async def launch_app(self, serial: str, package: str) -> None:
        """Launch an app by package name.

        Uses `monkey` because it's broadly compatible.
        """
        await self._run_async(
            [
                "-s",
                serial,
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ],
            timeout_sec=15,
            check=False,
        )

    async def force_stop(self, serial: str, package: str) -> None:
        await self._run_async(["-s", serial, "shell", "am", "force-stop", package], timeout_sec=15, check=False)

    async def open_url(self, serial: str, url: str) -> None:
        await self._run_async(
            [
                "-s",
                serial,
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                url,
            ],
            timeout_sec=15,
            check=False,
        )

    async def take_screenshot(self, serial: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [self.adb_path, "-s", serial, "exec-out", "screencap", "-p"]
        logger.debug("Running adb screenshot: %s", shlex.join(cmd))

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                timeout=self.default_timeout_sec,
                check=False,
            )
        except FileNotFoundError as e:
            raise ADBError(
                f"adb not found. Install Android Platform Tools and/or set adb.adb_path. ({e})"
            ) from e

        if proc.returncode != 0 or not proc.stdout:
            raise ADBError(
                f"screenshot failed for {serial}: code={proc.returncode} stderr={proc.stderr.decode(errors='ignore')}"
            )

        dest_path.write_bytes(proc.stdout)

    async def screenrecord(self, serial: str, dest_path: Path, *, duration_sec: int = 15) -> None:
        """Record device screen for duration_sec, then pull the file."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        remote = "/sdcard/phone_farm_recording.mp4"
        await self._run_async(
            [
                "-s",
                serial,
                "shell",
                "screenrecord",
                "--time-limit",
                str(max(1, min(duration_sec, 180))),
                remote,
            ],
            timeout_sec=duration_sec + 10,
            check=False,
        )

        await self._run_async(["-s", serial, "pull", remote, str(dest_path)], timeout_sec=60, check=False)
        await self._run_async(["-s", serial, "shell", "rm", "-f", remote], timeout_sec=15, check=False)


def sanitize_package(package: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_\.]+", package):
        raise ValueError(f"Invalid package name: {package!r}")
    return package
