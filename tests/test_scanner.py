import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_finder.scanner import AUDIO_EXTENSIONS, _fpcalc_available, scan_directory


class ScannerTests(unittest.TestCase):
    def test_extension_list_covers_audiobook_formats(self):
        self.assertTrue({".mp3", ".m4a", ".m4b", ".flac"}.issubset(AUDIO_EXTENSIONS))

    def test_scan_hashes_supported_files_and_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "chapter.MP3"
            audio.write_bytes(b"audio bytes")
            (root / "notes.txt").write_text("not audio", encoding="utf-8")
            review_file = root / "_Audiobook Review" / "old copy" / "old.mp3"
            review_file.parent.mkdir(parents=True)
            review_file.write_bytes(b"already reviewed")

            result = scan_directory(root)

            self.assertEqual(len(result.files), 1)
            self.assertEqual(result.files[0].title, "chapter")
            self.assertEqual(result.files[0].size_bytes, len(b"audio bytes"))
            self.assertFalse(result.issues)

    def test_scan_honors_cancellation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.mp3").write_bytes(b"one")
            (root / "two.m4b").write_bytes(b"two")

            result = scan_directory(root, should_cancel=lambda: True)

            self.assertTrue(result.cancelled)
            self.assertEqual(result.files, ())

    def test_scan_can_cancel_while_hashing_a_large_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "large.mp3").write_bytes(b"x" * (5 * 1024 * 1024))
            calls = 0

            def cancel_after_scan_starts():
                nonlocal calls
                calls += 1
                return calls >= 2

            result = scan_directory(root, should_cancel=cancel_after_scan_starts)

            self.assertTrue(result.cancelled)
            self.assertEqual(result.files, ())

    def test_missing_fingerprint_tools_do_not_abort_regular_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "chapter.mp3").write_bytes(b"audio bytes")

            with patch("audiobook_finder.scanner._fpcalc_available", return_value=False):
                result = scan_directory(root, include_fingerprints=True)

            self.assertEqual(len(result.files), 1)
            self.assertTrue(any("fpcalc" in issue.message for issue in result.issues))

    def test_fpcalc_must_be_launchable(self):
        with patch("audiobook_finder.scanner.shutil.which", return_value="C:/tools/fpcalc.exe"), \
                patch("audiobook_finder.scanner.subprocess.run") as run:
            self.assertTrue(_fpcalc_available())
            run.assert_called_once()

            run.side_effect = OSError("access denied")
            self.assertFalse(_fpcalc_available())

    def test_directory_traversal_errors_are_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def failing_walk(path, *, followlinks, onerror):
                onerror(PermissionError("share is unavailable"))
                return iter(())

            with patch("audiobook_finder.scanner.os.walk", side_effect=failing_walk):
                result = scan_directory(root)

            self.assertEqual(len(result.issues), 1)
            self.assertIn("Directory could not be scanned", result.issues[0].message)


if __name__ == "__main__":
    unittest.main()