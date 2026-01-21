from __future__ import annotations

from pathlib import Path
from typing import List


def tail_lines(path: Path, *, max_lines: int = 200) -> List[str]:
    """Return the last max_lines of a UTF-8 text file.

    We keep this implementation intentionally boring. Log rotation keeps files
    small, and the simple approach is reliable.
    """

    if max_lines <= 0:
        return []

    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    if len(lines) <= max_lines:
        return lines

    return lines[-max_lines:]
