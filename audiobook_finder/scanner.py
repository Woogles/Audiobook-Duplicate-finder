"""Read-only discovery and inspection of local audiobook files."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable


AUDIO_EXTENSIONS = frozenset(
    {
        ".aac", ".aax", ".aif", ".aiff", ".ape", ".flac", ".m4a", ".m4b",
        ".m4p", ".mka", ".mkv", ".mp3", ".mpc", ".oga", ".ogg", ".opus",
        ".wav", ".wma", ".wv",
    }
)
LOSSLESS_EXTENSIONS = frozenset({".aif", ".aiff", ".ape", ".flac", ".wav", ".wv"})


class ScanCancelled(Exception):
    pass


@dataclass(frozen=True)
class AudioFileInfo:
    path: str
    size_bytes: int
    sha256: str
    title: str
    album: str
    album_artist: str
    artist: str
    duration_seconds: float
    bitrate: int
    lossless: bool
    chapter_count: int
    has_cover: bool
    metadata_fields: int
    fingerprint_duration: float = 0.0
    fingerprint: str = ""


@dataclass(frozen=True)
class ScanIssue:
    path: str
    message: str


@dataclass(frozen=True)
class ScanSummary:
    files: tuple[AudioFileInfo, ...]
    issues: tuple[ScanIssue, ...]
    cancelled: bool = False


def _text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return _text(value[0]) if value else ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip() if value is not None else ""


def _tag(tags: object, names: tuple[str, ...]) -> str:
    if tags is None:
        return ""
    for name in names:
        try:
            value = tags.get(name)
        except AttributeError:
            value = None
        text = _text(value)
        if text:
            return text
    return ""


def _sha256(path: Path, should_cancel: Callable[[], bool] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            if should_cancel and should_cancel():
                raise ScanCancelled
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_audio(path: Path) -> tuple[float, str]:
    command = os.environ.get("FPCALC", "fpcalc")
    completed = subprocess.run(
        [command, "-raw", "-length", "0", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    values = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    return float(values["DURATION"]), values["FINGERPRINT"]


def _fpcalc_available() -> bool:
    command = os.environ.get("FPCALC", "fpcalc")
    executable = shutil.which(command)
    if executable is None and Path(command).is_file():
        executable = str(Path(command))
    if executable is None:
        return False
    try:
        subprocess.run(
            [executable, "-version"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def inspect_file(path: Path, should_cancel: Callable[[], bool] | None = None) -> AudioFileInfo:
    """Extract available tags and hash one file without modifying it."""
    from mutagen import File as MutagenFile

    before = path.stat()
    try:
        audio = MutagenFile(path)
    except Exception:
        audio = None
    tags = getattr(audio, "tags", None) if audio is not None else None
    info = getattr(audio, "info", None) if audio is not None else None

    title = _tag(tags, ("title", "TIT2", "©nam")) or path.stem
    album = _tag(tags, ("album", "TALB", "©alb"))
    album_artist = _tag(tags, ("albumartist", "album artist", "TPE2", "aART"))
    artist = _tag(tags, ("artist", "TPE1", "©ART"))
    duration = float(getattr(info, "length", 0.0) or 0.0)
    bitrate = int(getattr(info, "bitrate", 0) or 0)
    raw_tags = tags or {}
    chapter_count = sum(1 for key in raw_tags if str(key).upper().startswith("CHAP"))
    chapters = getattr(audio, "chapters", ()) if audio is not None else ()
    chapter_count = max(chapter_count, len(chapters or ()))
    has_cover = any(
        str(key).upper().startswith(("APIC", "COVR", "PICTURE"))
        for key in raw_tags
    )
    metadata_fields = sum(bool(_tag(tags, names)) for names in (
        ("title", "TIT2", "©nam"), ("album", "TALB", "©alb"),
        ("artist", "TPE1", "©ART"), ("albumartist", "TPE2", "aART"),
        ("date", "TDRC", "©day"), ("genre", "TCON", "©gen"),
    ))

    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise OSError("File changed while it was being inspected")
    digest = _sha256(path, should_cancel)
    hashed = path.stat()
    if after.st_size != hashed.st_size or after.st_mtime_ns != hashed.st_mtime_ns:
        raise OSError("File changed while it was being hashed")

    return AudioFileInfo(
        path=str(path.resolve()),
        size_bytes=hashed.st_size,
        sha256=digest,
        title=title,
        album=album,
        album_artist=album_artist,
        artist=artist,
        duration_seconds=duration,
        bitrate=bitrate,
        lossless=path.suffix.casefold() in LOSSLESS_EXTENSIONS,
        chapter_count=chapter_count,
        has_cover=has_cover,
        metadata_fields=metadata_fields,
    )


def scan_directory(
    root: Path,
    on_progress: Callable[[int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    include_fingerprints: bool = False,
    workers: int = 1,
) -> ScanSummary:
    """Recursively inspect supported audio files with bounded concurrency."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)

    candidates: list[Path] = []
    issues: list[ScanIssue] = []

    def record_walk_error(error: OSError) -> None:
        issues.append(ScanIssue(
            str(error.filename or root),
            f"Directory could not be scanned: {error}",
        ))

    for current, directories, filenames in os.walk(
        root, followlinks=False, onerror=record_walk_error
    ):
        directories[:] = sorted(
            name for name in directories
            if not (Path(current) / name).is_symlink()
            and name.casefold() != "_audiobook review"
        )
        for filename in filenames:
            candidate = Path(current) / filename
            if candidate.suffix.casefold() in AUDIO_EXTENSIONS and not candidate.is_symlink():
                candidates.append(candidate)

    fingerprinting = include_fingerprints
    if fingerprinting and not _fpcalc_available():
        issues.append(ScanIssue(str(root), "Audio fingerprinting needs the Chromaprint fpcalc executable."))
        fingerprinting = False

    candidates.sort()
    worker_count = max(1, int(workers))
    cancellation_lock = threading.Lock()

    def is_cancelled() -> bool:
        if should_cancel is None:
            return False
        with cancellation_lock:
            return should_cancel()

    def inspect_candidate(candidate: Path) -> tuple[AudioFileInfo, ScanIssue | None]:
        item = inspect_file(candidate, is_cancelled)
        fingerprint_issue = None
        if fingerprinting:
            try:
                duration, fingerprint = _fingerprint_audio(candidate)
                item = replace(item, fingerprint_duration=duration, fingerprint=fingerprint)
            except Exception as error:
                fingerprint_issue = ScanIssue(str(candidate), f"Fingerprint unavailable: {error}")
        return item, fingerprint_issue

    discovered: dict[Path, AudioFileInfo] = {}
    file_issues: list[ScanIssue] = []
    pending = {}
    next_candidate = 0
    completed_count = 0
    cancelled = False
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        while next_candidate < len(candidates) or pending:
            if is_cancelled():
                cancelled = True
                break

            while next_candidate < len(candidates) and len(pending) < worker_count * 2:
                if is_cancelled():
                    cancelled = True
                    break
                candidate = candidates[next_candidate]
                next_candidate += 1
                pending[executor.submit(inspect_candidate, candidate)] = candidate
            if cancelled or not pending:
                break

            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                candidate = pending.pop(future)
                try:
                    item, fingerprint_issue = future.result()
                    discovered[candidate] = item
                    if fingerprint_issue:
                        file_issues.append(fingerprint_issue)
                except ScanCancelled:
                    cancelled = True
                except Exception as error:
                    file_issues.append(ScanIssue(str(candidate), str(error)))
                completed_count += 1
                if on_progress:
                    on_progress(completed_count, str(candidate))

            if cancelled:
                break

        if cancelled:
            for future in pending:
                future.cancel()

    ordered_files = tuple(discovered[candidate] for candidate in candidates if candidate in discovered)
    issues.extend(sorted(file_issues, key=lambda issue: (issue.path.casefold(), issue.message)))
    return ScanSummary(ordered_files, tuple(issues), cancelled=cancelled)