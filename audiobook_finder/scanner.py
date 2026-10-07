"""Read-only discovery and inspection of local audiobook files."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
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
    return bool(shutil.which(command) or Path(command).is_file())


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
) -> ScanSummary:
    """Recursively scan supported audio files; the source tree is never changed."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)

    candidates: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if not (Path(current) / name).is_symlink()
            and name.casefold() != "_audiobook review"
        )
        for filename in filenames:
            candidate = Path(current) / filename
            if candidate.suffix.casefold() in AUDIO_EXTENSIONS and not candidate.is_symlink():
                candidates.append(candidate)

    discovered: list[AudioFileInfo] = []
    issues: list[ScanIssue] = []
    fingerprinting = include_fingerprints
    if fingerprinting and not _fpcalc_available():
        issues.append(ScanIssue(str(root), "Audio fingerprinting needs pyacoustid and fpcalc (Chromaprint)."))
        fingerprinting = False
    for index, candidate in enumerate(sorted(candidates), start=1):
        if should_cancel and should_cancel():
            return ScanSummary(tuple(discovered), tuple(issues), cancelled=True)
        if on_progress:
            on_progress(index, str(candidate))
        try:
            item = inspect_file(candidate, should_cancel)
            if fingerprinting:
                try:
                    duration, fingerprint = _fingerprint_audio(candidate)
                    item = replace(item, fingerprint_duration=duration, fingerprint=fingerprint)
                except Exception as error:
                    issues.append(ScanIssue(str(candidate), f"Fingerprint unavailable: {error}"))
            discovered.append(item)
        except ScanCancelled:
            return ScanSummary(tuple(discovered), tuple(issues), cancelled=True)
        except Exception as error:
            issues.append(ScanIssue(str(candidate), str(error)))
    return ScanSummary(tuple(discovered), tuple(issues))