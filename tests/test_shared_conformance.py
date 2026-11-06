"""Run the public selection and lifecycle contract in both collection orders."""

import unittest

from tests.support import CASES, case, read, reversed_case, run_case


class SharedConformanceTests(unittest.TestCase):
    def test_published_cases(self):
        manifest = read(CASES / "manifest.json")
        for name in manifest["suites"]["selection"]["cases"]:
            data, expected = case(name)
            for reverse in (False, True):
                with self.subTest(case=name, reversed=reverse):
                    run_case(self, reversed_case(data) if reverse else data, expected)


if __name__ == "__main__":
    unittest.main()
