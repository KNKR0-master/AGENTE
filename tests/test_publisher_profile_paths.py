import unittest
from pathlib import Path

from src.publisher.profile_paths import (
    browser_profile_id,
    browser_profile_root,
    resolve_browser_profile,
)


class PublisherProfilePathTests(unittest.TestCase):
    def test_root_uses_local_app_data(self):
        root = browser_profile_root(environ={"LOCALAPPDATA": r"C:\Users\Fixture\AppData\Local"})
        self.assertEqual(root, Path(r"C:\Users\Fixture\AppData\Local\PublisherDesk\browser_profiles").resolve())

    def test_legacy_reference_keeps_only_stable_profile_id(self):
        legacy = "data/publisher/browser_profiles/a9a3acebc275495a8271b74e73faeab6"
        self.assertEqual(browser_profile_id(legacy), "a9a3acebc275495a8271b74e73faeab6")
        resolved = resolve_browser_profile(legacy, environ={"LOCALAPPDATA": r"C:\Local"})
        self.assertEqual(resolved, Path(r"C:\Local\PublisherDesk\browser_profiles\a9a3acebc275495a8271b74e73faeab6").resolve())

    def test_explicit_root_is_available_for_isolated_tests(self):
        resolved = resolve_browser_profile("fixture", environ={"PUBLISHER_BROWSER_PROFILE_ROOT": r"C:\Temp\profiles"})
        self.assertEqual(resolved, Path(r"C:\Temp\profiles\fixture").resolve())

    def test_invalid_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "INVALID_BROWSER_PROFILE_ID"):
            browser_profile_id("../..")


if __name__ == "__main__":
    unittest.main()
