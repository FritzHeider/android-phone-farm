from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel, Field


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class PathsSettings(BaseModel):
    data_dir: str = "./data"
    logs_dir: str = "./logs"
    campaigns_dir: str = "./campaigns"


class AdbSettings(BaseModel):
    adb_path: str = "adb"
    default_timeout_sec: int = 20
    refresh_interval_sec: float = 2.0


class ScrcpySettings(BaseModel):
    enabled: bool = True
    scrcpy_path: str = "scrcpy"


class HubPowerCycleSettings(BaseModel):
    enabled: bool = False
    # Command is a list of tokens (no shell). {hub_id} is substituted.
    command: List[str] = Field(default_factory=list)


class UsbSettings(BaseModel):
    hub_power_cycle: HubPowerCycleSettings = Field(default_factory=HubPowerCycleSettings)


class WatchdogSettings(BaseModel):
    enabled: bool = True
    check_interval_sec: float = 8.0
    unresponsive_threshold_sec: float = 20.0
    max_recovery_attempts: int = 3


class DeviceOverride(BaseModel):
    name: str
    tags: List[str] = Field(default_factory=list)


class FarmSettings(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    paths: PathsSettings = Field(default_factory=PathsSettings)
    adb: AdbSettings = Field(default_factory=AdbSettings)
    scrcpy: ScrcpySettings = Field(default_factory=ScrcpySettings)
    usb: UsbSettings = Field(default_factory=UsbSettings)
    watchdog: WatchdogSettings = Field(default_factory=WatchdogSettings)
    devices: Dict[str, DeviceOverride] = Field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return Path(self.paths.data_dir).expanduser().resolve()

    @property
    def logs_dir(self) -> Path:
        return Path(self.paths.logs_dir).expanduser().resolve()

    @property
    def campaigns_dir(self) -> Path:
        return Path(self.paths.campaigns_dir).expanduser().resolve()


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(default_config_path: str | os.PathLike[str]) -> FarmSettings:
    """Load settings from YAML.

    Env overrides:
      PHONE_FARM_CONFIG: path to YAML config
      PHONE_FARM_HOST: server host
      PHONE_FARM_PORT: server port

    The returned settings are validated by Pydantic.
    """

    effective_path = Path(os.environ.get("PHONE_FARM_CONFIG", str(default_config_path))).expanduser()

    if not effective_path.exists():
        raise FileNotFoundError(f"Config not found: {effective_path}")

    with effective_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    env_overrides: Dict[str, Any] = {}
    if os.environ.get("PHONE_FARM_PORT"):
        env_overrides.setdefault("server", {})["port"] = int(os.environ["PHONE_FARM_PORT"])
    if os.environ.get("PHONE_FARM_HOST"):
        env_overrides.setdefault("server", {})["host"] = os.environ["PHONE_FARM_HOST"]

    merged = _deep_merge(raw, env_overrides)
    return FarmSettings.model_validate(merged)
