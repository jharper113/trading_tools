"""Portable resolution of paths recorded by Windows AmiBroker jobs."""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath
from typing import Optional


DEFAULT_WINDOWS_ROOT = Path("/home/jon/Dropbox/HarpFolders")


def resolve_portable_path(value: object, parent: Optional[Path] = None) -> Path:
    """Resolve archive paths on either Windows or the Linux Dropbox mirror."""
    text = str(value or "").strip()
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match and os.name != "nt":
        drive, remainder = match.groups()
        configured = os.environ.get(f"AMIBROKER_{drive.upper()}_ROOT")
        if drive.upper() == "Z":
            configured = configured or os.environ.get("AMIBROKER_WINDOWS_ROOT")
            root = Path(configured) if configured else DEFAULT_WINDOWS_ROOT
        elif configured:
            root = Path(configured)
        else:
            raise ValueError(f"No Linux path mapping is configured for Windows drive {drive.upper()}:")
        return root.joinpath(*PureWindowsPath(remainder).parts)
    candidate = Path(text)
    if candidate.is_absolute() or parent is None:
        return candidate
    return Path(parent) / candidate
