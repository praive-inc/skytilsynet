#!/usr/bin/env python3
"""Tests for the shared CSV formula-injection guard (issue #94, CWE-1236).

The helper lives here so both the intake server (server/foi_intake.py) and the
offline review CLI (scripts/foi_review.py) reuse one neutralizer without either
package reaching into the other."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.csv_safe import csv_safe  # noqa: E402


class CsvSafeTest(unittest.TestCase):
    def test_neutralizes_formula_leaders(self):
        for bad in ("=1+1", "+1", "-1", "@SUM(A1)", "\tcmd", "\rx"):
            out = csv_safe(bad)
            self.assertTrue(out.startswith("'"), "%r not neutralized" % bad)
            self.assertEqual(out[1:], bad)

    def test_leaves_ordinary_values_untouched(self):
        for ok in ("Acos WebSak", "Norge (EØS)", "https://ex.org/svar", "", None):
            self.assertEqual(csv_safe(ok), "" if ok is None else ok)


if __name__ == "__main__":
    unittest.main()
