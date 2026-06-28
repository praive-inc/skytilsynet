#!/usr/bin/env python3
"""
Tests for the static governance-rating table (issue #9).

governance_for is a pure lookup: a jurisdiction string -> the cited democracy
rating of the country that governs it, or None when not rated. No network.
Run:  python3 -m unittest -v
"""
import unittest

import governance


class GovernanceFor(unittest.TestCase):
    def test_us_resolves_to_freedom_house_democracy(self):
        g = governance.governance_for("United States (CLOUD Act)")
        self.assertEqual(g["country"], "United States")
        self.assertEqual(g["tier"], "democracy")
        self.assertEqual(g["status"], "Free")
        self.assertEqual(g["score"], 81)
        self.assertEqual(g["year"], 2026)
        self.assertIn("Freedom House", g["index"])
        self.assertIn("freedomhouse.org", g["sourceUrl"])

    def test_russia_origin_resolves_to_authoritarian(self):
        g = governance.governance_for("Russia (origin)")
        self.assertEqual(g["country"], "Russia")
        self.assertEqual(g["tier"], "authoritarian")
        self.assertEqual(g["status"], "Not Free")

    def test_eu_members_are_democracies(self):
        self.assertEqual(governance.governance_for("Germany (EU)")["tier"], "democracy")
        self.assertEqual(governance.governance_for("France (EU)")["country"], "France")

    def test_norway_eea_resolves(self):
        g = governance.governance_for("Norway (EEA)")
        self.assertEqual(g["country"], "Norway")
        self.assertEqual(g["tier"], "democracy")

    def test_non_eu_jurisdiction_still_carries_a_rating(self):
        # The non-EU axis is separate; governance still resolves for Switzerland.
        self.assertEqual(
            governance.governance_for("Switzerland (non-EU)")["country"], "Switzerland")

    def test_china_generalises_even_though_absent_from_no_data(self):
        # The table must generalise to the wider "who governs the jurisdiction"
        # frame (issue #9), not just the countries seen in NO kommune email.
        g = governance.governance_for("China")
        self.assertEqual(g["tier"], "authoritarian")
        self.assertEqual(g["score"], 9)

    def test_undetermined_and_empty_have_no_rating(self):
        self.assertIsNone(governance.governance_for("Undetermined"))
        self.assertIsNone(governance.governance_for(""))
        self.assertIsNone(governance.governance_for(None))

    def test_every_rating_is_cited_and_dated(self):
        # CLAUDE.md rule 1: never a label without its source.
        for jur in ("United States (CLOUD Act)", "Germany (EU)", "Russia (origin)"):
            g = governance.governance_for(jur)
            self.assertTrue(g["sourceUrl"].startswith("https://"))
            self.assertEqual(g["year"], governance.FH_YEAR)
            self.assertTrue(g["index"])

    def test_tier_is_derived_only_from_status(self):
        # Factual frame, not editorial: tier is a pure function of FH status.
        self.assertEqual(governance.TIER["Free"], "democracy")
        self.assertEqual(governance.TIER["Partly Free"], "partly free")
        self.assertEqual(governance.TIER["Not Free"], "authoritarian")


if __name__ == "__main__":
    unittest.main()
