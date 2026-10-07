import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_finder.benchmark import (
    _recommend_worker_count,
    benchmark_workers,
)


class BenchmarkTests(unittest.TestCase):
    def test_recommendation_requires_meaningful_throughput_gain(self):
        self.assertEqual(_recommend_worker_count({1: 10.0, 2: 11.0, 4: 10.5}), 1)
        self.assertEqual(_recommend_worker_count({1: 10.0, 2: 12.0, 4: 11.0}), 2)

    def test_benchmark_uses_bounded_sample_and_returns_read_volume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3", "e.mp3"):
                (root / name).write_bytes(b"sample")

            with patch("audiobook_finder.benchmark.os.cpu_count", return_value=4), \
                    patch("audiobook_finder.benchmark._read_sample", return_value=6) as read_sample, \
                    patch("audiobook_finder.benchmark.time.perf_counter", side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0]):
                result = benchmark_workers(root)

            self.assertEqual(result.files_sampled, 4)
            self.assertEqual(result.bytes_read, 4 * 6 * 3)
            self.assertEqual(result.recommended_workers, 1)
            self.assertEqual(read_sample.call_count, 12)


if __name__ == "__main__":
    unittest.main()