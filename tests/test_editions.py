import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_finder.editions import build_editions, find_duplicate_groups
from audiobook_finder.matching import Confidence
from audiobook_finder.scanner import AudioFileInfo


def file_info(path, album, digest, duration=1000, artist="Author"):
    return AudioFileInfo(
        path=str(path), size_bytes=10, sha256=digest, title=Path(path).stem,
        album=album, album_artist=artist, artist=artist, duration_seconds=duration,
        bitrate=128000, lossless=False, chapter_count=0, has_cover=False, metadata_fields=4,
    )


class EditionTests(unittest.TestCase):
    def test_chapters_and_disc_subfolders_form_one_edition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = (
                file_info(root / "Book" / "Disc 1" / "01.mp3", "Book", "one"),
                file_info(root / "Book" / "Disc 2" / "02.mp3", "Book", "two"),
            )

            editions = build_editions(files, root)

            self.assertEqual(len(editions), 1)
            self.assertEqual(editions[0].file_count, 2)

    def test_copies_in_separate_folders_remain_separate_editions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = (
                file_info(root / "Copy A" / "01.mp3", "Book", "one"),
                file_info(root / "Copy B" / "01.mp3", "Book", "two"),
            )

            editions = build_editions(files, root)

            self.assertEqual(len(editions), 2)
            self.assertEqual(find_duplicate_groups(editions)[0].confidence, Confidence.STRONG)

    def test_root_level_untagged_books_keep_their_own_titles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = (
                file_info(root / "First Story.m4b", "", "one"),
                file_info(root / "Second Story.m4b", "", "two"),
            )

            editions = build_editions(files, root)

            self.assertEqual({edition.title for edition in editions}, {"First Story", "Second Story"})

    def test_fingerprints_find_candidates_with_unrelated_titles(self):
        from audiobook_finder.matching import Edition, _compare_fingerprint_pair

        editions = (
            Edition("Unknown Label One", (), 36000, ("one",), fingerprints=((36000, "left-fp"),)),
            Edition("Different Folder", (), 36000, ("two",), fingerprints=((36000, "right-fp"),)),
        )
        with patch("audiobook_finder.matching._compare_fingerprint_pair", return_value=0.96):
            _compare_fingerprint_pair.cache_clear()
            groups = find_duplicate_groups(editions)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].confidence, Confidence.STRONG)

    def test_identical_file_content_can_link_differently_named_editions(self):
        first = type("Edition", (), {})
        del first
        from audiobook_finder.matching import Edition

        editions = (
            Edition("Mystery", (), 2000, ("same",), paths=("one",)),
            Edition("Unknown Name", (), 0, ("same",), paths=("two",)),
        )

        groups = find_duplicate_groups(editions)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].confidence, Confidence.STRONG)


if __name__ == "__main__":
    unittest.main()