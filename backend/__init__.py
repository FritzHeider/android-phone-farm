"""Backend package for android-phone-farm.

The backend is the single source of truth.
All interfaces (GUI, TUI, CLI) must talk to the backend API.

Design goals:
- deterministic device state
- operator-friendly errors
- graceful degradation on flaky USB/cables
"""

__all__ = ["__version__"]

__version__ = "0.2.0"
