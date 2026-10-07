"""Coordinate a complete read-only scan followed by conservative file moves."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .editions import DuplicateGroup, build_editions, find_duplicate_groups
from .operations import move_strong_duplicates
from .scanner import ScanIssue, scan_directory


@dataclass(frozen=True)
class WorkflowResult:
    edition_count: int
    groups: tuple[DuplicateGroup, ...]
    review_groups: tuple[DuplicateGroup, ...]
    moved: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    issues: tuple[ScanIssue, ...]
    cancelled: bool


def scan_and_process(
    root: Path,
    on_progress: Callable[[int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    include_fingerprints: bool = False,
) -> WorkflowResult:
    """Scan first; only after completion, move strong duplicates and return review cases."""
    root = root.expanduser().resolve(strict=True)
    summary = scan_directory(root, on_progress, should_cancel, include_fingerprints)
    if summary.cancelled:
        return WorkflowResult(0, (), (), (), summary.issues, cancelled=True)

    editions = build_editions(summary.files, root)
    groups = find_duplicate_groups(editions)
    moved_editions = move_strong_duplicates(groups, root)
    moved = tuple((edition.title, mappings) for edition, mappings in moved_editions)
    review = tuple(group for group in groups if group.confidence.value == "review")
    return WorkflowResult(len(editions), groups, review, moved, summary.issues, cancelled=False)