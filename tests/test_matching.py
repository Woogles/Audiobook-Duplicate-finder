import unittest
from unittest.mock import patch

from audiobook_finder.matching import Confidence, Edition, compare_editions, preference_key


class MatchingTests(unittest.TestCase):
    def test_identical_hash_sets_are_strong_even_when_names_differ(self):
        first = Edition("A Book", ("A. Author",), 36000, ("abc", "def"))
        second = Edition("Renamed", (), 0, ("def", "abc"))

        self.assertEqual(compare_editions(first, second).confidence, Confidence.STRONG)

    def test_close_title_author_and_duration_is_strong(self):
        first = Edition("The Example Book", ("A. Author",), 36000, ("one",))
        second = Edition("The Example Book", ("A. Author",), 36600, ("two",))

        self.assertEqual(compare_editions(first, second).confidence, Confidence.STRONG)

    def test_duration_difference_keeps_match_in_review(self):
        first = Edition("The Example Book", ("A. Author",), 36000, ("one",))
        second = Edition("The Example Book", ("A. Author",), 30000, ("two",))

        self.assertEqual(compare_editions(first, second).confidence, Confidence.REVIEW)

    def test_unrelated_titles_are_not_candidates(self):
        first = Edition("The Example Book", ("A. Author",), 36000, ("one",))
        second = Edition("A Different Story", ("A. Author",), 36000, ("two",))

        self.assertEqual(compare_editions(first, second).confidence, Confidence.NONE)

    def test_strong_audio_fingerprint_matches_different_names(self):
        first = Edition("Unrelated folder name", (), 36000, ("one",), fingerprints=((36000, "fp-left"),))
        second = Edition("Another label", (), 36000, ("two",), fingerprints=((36000, "fp-right"),))

        with patch("audiobook_finder.matching._compare_fingerprint_pair", return_value=0.96):
            from audiobook_finder.matching import _compare_fingerprint_pair
            _compare_fingerprint_pair.cache_clear()
            result = compare_editions(first, second)

        self.assertEqual(result.confidence, Confidence.STRONG)
        self.assertTrue(any("Audio fingerprints agree" in reason for reason in result.reasons))

    def test_raw_fingerprint_comparison_detects_aligned_content(self):
        from audiobook_finder.matching import _compare_fingerprint_pair

        _compare_fingerprint_pair.cache_clear()
        result = _compare_fingerprint_pair(120, "1,2,3,4,5", 120, "0,1,2,3,4,5")

        self.assertEqual(result, 1.0)

    def test_preference_favors_lossless_chapterized_complete_edition(self):
        preferred = Edition("Book", (), 36000, (), file_count=12, lossless=True, chapter_count=40)
        other = Edition("Book", (), 36000, (), file_count=1, average_bitrate=320000)

        self.assertGreater(preference_key(preferred), preference_key(other))

    def test_higher_bitrate_takes_precedence_over_chapter_layout(self):
        high_quality = Edition("Book", (), 36000, (), average_bitrate=320000)
        chapterized = Edition("Book", (), 36000, (), file_count=12, average_bitrate=128000, chapter_count=12)

        self.assertGreater(preference_key(high_quality), preference_key(chapterized))


if __name__ == "__main__":
    unittest.main()