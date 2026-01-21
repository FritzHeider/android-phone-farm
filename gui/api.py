from __future__ import annotations

from typing import Any, Dict, List, Tuple

import httpx


class Api:
    """Synchronous API client.

    The GUI runs requests in worker threads so the UI stays responsive.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=10.0)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def health(self) -> Dict[str, Any]:
        r = self._client.get("/api/v1/health")
        r.raise_for_status()
        return r.json()

    def get_devices(self) -> List[Dict[str, Any]]:
        r = self._client.get("/api/v1/devices")
        r.raise_for_status()
        return r.json()

    def get_selection(self) -> List[str]:
        r = self._client.get("/api/v1/selection")
        r.raise_for_status()
        return r.json().get("device_serials", [])

    def set_selection(self, serials: List[str]) -> Dict[str, Any]:
        r = self._client.post("/api/v1/selection", json={"device_serials": serials})
        r.raise_for_status()
        return r.json()

    def clear_selection(self) -> Dict[str, Any]:
        r = self._client.post("/api/v1/selection/clear")
        r.raise_for_status()
        return r.json()

    def get_snapshot(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        devices = self.get_devices()
        selection = self.get_selection()
        return devices, selection

    def run_campaign(self, campaign_id: str, serials: List[str]) -> Dict[str, Any]:
        r = self._client.post(
            "/api/v1/campaigns/run",
            json={"campaign_id": campaign_id, "device_serials": serials},
        )
        r.raise_for_status()
        return r.json()

    def emergency_stop(self) -> Dict[str, Any]:
        r = self._client.post("/api/v1/emergency_stop")
        r.raise_for_status()
        return r.json()

    def restart_adb(self) -> Dict[str, Any]:
        r = self._client.post("/api/v1/usb/restart_adb")
        r.raise_for_status()
        return r.json()

    def reboot(self, serial: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/reboot")
        r.raise_for_status()
        return r.json()

    def cancel_task(self, serial: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/cancel")
        r.raise_for_status()
        return r.json()

    def start_scrcpy(self, serial: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/start_scrcpy")
        r.raise_for_status()
        return r.json()

    def open_url(self, serial: str, url: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/open_url", json={"url": url})
        r.raise_for_status()
        return r.json()

    def launch_app(self, serial: str, package: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/launch_app", json={"package": package})
        r.raise_for_status()
        return r.json()

    def force_stop(self, serial: str, package: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/force_stop", json={"package": package})
        r.raise_for_status()
        return r.json()

    def screenshot(self, serial: str) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/screenshot")
        r.raise_for_status()
        return r.json()

    def screenrecord(self, serial: str, seconds: int = 15) -> Dict[str, Any]:
        r = self._client.post(f"/api/v1/devices/{serial}/screenrecord", params={"seconds": seconds})
        r.raise_for_status()
        return r.json()

    def cooldown_start(self, serials: List[str], seconds: int) -> Dict[str, Any]:
        r = self._client.post(
            "/api/v1/cooldown/start",
            json={"device_serials": serials, "seconds": seconds},
        )
        r.raise_for_status()
        return r.json()

    def cooldown_stop(self, serials: List[str]) -> Dict[str, Any]:
        r = self._client.post(
            "/api/v1/cooldown/stop",
            json={"device_serials": serials},
        )
        r.raise_for_status()
        return r.json()

    def logs_tail(self, lines: int = 200) -> Dict[str, Any]:
        r = self._client.get("/api/v1/logs/tail", params={"lines": lines})
        r.raise_for_status()
        return r.json()
