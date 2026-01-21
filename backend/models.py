from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DeviceState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    BOOTING = "BOOTING"
    READY = "READY"
    BUSY = "BUSY"
    COOLING = "COOLING"
    UNRESPONSIVE = "UNRESPONSIVE"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class AdbDeviceRecord:
    """Parsed record from `adb devices -l`."""

    serial: str
    state: str
    model: Optional[str] = None
    device: Optional[str] = None
    product: Optional[str] = None
    transport_id: Optional[str] = None


class DeviceMeta(BaseModel):
    serial: str
    name: str
    tags: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    product: Optional[str] = None
    device: Optional[str] = None


class DeviceRuntime(BaseModel):
    state: DeviceState = DeviceState.DISCONNECTED
    last_seen: Optional[datetime] = None
    last_healthy: Optional[datetime] = None
    current_task_id: Optional[str] = None
    current_task_label: Optional[str] = None
    recovery_attempts: int = 0
    last_error: Optional[str] = None


class DeviceSnapshot(BaseModel):
    meta: DeviceMeta
    runtime: DeviceRuntime


class FleetSnapshot(BaseModel):
    generated_at: datetime
    devices: List[DeviceSnapshot]


class ApiError(BaseModel):
    error: str
    detail: Optional[str] = None


class ActionResult(BaseModel):
    ok: bool
    message: str
    detail: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class LaunchAppRequest(BaseModel):
    package: str


class OpenUrlRequest(BaseModel):
    url: str


class ForceStopAppRequest(BaseModel):
    package: str


class RunCampaignRequest(BaseModel):
    campaign_id: str
    device_serials: List[str]


class SelectionState(BaseModel):
    device_serials: List[str] = Field(default_factory=list)
    updated_at: datetime


class SetSelectionRequest(BaseModel):
    device_serials: List[str]


class CooldownRequest(BaseModel):
    device_serials: List[str]
    seconds: int = Field(default=60, ge=1, le=86400)


class CooldownState(BaseModel):
    # map serial -> ISO timestamp
    until: Dict[str, str] = Field(default_factory=dict)


class LogTailResponse(BaseModel):
    path: str
    lines: List[str]


class CaptureResponse(BaseModel):
    ok: bool
    path: Optional[str] = None
    message: str
