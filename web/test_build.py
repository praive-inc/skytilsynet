#!/usr/bin/env python3
"""
Tests for the Skybarometeret static-site build.

The build is a pure transform (scan data + history + snapshots -> one HTML
file). We exercise the real seams: the honest trend computation against the
actual snapshot shape, and a full render against the real dataset asserting the
acceptance criteria are present in the output. Run:  python3 -m unittest -v
"""
import json
import unittest

import build


def snap(date, platforms):
    """A minimal snapshot dict shaped like scanner/snapshots/*.json."""
    return {
        "date": date,
        "kommuner": [{"kommune": n, "platform": p} for n, p in platforms.items()],
    }


class ComputeTrend(unittest.TestCase):
    def test_none_when_missing_a_snapshot(self):
        self.assertIsNone(build.compute_trend(None, snap("2026-06-28", {"A": "US_MICROSOFT"})))

    def test_counts_who_left_microsoft(self):
        old = snap("2026-06-27", {"A": "US_MICROSOFT", "B": "US_MICROSOFT"})
        new = snap("2026-06-28", {"A": "US_MICROSOFT", "B": "EU_SOVEREIGN"})
        t = build.compute_trend(old, new)
        self.assertEqual(t["left_microsoft"], ["B"])
        self.assertEqual(t["joined_microsoft"], [])
        self.assertEqual((t["from_date"], t["to_date"]), ("2026-06-27", "2026-06-28"))

    def test_counts_who_joined_microsoft(self):
        old = snap("2026-06-27", {"A": "OTHER"})
        new = snap("2026-06-28", {"A": "US_MICROSOFT"})
        t = build.compute_trend(old, new)
        self.assertEqual(t["joined_microsoft"], ["A"])
        self.assertEqual(t["left_microsoft"], [])

    def test_ignores_non_microsoft_churn(self):
        # OTHER -> EU_SOVEREIGN is neither leaving nor joining Microsoft.
        old = snap("2026-06-27", {"A": "OTHER"})
        new = snap("2026-06-28", {"A": "EU_SOVEREIGN"})
        t = build.compute_trend(old, new)
        self.assertEqual(t["left_microsoft"], [])
        self.assertEqual(t["joined_microsoft"], [])


# A small but representative dataset covering every platform class + a flag.
DATA = {
    "meta": {"sourceDate": "2026-06-28", "title": "t", "license": "CC BY 4.0"},
    "summary": {"total": 4, "us_total": 2, "us_pct": 50.0, "microsoft_pct": 25.0,
                "eu_sovereign": 1, "other": 1},
    "kommuner": [
        {"kommune": "Oslo", "domain": "oslo.kommune.no", "platform": "US_MICROSOFT",
         "jurisdiction": "United States (CLOUD Act)",
         "alternative": "openDesk (Open-Xchange + Nextcloud) / LibreOffice",
         "behind_gateway": False, "flags": [], "fingerprint": "autodiscover",
         "evidence": {"mx": ["0 oslo.mail.protection.outlook.com"],
                      "spf": "v=spf1 include:spf.protection.outlook.com -all",
                      "autodiscover": "autodiscover.outlook.com"},
         "sourceDate": "2026-06-28"},
        {"kommune": "Bærum", "domain": "baerum.kommune.no", "platform": "US_MICROSOFT",
         "jurisdiction": "United States (CLOUD Act)",
         "alternative": "openDesk (Open-Xchange + Nextcloud) / LibreOffice",
         "behind_gateway": True, "flags": ["backend_unmasked"], "fingerprint": None,
         "evidence": {"mx": ["10 gw.tmes.trendmicro.eu"], "spf": "", "autodiscover": None},
         "sourceDate": "2026-06-28"},
        {"kommune": "Vest-Lofoten", "domain": "nykommuneilofoten.no", "platform": "EU_SOVEREIGN",
         "jurisdiction": "Norway (EEA)", "alternative": None,
         "behind_gateway": False, "flags": [], "fingerprint": None,
         "evidence": {"mx": ["10 mx.domeneshop.no"], "spf": "v=spf1 ~all", "autodiscover": None},
         "sourceDate": "2026-06-28"},
        {"kommune": "Alvdal", "domain": "alvdal.kommune.no", "platform": "OTHER",
         "jurisdiction": "Undetermined", "alternative": None,
         "behind_gateway": False, "flags": [], "fingerprint": None,
         "evidence": {"mx": ["10 se.mx1.mailanyone.net"], "spf": "v=spf1 ~all", "autodiscover": None},
         "sourceDate": "2026-06-28"},
    ],
}
HISTORY = [
    {"date": "2026-06-27", "microsoft_pct": 90.2, "us_pct": 91.3},
    {"date": "2026-06-28", "microsoft_pct": 91.6, "us_pct": 92.7},
]
TREND = {"from_date": "2026-06-27", "to_date": "2026-06-28",
         "left_microsoft": [], "joined_microsoft": ["Kautokeino"]}


class BuildHtml(unittest.TestCase):
    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND)

    def test_disclaimer_present(self):
        # CLAUDE.md rule 2 — load-bearing, must be in the document.
        self.assertIn("ikke et offentlig organ", self.html)
        self.assertIn("not a government body", self.html)

    def test_every_kommune_is_in_the_page(self):
        for k in DATA["kommuner"]:
            self.assertIn(k["kommune"], self.html)

    def test_baked_data_is_valid_json_and_round_trips(self):
        start = self.html.index('id="data"')
        open_tag = self.html.index(">", start) + 1
        close = self.html.index("</script>", open_tag)
        blob = self.html[open_tag:close].replace("<\\/", "</")
        payload = json.loads(blob)
        self.assertEqual(len(payload["kommuner"]), 4)
        self.assertEqual(payload["trend"]["joined_microsoft"], ["Kautokeino"])

    def test_four_facts_framing(self):
        # platform / jurisdiction / data residency / contract value
        for label in ["Plattform", "jurisdiksjon", "oppholdssted", "Kontraktsverdi"]:
            self.assertIn(label, self.html)

    def test_states_the_fact_never_bad(self):
        self.assertIn("CLOUD Act", self.html)
        # Never moralizes.
        self.assertNotIn("dårlig", self.html.lower())

    def test_switch_map_with_washing_flags(self):
        self.assertIn("openDesk", self.html)
        self.assertIn("OVHcloud", self.html)
        self.assertIn("suverenitetsvasking", self.html.lower())
        self.assertIn("OnlyOffice", self.html)        # Russian-origin trap
        self.assertIn("CLOUD Act", self.html)          # EU-located != EU-owned

    def test_benchmark_narrative(self):
        self.assertIn("Schleswig-Holstein", self.html)
        self.assertIn("15 mill", self.html)            # €15M/yr
        self.assertIn("Larvik", self.html)
        self.assertIn("10 mill", self.html)            # NOK 10M/yr

    def test_trend_is_data_driven_not_hardcoded(self):
        # The honest trend object must be baked in; no fabricated "3 left".
        self.assertIn('"joined_microsoft":["Kautokeino"]', self.html)

    def test_no_us_managed_serving_dependency(self):
        # RFC-001 P5: no external fetches — no CDN, fonts, map tiles.
        for bad in ["googleapis", "gstatic", "jsdelivr", "unpkg", "cloudflare",
                    "cdnjs", "mapbox", "<script src", "<link rel=\"stylesheet\""]:
            self.assertNotIn(bad, self.html)


class BuildMainOnRealData(unittest.TestCase):
    """Smoke test the real pipeline against the committed dataset."""
    def test_real_data_renders_all_kommuner(self):
        data = json.load(open(build.DATA))
        history = json.load(open(build.HISTORY))
        old, new = build.load_snapshots()
        html = build.build_html(data, history, build.compute_trend(old, new))
        self.assertIn("ikke et offentlig organ", html)
        self.assertEqual(html.count('class="cell"'), 0)  # cells are rendered client-side
        # Every kommune name survives into the baked JSON.
        for k in data["kommuner"]:
            self.assertIn(k["kommune"], html)


if __name__ == "__main__":
    unittest.main()
