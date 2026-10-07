import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_finder.settings import load_library_path, save_library_path, validate_library_path


class SettingsTests(unittest.TestCase):
    def test_valid_existing_directory_is_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(validate_library_path(temporary), Path(temporary).resolve())

    def test_nonexistent_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with self.assertRaises(ValueError):
                validate_library_path(missing)

    def test_file_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            file_path = Path(temporary) / "not-a-folder.txt"
            file_path.write_text("test", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                validate_library_path(file_path)

    def test_saved_path_loads_from_per_user_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "library"
            library.mkdir()
            with patch.dict("os.environ", {"LOCALAPPDATA": temporary}):
                self.assertEqual(save_library_path(library), library.resolve())
                self.assertEqual(load_library_path(), str(library.resolve()))


if __name__ == "__main__":
    unittest.main()