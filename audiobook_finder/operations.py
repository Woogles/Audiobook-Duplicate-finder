"""Non-destructive review moves and their reversible transaction log."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .editions import DuplicateGroup
from .matching import Confidence, Edition, preference_key


REVIEW_DIRECTORY = "_Audiobook Review"
LOG_NAME = "_move-log.jsonl"
INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def _safe_name(value: str) -> str:
    safe = INVALID_WINDOWS_NAME.sub("_", value).strip(" .")
    if not safe:
        safe = "Unknown book"
    if safe.upper() in RESERVED_WINDOWS_NAMES:
        safe = f"_{safe}"
    return safe[:120]


def _append_event(log_path: Path, event: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event, ensure_ascii=True) + "\n")
        log.flush()
        os.fsync(log.fileno())


def move_edition(edition: Edition, library_root: Path) -> tuple[tuple[str, str], ...]:
    """Move one approved edition into the review area, rolling back on failure."""
    root = library_root.expanduser().resolve(strict=True)
    sources = tuple(Path(path).resolve(strict=True) for path in edition.paths)
    if not sources:
        raise ValueError("This edition has no source files")
    for source in sources:
        if not source.is_file() or not source.is_relative_to(root):
            raise ValueError(f"Source is missing or outside the selected library: {source}")

    common_parent = Path(os.path.commonpath([str(source.parent) for source in sources]))
    source_label = _safe_name(common_parent.name or "Library root")
    identity = uuid.uuid4().hex
    edition_folder = _safe_name(edition.title)
    target_parent = root / REVIEW_DIRECTORY / edition_folder
    target_directory = target_parent / f"{source_label} - {identity[:8]}"
    target_directory.mkdir(parents=True, exist_ok=False)
    log_path = root / REVIEW_DIRECTORY / LOG_NAME
    mappings: list[tuple[Path, Path]] = []
    for source in sources:
        relative = source.relative_to(common_parent)
        destination = target_directory / relative
        if destination.exists():
            raise FileExistsError(destination)
        mappings.append((source, destination))

    timestamp = datetime.now(timezone.utc).isoformat()
    moved: list[tuple[Path, Path]] = []
    try:
        for source, destination in mappings:
            _append_event(log_path, {
                "event": "intent", "transaction": identity, "source": str(source),
                "destination": str(destination), "timestamp": timestamp,
            })
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            moved.append((source, destination))
            _append_event(log_path, {
                "event": "moved", "transaction": identity, "source": str(source),
                "destination": str(destination), "timestamp": timestamp,
            })
        _append_event(log_path, {"event": "complete", "transaction": identity, "timestamp": timestamp})
    except Exception:
        for source, destination in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not source.exists():
                os.replace(destination, source)
        _append_event(log_path, {"event": "rolled_back", "transaction": identity, "timestamp": timestamp})
        raise

    return tuple((str(source), str(destination)) for source, destination in mappings)


def move_strong_duplicates(
    groups: tuple[DuplicateGroup, ...], library_root: Path
) -> tuple[tuple[Edition, tuple[tuple[str, str], ...]], ...]:
    """Keep the preferred edition and move the others only in strong groups."""
    moved: list[tuple[Edition, tuple[tuple[str, str], ...]]] = []
    for group in groups:
        if group.confidence is not Confidence.STRONG or len(group.editions) < 2:
            continue
        preferred = max(group.editions, key=preference_key)
        for edition in group.editions:
            if edition is not preferred:
                moved.append((edition, move_edition(edition, library_root)))
    return tuple(moved)


def undo_last_move(library_root: Path) -> tuple[tuple[str, str], ...]:
    """Undo the most recent completed move transaction without overwriting files."""
    root = library_root.expanduser().resolve(strict=True)
    log_path = root / REVIEW_DIRECTORY / LOG_NAME
    if not log_path.is_file():
        raise FileNotFoundError("No move history exists in this library")

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    transactions: list[str] = []
    for event in events:
        transaction = event.get("transaction")
        if event.get("event") == "intent" and transaction not in transactions:
            transactions.append(transaction)
    blocked = {
        event["transaction"] for event in events
        if event.get("event") in {"undone", "rolled_back"}
    }
    available = [transaction for transaction in transactions if transaction not in blocked]
    if not available:
        raise LookupError("There are no completed moves to undo")
    transaction = available[-1]
    intents = [
        event for event in events
        if event.get("event") == "intent" and event.get("transaction") == transaction
    ]
    restored: list[tuple[str, str]] = []
    for event in reversed(intents):
        source = Path(event["source"])
        destination = Path(event["destination"])
        if destination.exists() and source.exists():
            raise FileExistsError(f"Cannot undo without overwriting the original: {source}")
        if destination.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, source)
            restored.append((str(destination), str(source)))
        elif not source.exists():
            raise FileNotFoundError(f"Neither moved nor original file exists: {source}")
    _append_event(log_path, {
        "event": "undone", "transaction": transaction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return tuple(restored)