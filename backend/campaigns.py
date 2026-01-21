from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Annotated, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    """Campaign step types.

    This project intentionally ships only "safe" steps that make sense for
    legitimate device-lab operations (open app, open URL, capture evidence,
    start scrcpy for manual control).
    """

    LAUNCH_APP = "LAUNCH_APP"
    FORCE_STOP_APP = "FORCE_STOP_APP"
    OPEN_URL = "OPEN_URL"
    WAIT = "WAIT"
    SCREENSHOT = "SCREENSHOT"
    SCREENRECORD = "SCREENRECORD"
    START_SCRCPY = "START_SCRCPY"


class BaseStep(BaseModel):
    type: StepType
    label: str = ""


class LaunchAppStep(BaseStep):
    type: StepType = StepType.LAUNCH_APP
    package: str


class ForceStopAppStep(BaseStep):
    type: StepType = StepType.FORCE_STOP_APP
    package: str


class OpenUrlStep(BaseStep):
    type: StepType = StepType.OPEN_URL
    url: str


class WaitStep(BaseStep):
    type: StepType = StepType.WAIT
    min_seconds: float = Field(ge=0, default=2.0)
    max_seconds: float = Field(ge=0, default=6.0)


class ScreenshotStep(BaseStep):
    type: StepType = StepType.SCREENSHOT
    filename: str


class ScreenrecordStep(BaseStep):
    type: StepType = StepType.SCREENRECORD
    filename: str
    duration_sec: int = Field(ge=1, le=180, default=15)


class StartScrcpyStep(BaseStep):
    type: StepType = StepType.START_SCRCPY


Step = Annotated[
    Union[
        LaunchAppStep,
        ForceStopAppStep,
        OpenUrlStep,
        WaitStep,
        ScreenshotStep,
        ScreenrecordStep,
        StartScrcpyStep,
    ],
    Field(discriminator="type"),
]


class CampaignDefinition(BaseModel):
    id: str
    title: str
    description: str
    operator_note: str = ""
    steps: List[Step]


class CampaignLibrary:
    def __init__(self, campaigns_dir: Path) -> None:
        self._dir = campaigns_dir
        self._campaigns: Dict[str, CampaignDefinition] = {}

    @property
    def campaigns_dir(self) -> Path:
        return self._dir

    def list(self) -> List[CampaignDefinition]:
        return sorted(self._campaigns.values(), key=lambda c: c.id)

    def get(self, campaign_id: str) -> Optional[CampaignDefinition]:
        return self._campaigns.get(campaign_id)

    def reload(self) -> None:
        self._campaigns.clear()
        if not self._dir.exists():
            logger.warning("Campaigns dir does not exist: %s", self._dir)
            return

        for path in sorted(self._dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not raw:
                    continue
                campaign = CampaignDefinition.model_validate(raw)
            except Exception:
                logger.exception("Failed to load campaign: %s", path)
                continue

            if campaign.id in self._campaigns:
                logger.warning("Duplicate campaign id %s in %s (overwriting)", campaign.id, path)
            self._campaigns[campaign.id] = campaign

        logger.info("Loaded %d campaigns from %s", len(self._campaigns), self._dir)
