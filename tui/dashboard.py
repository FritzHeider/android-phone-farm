from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Static, TextLog


@dataclass
class DeviceRow:
    name: str
    serial: str
    state: str
    task: str
    error: str


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_devices(self) -> List[Dict[str, Any]]:
        r = await self._client.get("/api/v1/devices")
        r.raise_for_status()
        return r.json()

    async def get_selection(self) -> List[str]:
        r = await self._client.get("/api/v1/selection")
        r.raise_for_status()
        return r.json().get("device_serials", [])

    async def set_selection(self, serials: List[str]) -> None:
        await self._client.post("/api/v1/selection", json={"device_serials": serials})

    async def clear_selection(self) -> None:
        await self._client.post("/api/v1/selection/clear")

    async def run_campaign(self, campaign_id: str, serials: List[str]) -> Dict[str, Any]:
        r = await self._client.post(
            "/api/v1/campaigns/run",
            json={"campaign_id": campaign_id, "device_serials": serials},
        )
        r.raise_for_status()
        return r.json()

    async def emergency_stop(self) -> None:
        await self._client.post("/api/v1/emergency_stop")

    async def restart_adb(self) -> None:
        await self._client.post("/api/v1/usb/restart_adb")

    async def reboot(self, serial: str) -> None:
        await self._client.post(f"/api/v1/devices/{serial}/reboot")

    async def start_scrcpy(self, serial: str) -> None:
        await self._client.post(f"/api/v1/devices/{serial}/start_scrcpy")

    async def cooldown_start(self, serials: List[str], seconds: int) -> None:
        await self._client.post(
            "/api/v1/cooldown/start", json={"device_serials": serials, "seconds": seconds}
        )

    async def cooldown_stop(self, serials: List[str]) -> None:
        await self._client.post("/api/v1/cooldown/stop", json={"device_serials": serials})

    async def logs_tail(self, lines: int = 120) -> List[str]:
        r = await self._client.get("/api/v1/logs/tail", params={"lines": lines})
        r.raise_for_status()
        return r.json().get("lines", [])


class ConfirmScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen > Vertical {
        width: 70;
        padding: 1 2;
        border: solid $accent;
        background: $panel;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.message, id="msg"),
            Static("\nPress Y to confirm or N to cancel.")
        )

    async def on_key(self, event: events.Key) -> None:
        if event.key.lower() == "y":
            self.dismiss(True)
        elif event.key.lower() in {"n", "escape"}:
            self.dismiss(False)


class FarmTUI(App):
    CSS = """
    #left {
        width: 1fr;
        min-width: 60;
    }
    #right {
        width: 42;
        border-left: solid $surface;
        padding-left: 1;
    }
    #status {
        height: 3;
        padding: 0 1;
    }
    #logs {
        height: 12;
        border-top: solid $surface;
    }
    #actions {
        height: 1fr;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "toggle", "Toggle select"),
        ("a", "select_all", "Select all"),
        ("n", "select_none", "Select none"),
        ("r", "reboot", "Reboot"),
        ("s", "scrcpy", "See screens"),
        ("u", "restart_adb", "Restart ADB"),
        ("e", "emergency", "Emergency stop"),
        ("c", "cooldown", "Cooldown 60s"),
        ("C", "cooldown_stop", "Stop cooldown"),
        ("1", "preset('instagram_open')", "Instagram"),
        ("2", "preset('tiktok_open')", "TikTok"),
        ("3", "preset('x_open')", "X"),
        ("4", "preset('facebook_open')", "Facebook"),
        ("5", "preset('youtube_open')", "YouTube"),
        ("6", "preset('see_screens')", "scrcpy"),
    ]

    base_url = reactive("http://127.0.0.1:8765")
    filter_text = reactive("")

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.api = ApiClient(self.base_url)
        self._devices: List[DeviceRow] = []
        self._selected: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left"):
                yield Static(f"Backend: {self.base_url}")
                yield Input(placeholder="Filter by name/serial/state", id="filter")
                yield DataTable(id="table")
                yield Static("", id="status")
            with Vertical(id="right"):
                yield Static("Actions (hotkeys)")
                yield Static(
                    """
SPACE  Toggle select row
A      Select all     N   Select none
1..5   Open app presets
6      See screens (scrcpy)
S      See screens (scrcpy)
R      Reboot selected
C      Cooldown 60s   Shift+C Stop cooldown
U      Restart ADB (Mac)
E      Emergency stop
Q      Quit
""".strip(),
                    id="actions",
                )
        yield TextLog(id="logs", highlight=True)
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Sel", "Name", "Serial", "State", "Task")
        table.cursor_type = "row"
        self.set_interval(2.0, self._refresh_devices)
        self.set_interval(3.0, self._refresh_logs)
        await self._sync_selection_from_backend()
        await self._refresh_devices()
        await self._refresh_logs()

    async def on_unmount(self) -> None:
        await self.api.close()

    async def _sync_selection_from_backend(self) -> None:
        try:
            self._selected = await self.api.get_selection()
        except Exception:
            self._selected = []

    def _apply_filter(self, rows: List[DeviceRow]) -> List[DeviceRow]:
        ft = (self.filter_text or "").strip().lower()
        if not ft:
            return rows
        out: List[DeviceRow] = []
        for r in rows:
            hay = f"{r.name} {r.serial} {r.state} {r.task} {r.error}".lower()
            if ft in hay:
                out.append(r)
        return out

    async def _refresh_devices(self) -> None:
        try:
            raw = await self.api.get_devices()
        except Exception as e:
            self._set_status(f"Backend error: {e}")
            return

        rows: List[DeviceRow] = []
        for d in raw:
            meta = d.get("meta", {})
            rt = d.get("runtime", {})
            rows.append(
                DeviceRow(
                    name=meta.get("name") or meta.get("serial"),
                    serial=meta.get("serial"),
                    state=rt.get("state") or "?",
                    task=rt.get("current_task_label") or "",
                    error=rt.get("last_error") or "",
                )
            )

        rows.sort(key=lambda r: (r.name or "", r.serial or ""))
        self._devices = rows

        show_rows = self._apply_filter(rows)
        table = self.query_one("#table", DataTable)
        current_cursor = None
        try:
            current_cursor = table.cursor_row
        except Exception:
            current_cursor = None

        table.clear()
        for r in show_rows:
            sel = "X" if r.serial in self._selected else ""
            state_text = _state_badge(r.state)
            table.add_row(sel, r.name, r.serial, state_text, r.task, key=r.serial)

        if current_cursor is not None and table.row_count:
            table.cursor_row = max(0, min(current_cursor, table.row_count - 1))

        self._set_status(f"Devices: {len(rows)} | Visible: {len(show_rows)} | Selected: {len(self._selected)}")

    async def _refresh_logs(self) -> None:
        log = self.query_one("#logs", TextLog)
        try:
            lines = await self.api.logs_tail(120)
        except Exception:
            return

        # Clear and reprint. For local logs, this is fine.
        log.clear()
        for ln in lines[-120:]:
            log.write(ln)

    def _set_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.filter_text = event.value
            await self._refresh_devices()

    def _current_serial(self) -> Optional[str]:
        table = self.query_one("#table", DataTable)
        try:
            row_key = table.get_row_key(table.cursor_row)
        except Exception:
            return None
        return str(row_key) if row_key is not None else None

    async def action_toggle(self) -> None:
        serial = self._current_serial()
        if not serial:
            return
        if serial in self._selected:
            self._selected = [s for s in self._selected if s != serial]
        else:
            self._selected.append(serial)

        try:
            await self.api.set_selection(self._selected)
        except Exception as e:
            self._set_status(f"Failed to set selection: {e}")

        await self._refresh_devices()

    async def action_select_all(self) -> None:
        self._selected = [r.serial for r in self._devices]
        await self.api.set_selection(self._selected)
        await self._refresh_devices()

    async def action_select_none(self) -> None:
        self._selected = []
        await self.api.clear_selection()
        await self._refresh_devices()

    async def action_preset(self, campaign_id: str) -> None:
        if not self._selected:
            self._set_status("Select at least one device first.")
            return
        try:
            resp = await self.api.run_campaign(campaign_id, self._selected)
            self._set_status(resp.get("message") or "Campaign queued")
        except Exception as e:
            self._set_status(f"Campaign failed: {e}")

    async def action_scrcpy(self) -> None:
        if not self._selected:
            self._set_status("Select at least one device first.")
            return
        for s in list(self._selected):
            try:
                await self.api.start_scrcpy(s)
            except Exception:
                pass
        self._set_status(f"Opened scrcpy for {len(self._selected)} device(s).")

    async def action_reboot(self) -> None:
        if not self._selected:
            self._set_status("Select at least one device first.")
            return
        ok = await self.push_screen_wait(ConfirmScreen(f"Reboot {len(self._selected)} selected device(s)?"))
        if not ok:
            return
        for s in list(self._selected):
            try:
                await self.api.reboot(s)
            except Exception:
                pass
        self._set_status("Reboot requested.")

    async def action_restart_adb(self) -> None:
        ok = await self.push_screen_wait(ConfirmScreen("Restart ADB server on this Mac?"))
        if not ok:
            return
        try:
            await self.api.restart_adb()
            self._set_status("ADB restarted.")
        except Exception as e:
            self._set_status(f"ADB restart failed: {e}")

    async def action_emergency(self) -> None:
        ok = await self.push_screen_wait(ConfirmScreen("EMERGENCY STOP: cancel all tasks now?"))
        if not ok:
            return
        try:
            await self.api.emergency_stop()
            self._set_status("Emergency stop executed.")
        except Exception as e:
            self._set_status(f"Emergency stop failed: {e}")

    async def action_cooldown(self) -> None:
        if not self._selected:
            self._set_status("Select at least one device first.")
            return
        try:
            await self.api.cooldown_start(self._selected, 60)
            self._set_status("Cooldown started (60s).")
        except Exception as e:
            self._set_status(f"Cooldown failed: {e}")

    async def action_cooldown_stop(self) -> None:
        if not self._selected:
            self._set_status("Select at least one device first.")
            return
        try:
            await self.api.cooldown_stop(self._selected)
            self._set_status("Cooldown stopped.")
        except Exception as e:
            self._set_status(f"Cooldown stop failed: {e}")


def _state_badge(state: str) -> Text:
    s = (state or "").upper()
    if s == "READY":
        return Text(s, style="green")
    if s == "BUSY":
        return Text(s, style="yellow")
    if s == "COOLING":
        return Text(s, style="cyan")
    if s == "NEEDS_ATTENTION":
        return Text(s, style="red")
    if s == "UNRESPONSIVE":
        return Text(s, style="bright_red")
    if s == "DISCONNECTED":
        return Text(s, style="dim")
    return Text(s)


def main() -> None:
    host = os.environ.get("PHONE_FARM_HOST", "127.0.0.1")
    port = os.environ.get("PHONE_FARM_PORT", "8765")
    base_url = os.environ.get("PHONE_FARM_BASE_URL", f"http://{host}:{port}")
    app = FarmTUI(base_url=base_url)
    app.run()
