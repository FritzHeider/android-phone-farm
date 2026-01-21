from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Set

from .models import DeviceState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StateTransition:
    at: datetime
    from_state: DeviceState
    to_state: DeviceState
    reason: str


class DeviceStateMachine:
    """Strict state machine for one device.

    The main purpose is to make device status deterministic and auditable.
    """

    _allowed: Dict[DeviceState, Set[DeviceState]] = {
        DeviceState.DISCONNECTED: {DeviceState.BOOTING, DeviceState.READY, DeviceState.NEEDS_ATTENTION},
        DeviceState.BOOTING: {DeviceState.READY, DeviceState.UNRESPONSIVE, DeviceState.NEEDS_ATTENTION, DeviceState.DISCONNECTED},
        DeviceState.READY: {DeviceState.BUSY, DeviceState.COOLING, DeviceState.UNRESPONSIVE, DeviceState.DISCONNECTED, DeviceState.NEEDS_ATTENTION},
        DeviceState.BUSY: {DeviceState.READY, DeviceState.COOLING, DeviceState.UNRESPONSIVE, DeviceState.DISCONNECTED, DeviceState.NEEDS_ATTENTION},
        DeviceState.COOLING: {DeviceState.READY, DeviceState.UNRESPONSIVE, DeviceState.DISCONNECTED, DeviceState.NEEDS_ATTENTION},
        DeviceState.UNRESPONSIVE: {DeviceState.READY, DeviceState.DISCONNECTED, DeviceState.NEEDS_ATTENTION},
        DeviceState.NEEDS_ATTENTION: {DeviceState.READY, DeviceState.DISCONNECTED, DeviceState.BOOTING},
    }

    def __init__(self, initial: DeviceState = DeviceState.DISCONNECTED) -> None:
        self._state: DeviceState = initial
        self._history: List[StateTransition] = []

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def history(self) -> List[StateTransition]:
        return list(self._history)

    def transition(self, to_state: DeviceState, *, reason: str) -> None:
        if to_state == self._state:
            return
        allowed = self._allowed.get(self._state, set())
        if to_state not in allowed:
            raise ValueError(f"Invalid transition {self._state} -> {to_state}. Reason={reason}")

        now = datetime.now(timezone.utc)
        self._history.append(StateTransition(at=now, from_state=self._state, to_state=to_state, reason=reason))
        self._state = to_state

    def safe_transition(self, to_state: DeviceState, *, reason: str) -> None:
        """Never crash the whole system due to a state anomaly."""
        try:
            self.transition(to_state, reason=reason)
        except ValueError as e:
            logger.warning("State machine anomaly: %s", e)
            now = datetime.now(timezone.utc)
            self._history.append(
                StateTransition(at=now, from_state=self._state, to_state=to_state, reason=f"FORCED: {reason}")
            )
            self._state = to_state
