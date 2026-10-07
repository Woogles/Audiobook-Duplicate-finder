"""Small, read-only benchmark for estimating useful scan concurrency."""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .scanner import AUDIO_EXTENSIONS
from .settings import MAX_SCAN_WORKERS


SAMPLE_FILE_COUNT = 4
SAMPLE_BYTES_PER_FILE = 8 * 1024 * 1024
BENCHMARK_WORKERS = (1, 2, 4)
MIN_THROUGHPUT_GAIN = 1.15


@dataclass(frozen=True)
class BenchmarkResult:
    recommended_workers: int
    throughput_mib_per_second: tuple[tuple[int, float], ...]
    files_sampled: int
    bytes_read: int


def _sample_audio_files(root: Path) -> tuple[Path, ...]:
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
    return tuple(sorted(candidates)[:SAMPLE_FILE_COUNT])


def _read_sample(path: Path) -> int:
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as source:
        while bytes_read < SAMPLE_BYTES_PER_FILE:
            chunk = source.read(min(1024 * 1024, SAMPLE_BYTES_PER_FILE - bytes_read))
            if not chunk:
                break
            digest.update(chunk)
            bytes_read += len(chunk)
    return bytes_read


def _recommend_worker_count(throughput: dict[int, float]) -> int:
    if not throughput:
        return 1
    baseline = throughput.get(1, 0.0)
    best_workers, best_rate = max(throughput.items(), key=lambda item: (item[1], -item[0]))
    if best_workers == 1 or baseline <= 0 or best_rate < baseline * MIN_THROUGHPUT_GAIN:
        return 1
    return best_workers


def benchmark_workers(root: Path) -> BenchmarkResult:
    """Compare 1/2/4 workers by hashing bounded prefixes of local audio files."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    sample = _sample_audio_files(root)
    if not sample:
        raise ValueError("No supported audio files were found to benchmark.")

    cpu_limit = min(os.cpu_count() or 1, MAX_SCAN_WORKERS)
    max_workers = min(cpu_limit, len(sample), max(BENCHMARK_WORKERS))
    worker_options = tuple(count for count in BENCHMARK_WORKERS if count <= max_workers)
    rates: dict[int, float] = {}
    bytes_read = 0
    for worker_count in worker_options:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            sizes = tuple(executor.map(_read_sample, sample))
        elapsed = max(time.perf_counter() - started, 0.000001)
        bytes_read += sum(sizes)
        rates[worker_count] = sum(sizes) / (1024 * 1024) / elapsed

    return BenchmarkResult(
        recommended_workers=_recommend_worker_count(rates),
        throughput_mib_per_second=tuple(sorted(rates.items())),
        files_sampled=len(sample),
        bytes_read=bytes_read,
    )