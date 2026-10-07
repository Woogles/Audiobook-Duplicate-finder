import tempfile
import unittest
import json
from pathlib import Path

from audiobook_finder.editions import DuplicateGroup
from audiobook_finder.matching import Confidence, Edition
from audiobook_finder.operations import REVIEW_DIRECTORY, move_edition, move_strong_duplicates, undo_last_move


class OperationTests(unittest.TestCase):
    def test_move_and_undo_restore_every_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Book" / "01.mp3"
            source.parent.mkdir()
            source.write_bytes(b"chapter")
            edition = Edition("Book", (), 120, ("hash",), paths=(str(source),))

            moved = move_edition(edition, root)
            destination = Path(moved[0][1])
            self.assertTrue(destination.is_file())
            self.assertFalse(source.exists())
            restored = undo_last_move(root)

            self.assertEqual(len(restored), 1)
            self.assertEqual(source.read_bytes(), b"chapter")
            self.assertTrue((root / REVIEW_DIRECTORY).is_dir())

    def test_only_strong_groups_are_moved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preferred_path = root / "Preferred" / "01.mp3"
            duplicate_path = root / "Duplicate" / "01.mp3"
            for path in (preferred_path, duplicate_path):
                path.parent.mkdir()
                path.write_bytes(path.parent.name.encode())
            preferred = Edition("Book", (), 100, ("a",), lossless=True, paths=(str(preferred_path),))
            duplicate = Edition("Book", (), 100, ("b",), paths=(str(duplicate_path),))
            strong = DuplicateGroup((preferred, duplicate), Confidence.STRONG, ())
            review = DuplicateGroup((preferred, duplicate), Confidence.REVIEW, ())

            result = move_strong_duplicates((review, strong), root)

            self.assertEqual(len(result), 1)
            self.assertTrue(preferred_path.exists())
            self.assertFalse(duplicate_path.exists())

    def test_move_rejects_sources_outside_library(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            source = Path(outside) / "file.mp3"
            source.write_bytes(b"outside")
            edition = Edition("Book", (), 1, (), paths=(str(source),))

            with self.assertRaises(ValueError):
                move_edition(edition, root)

    def test_undo_recovers_move_interrupted_before_completion_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Book" / "chapter.mp3"
            destination = root / REVIEW_DIRECTORY / "Book" / "partial" / "chapter.mp3"
            source.parent.mkdir()
            source.write_bytes(b"chapter")
            destination.parent.mkdir(parents=True)
            transaction = "interrupted"
            log_path = root / REVIEW_DIRECTORY / "_move-log.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps({
                "event": "intent", "transaction": transaction,
                "source": str(source), "destination": str(destination),
            }) + "\n", encoding="utf-8")
            source.replace(destination)

            restored = undo_last_move(root)

            self.assertEqual(len(restored), 1)
            self.assertEqual(source.read_bytes(), b"chapter")


if __name__ == "__main__":
    unittest.main()