import unittest

from app.exiftool_runner import ExifToolRunner, find_exiftool


class ExifToolRunnerTests(unittest.TestCase):
    def test_bundled_exiftool_is_found_and_has_version(self):
        executable = find_exiftool()
        self.assertIsNotNone(executable)
        runner = ExifToolRunner(executable)
        version = runner.version()
        self.assertIsNotNone(version)
        self.assertRegex(version or "", r"^\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
