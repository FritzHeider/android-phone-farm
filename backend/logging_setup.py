from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler


def configure_logging(logs_dir: Path, *, verbose: bool = False) -> None:
    """Configure application-wide logging.

    - Rich console logs for operators
    - Rotating file logs for audit and debugging

    This function is idempotent.
    """

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "farm.log"

    level = logging.DEBUG if verbose else logging.INFO

    root = logging.getLogger()
    if getattr(root, "_phone_farm_configured", False):
        return

    root.setLevel(level)

    console_handler = RichHandler(rich_tracebacks=True, markup=True)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    setattr(root, "_phone_farm_configured", True)
