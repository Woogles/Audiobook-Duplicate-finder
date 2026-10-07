"""Conservative comparison and preference ranking for audiobook editions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from functools import lru_cache


class Confidence(str, Enum):
    STRONG = "strong"
    REVIEW = "review"
    NONE = "none"


@dataclass(frozen=True)
class Edition:
    title: str
    authors: tuple[str, ...]
    duration_seconds: float
    file_hashes: tuple[str, ...]
    file_count: int = 1
    average_bitrate: int = 0
    lossless: bool = False
    chapter_count: int = 0
    metadata_fields: int = 0
    has_cover: bool = False
    paths: tuple[str, ...] = ()
    fingerprints: tuple[tuple[float, str], ...] = ()


@dataclass(frozen=True)
class MatchResult:
    confidence: Confidence
    score: float
    title_similarity: float
    author_similarity: float
    duration_similarity: float
    reasons: tuple[str, ...]


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def _similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _author_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    left_names = {normalize_text(author) for author in left if normalize_text(author)}
    right_names = {normalize_text(author) for author in right if normalize_text(author)}
    if not left_names or not right_names:
        return 0.0
    return len(left_names & right_names) / len(left_names | right_names)


@lru_cache(maxsize=100_000)
def _compare_fingerprint_pair(left_duration: float, left: str, right_duration: float, right: str) -> float:
    try:
        left_values = [int(value) for value in left.split(",") if value]
        right_values = [int(value) for value in right.split(",") if value]
    except ValueError:
        return 0.0
    if not left_values or not right_values:
        return 0.0

    counts: dict[int, int] = {}
    for left_index, left_value in enumerate(left_values):
        for right_index in range(max(0, left_index - 120), min(len(right_values), left_index + 120)):
            if (left_value ^ right_values[right_index]).bit_count() <= 2:
                offset = left_index - right_index
                counts[offset] = counts.get(offset, 0) + 1
    return max(counts.values(), default=0) / min(len(left_values), len(right_values))


def _fingerprint_coverage(
    source: tuple[tuple[float, str], ...], target: tuple[tuple[float, str], ...]
) -> float:
    total_duration = sum(max(duration, 0.0) for duration, _ in source)
    if not source or total_duration <= 0:
        return 0.0
    weighted_similarity = 0.0
    for duration, fingerprint in source:
        best = max((
            _compare_fingerprint_pair(duration, fingerprint, target_duration, target_fingerprint)
            for target_duration, target_fingerprint in target
        ), default=0.0)
        weighted_similarity += max(duration, 0.0) * best
    return weighted_similarity / total_duration


def _fingerprint_similarity(left: Edition, right: Edition) -> float:
    if not left.fingerprints or not right.fingerprints:
        return 0.0
    return (_fingerprint_coverage(left.fingerprints, right.fingerprints)
            + _fingerprint_coverage(right.fingerprints, left.fingerprints)) / 2


def compare_editions(left: Edition, right: Edition) -> MatchResult:
    """Compare two book editions without allowing fuzzy evidence to auto-move."""
    left_hashes = tuple(sorted(left.file_hashes))
    right_hashes = tuple(sorted(right.file_hashes))
    if left_hashes and left_hashes == right_hashes:
        return MatchResult(Confidence.STRONG, 1.0, 1.0, 1.0, 1.0, ("Identical file hash set",))

    title = _similarity(left.title, right.title)
    authors = _author_similarity(left.authors, right.authors)
    longer_duration = max(left.duration_seconds, right.duration_seconds)
    duration = (
        min(left.duration_seconds, right.duration_seconds) / longer_duration
        if longer_duration > 0
        else 0.0
    )
    fingerprint = _fingerprint_similarity(left, right)
    score = max(title * 0.55 + authors * 0.25 + duration * 0.20, fingerprint * 0.98)
    reasons: list[str] = []
    if title >= 0.92:
        reasons.append("Title is very similar")
    if authors >= 0.9:
        reasons.append("Author names agree")
    if duration >= 0.97:
        reasons.append("Durations are within 3%")
    if fingerprint >= 0.65:
        reasons.append(f"Audio fingerprints agree ({fingerprint:.0%})")

    strong = (
        (title >= 0.96 and authors >= 0.9 and duration >= 0.97)
        or (fingerprint >= 0.92 and duration >= 0.8)
    )
    candidate = (
        (title >= 0.82 and (authors >= 0.5 or duration >= 0.9))
        or fingerprint >= 0.65
    )
    confidence = Confidence.STRONG if strong else Confidence.REVIEW if candidate else Confidence.NONE
    return MatchResult(confidence, score, title, authors, duration, tuple(reasons))


def preference_key(edition: Edition) -> tuple[float, ...]:
    """Higher values are preferred when selecting the edition to keep."""
    return (
        float(edition.lossless),
        float(edition.average_bitrate),
        float(edition.duration_seconds),
        float(edition.chapter_count > 0),
        float(edition.file_count > 1),
        float(edition.metadata_fields),
        float(edition.has_cover),
    )