"""Combine chapter files into audiobook editions and find candidate groups."""

from __future__ import annotations

import re
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .matching import Confidence, Edition, MatchResult, compare_editions, normalize_text
from .scanner import AudioFileInfo


DISC_DIRECTORY = re.compile(r"^(?:cd|disc|disk|part)\s*\d+$", re.IGNORECASE)
GENERIC_ALBUMS = {"", "unknown", "unknown album", "audiobook", "audio book"}


@dataclass(frozen=True)
class DuplicateGroup:
    editions: tuple[Edition, ...]
    confidence: Confidence
    comparisons: tuple[MatchResult, ...]


def _edition_directory(path: Path, root: Path) -> Path:
    directory = path.parent
    while directory != root and DISC_DIRECTORY.fullmatch(directory.name.strip()):
        directory = directory.parent
    return directory


def build_editions(files: tuple[AudioFileInfo, ...], root: Path) -> tuple[Edition, ...]:
    """Group files from one album/folder into an edition without merging copies."""
    root = root.resolve()
    buckets: dict[tuple[str, str, str], list[AudioFileInfo]] = defaultdict(list)
    for item in files:
        path = Path(item.path)
        directory = _edition_directory(path, root)
        album = normalize_text(item.album)
        author = normalize_text(item.album_artist or item.artist)
        if album and album not in GENERIC_ALBUMS:
            identity = (str(directory).casefold(), album, author)
        elif directory == root:
            identity = (str(path).casefold(), normalize_text(item.title), author)
        else:
            identity = (str(directory).casefold(), normalize_text(directory.name), author)
        buckets[identity].append(item)

    editions: list[Edition] = []
    for (_, album_key, _), members in sorted(buckets.items()):
        members.sort(key=lambda item: item.path.casefold())
        title = next((member.album for member in members if member.album), "")
        if not title:
            parent = Path(members[0].path).parent
            title = members[0].title if parent == root else (parent.name or members[0].title)
        authors = tuple(sorted({
            member.album_artist or member.artist
            for member in members
            if member.album_artist or member.artist
        }))
        bitrates = [member.bitrate for member in members if member.bitrate > 0]
        editions.append(Edition(
            title=title,
            authors=authors,
            duration_seconds=sum(member.duration_seconds for member in members),
            file_hashes=tuple(member.sha256 for member in members),
            file_count=len(members),
            average_bitrate=sum(bitrates) // len(bitrates) if bitrates else 0,
            lossless=any(member.lossless for member in members),
            chapter_count=sum(member.chapter_count for member in members),
            metadata_fields=sum(member.metadata_fields for member in members),
            has_cover=any(member.has_cover for member in members),
            paths=tuple(member.path for member in members),
            fingerprints=tuple(
                (member.fingerprint_duration, member.fingerprint)
                for member in members
                if member.fingerprint and member.fingerprint_duration > 0
            ),
        ))
    return tuple(editions)


def _candidate_pairs(editions: tuple[Edition, ...]) -> set[tuple[int, int]]:
    token_index: dict[str, list[int]] = defaultdict(list)
    hash_index: dict[str, list[int]] = defaultdict(list)
    duration_index: dict[int, list[int]] = defaultdict(list)
    for index, edition in enumerate(editions):
        for token in set(normalize_text(edition.title).split()):
            if len(token) >= 3:
                token_index[token].append(index)
        for digest in set(edition.file_hashes):
            hash_index[digest].append(index)
        if edition.fingerprints and edition.duration_seconds > 0:
            duration_bucket = int(math.log(edition.duration_seconds, 1.25))
            duration_index[duration_bucket].append(index)

    pairs: set[tuple[int, int]] = set()
    for index_lists in (*token_index.values(), *hash_index.values()):
        unique_indices = sorted(set(index_lists))
        for offset, left in enumerate(unique_indices):
            for right in unique_indices[offset + 1:]:
                pairs.add((left, right))
    for bucket, indices in duration_index.items():
        nearby = set(indices)
        for neighbor in (bucket - 1, bucket + 1):
            nearby.update(duration_index.get(neighbor, ()))
        for left in indices:
            for right in nearby:
                if left != right:
                    pairs.add(tuple(sorted((left, right))))
    return pairs


def find_duplicate_groups(editions: tuple[Edition, ...]) -> tuple[DuplicateGroup, ...]:
    """Return connected candidate groups; a group auto-moves only if all pairs are strong."""
    edges: list[tuple[int, int, MatchResult]] = []
    for left, right in sorted(_candidate_pairs(editions)):
        result = compare_editions(editions[left], editions[right])
        if result.confidence is not Confidence.NONE:
            edges.append((left, right, result))

    adjacency: dict[int, set[int]] = defaultdict(set)
    for left, right, _ in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)

    groups: list[DuplicateGroup] = []
    remaining = set(adjacency)
    while remaining:
        start = min(remaining)
        stack = [start]
        component: set[int] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        remaining -= component
        component_edges = [edge for edge in edges if edge[0] in component and edge[1] in component]
        component_editions = tuple(editions[index] for index in sorted(component))
        comparisons = tuple(edge[2] for edge in component_edges)
        all_pairs_strong = len(component_edges) == len(component) * (len(component) - 1) // 2
        all_pairs_strong = all_pairs_strong and all(
            comparison.confidence is Confidence.STRONG for comparison in comparisons
        )
        groups.append(DuplicateGroup(
            editions=component_editions,
            confidence=Confidence.STRONG if all_pairs_strong else Confidence.REVIEW,
            comparisons=comparisons,
        ))
    return tuple(groups)