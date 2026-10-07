import tempfile
import unittest
from pathlib import Path

from audiobook_finder.operations import REVIEW_DIRECTORY
from audiobook_finder.workflow import scan_and_process


class WorkflowTests(unittest.TestCase):
    def test_completed_scan_moves_only_identical_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "Book Copy A" / "chapter.mp3"
            second = root / "Book Copy B" / "chapter.mp3"
            for path in (first, second):
                path.parent.mkdir()
                path.write_bytes(b"same audio bytes")

            result = scan_and_process(root)

            self.assertEqual(len(result.moved), 1)
            self.assertEqual(result.review_groups, ())
            self.assertEqual(sum(path.exists() for path in (first, second)), 1)
            self.assertTrue((root / REVIEW_DIRECTORY).is_dir())

    def test_cancelled_scan_does_not_move_any_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.mp3").write_bytes(b"same")
            (root / "two.mp3").write_bytes(b"same")

            result = scan_and_process(root, should_cancel=lambda: True)

            self.assertTrue(result.cancelled)
            self.assertEqual(result.moved, ())
            self.assertTrue((root / "one.mp3").exists())
            self.assertTrue((root / "two.mp3").exists())


if __name__ == "__main__":
    unittest.main()