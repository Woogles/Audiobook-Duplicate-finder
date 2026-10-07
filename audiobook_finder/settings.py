"""Per-user application settings and library path validation."""

from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "AudiobookDuplicateFinder" / "settings.json"


def validate_library_path(value: str | Path) -> Path:
    """Return an absolute readable directory path or raise a useful error."""
    text = str(value).strip().strip('"')
    if not text:
        raise ValueError("A library folder path is required")
    try:
        path = Path(text).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Library folder does not exist or cannot be resolved: {text}") from error
    if not path.is_dir():
        raise NotADirectoryError(f"Library path is not a folder: {path}")
    try:
        next(path.iterdir(), None)
    except OSError as error:
        raise PermissionError(f"Library folder cannot be read: {path}") from error
    return path


def save_library_path(value: str | Path) -> Path:
    path = validate_library_path(value)
    destination = settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"library_path": str(path)}, indent=2),
        encoding="utf-8",
    )
    return path


def load_library_path() -> str:
    try:
        value = json.loads(settings_path().read_text(encoding="utf-8-sig")).get("library_path", "")
        return str(validate_library_path(value)) if value else ""
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ""