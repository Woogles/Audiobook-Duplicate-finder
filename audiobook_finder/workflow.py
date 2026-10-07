"""Coordinate a complete read-only scan followed by conservative file moves."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .editions import DuplicateGroup, build_editions, find_duplicate_groups
from .operations import move_strong_duplicates
from .scanner import ScanIssue, scan_directory


LOGGER = logging.getLogger(__name__)


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
    LOGGER.info("Starting library scan at %s", root)
    summary = scan_directory(root, on_progress, should_cancel, include_fingerprints)
    for issue in summary.issues:
        LOGGER.warning("Scan issue at %s: %s", issue.path, issue.message)
    if summary.cancelled:
        LOGGER.info("Library scan cancelled at %s after discovering %d files", root, len(summary.files))
        return WorkflowResult(0, (), (), (), summary.issues, cancelled=True)

    editions = build_editions(summary.files, root)
    groups = find_duplicate_groups(editions)
    moved_editions = move_strong_duplicates(groups, root)
    moved = tuple((edition.title, mappings) for edition, mappings in moved_editions)
    review = tuple(group for group in groups if group.confidence.value == "review")
    LOGGER.info(
        "Library scan finished at %s: files=%d editions=%d duplicate_groups=%d "
        "moved_editions=%d review_groups=%d issues=%d",
        root, len(summary.files), len(editions), len(groups), len(moved), len(review),
        len(summary.issues),
    )
    return WorkflowResult(len(editions), groups, review, moved, summary.issues, cancelled=False)