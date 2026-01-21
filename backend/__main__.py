from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "farm.yaml"


def main() -> None:
    """Start the backend service.

    Environment overrides:
      PHONE_FARM_HOST: host to bind
      PHONE_FARM_PORT: port to bind
      PHONE_FARM_CONFIG: path to YAML config

    The backend is intentionally a simple local process. The wrapper script
    (`./bin/farm start`) handles running it in the background.
    """

    host = os.environ.get("PHONE_FARM_HOST", "127.0.0.1")
    port = int(os.environ.get("PHONE_FARM_PORT", "8765"))
    os.environ.setdefault("PHONE_FARM_CONFIG", str(_default_config_path()))

    uvicorn.run(
        "backend.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
