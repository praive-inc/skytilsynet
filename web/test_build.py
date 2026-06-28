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
# `evidence` is the issue-#8 audit trail: a list of per-signal records, each
# with its source + date; `verdict` is the confidence-weighted call.
def sig(t, obs, src, inf, conf, plat):
    return {"signal_type": t, "observation": obs, "source": src,
            "observed_at": "2026-06-28", "inference": inf,
            "confidence": conf, "platform": plat}


DATA = {
    "meta": {"sourceDate": "2026-06-28", "title": "t", "license": "CC BY 4.0"},
    "summary": {"total": 4, "us_total": 2, "us_pct": 50.0, "microsoft_pct": 25.0,
                "eu_sovereign": 1, "other": 1},
    "kommuner": [
        {"kommune": "Oslo", "domain": "oslo.kommune.no", "platform": "US_MICROSOFT",
         "jurisdiction": "United States (CLOUD Act)",
         "alternative": "openDesk (Open-Xchange + Nextcloud) / LibreOffice",
         "behind_gateway": False, "flags": [], "fingerprint": "autodiscover",
         "verdict": {"platform": "US_MICROSOFT", "label": "Microsoft 365",
                     "confidence": 0.95, "uavklart": False, "note": None},
         "evidence": [
             sig("mx", "0 oslo.mail.protection.outlook.com", "dig MX oslo.kommune.no",
                 "MX leverer e-post til Microsoft 365", 0.95, "US_MICROSOFT"),
             sig("spf", "v=spf1 include:spf.protection.outlook.com -all",
                 "dig TXT oslo.kommune.no (v=spf1)",
                 "SPF autoriserer Microsoft", 0.9, "US_MICROSOFT"),
             sig("autodiscover", "autodiscover.outlook.com",
                 "dig CNAME autodiscover.oslo.kommune.no",
                 "autodiscover → outlook.com: Microsoft 365-leietaker", 0.8, "US_MICROSOFT")],
         "sourceDate": "2026-06-28"},
        {"kommune": "Bærum", "domain": "baerum.kommune.no", "platform": "US_MICROSOFT",
         "jurisdiction": "United States (CLOUD Act)",
         "alternative": "openDesk (Open-Xchange + Nextcloud) / LibreOffice",
         "behind_gateway": False, "flags": [], "fingerprint": "spf-ms-ip",
         "verdict": {"platform": "US_MICROSOFT", "label": "Microsoft 365",
                     "confidence": 0.95, "uavklart": False, "note": None},
         "evidence": [
             sig("spf", "v=spf1 ip4:40.92.1.5 -all", "dig TXT baerum.kommune.no (v=spf1)",
                 "SPF uten gjenkjent plattform", 0.3, None),
             sig("spf_ip", "40.92.1.5", "ip4 i SPF for baerum.kommune.no ∈ Microsoft EOP-områder",
                 "Microsoft EOP-IP 40.92.1.5 inlinet i flatet SPF — bevis for Microsoft",
                 0.95, "US_MICROSOFT")],
         "sourceDate": "2026-06-28"},
        {"kommune": "Vest-Lofoten", "domain": "nykommuneilofoten.no", "platform": "EU_SOVEREIGN",
         "jurisdiction": "Norway (EEA)", "alternative": None,
         "behind_gateway": False, "flags": [], "fingerprint": None,
         "verdict": {"platform": "EU_SOVEREIGN", "label": "Europeisk / norsk drift",
                     "confidence": 0.9, "uavklart": False, "note": None},
         "evidence": [
             sig("mx", "10 mx.domeneshop.no", "dig MX nykommuneilofoten.no",
                 "MX peker på europeisk/norsk e-postdrift", 0.9, "EU_SOVEREIGN")],
         "sourceDate": "2026-06-28"},
        {"kommune": "Alvdal", "domain": "alvdal.kommune.no", "platform": "OTHER",
         "jurisdiction": "Undetermined", "alternative": None,
         "behind_gateway": False, "flags": [], "fingerprint": None,
         "verdict": {"platform": "UAVKLART", "label": "Uavklart", "confidence": 0.3,
                     "uavklart": True,
                     "note": "Regional/ukjent plattform — ikke avgjort fra DNS alene"},
         "evidence": [
             sig("mx", "10 se.mx1.mailanyone.net", "dig MX alvdal.kommune.no",
                 "Ukjent eller gateway-maskert MX — plattform ikke avgjort", 0.3, None)],
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

    def test_evidence_trail_is_baked_per_signal_with_source_and_date(self):
        # Issue #8: every signal is a citable record (source query + observed_at).
        self.assertIn('"signal_type"', self.html)
        self.assertIn('"observed_at"', self.html)
        self.assertIn("dig MX oslo.kommune.no", self.html)        # exact query cited
        self.assertIn("dig CNAME autodiscover.oslo.kommune.no", self.html)

    def test_detail_renders_evidence_trail_and_confidence(self):
        # The detail view must iterate the per-signal trail and show confidence.
        self.assertIn("k.evidence", self.html)
        self.assertIn("konfidens", self.html.lower())
        self.assertIn("Vis hvordan vi vet det", self.html)        # 'show your work' heading

    def test_matched_ms_ip_signal_is_highlighted(self):
        # The spf_ip signal carries the matched MS IP and the template marks it.
        self.assertIn("spf_ip", self.html)
        self.assertIn("40.92.1.5", self.html)

    def test_uavklart_verdict_is_baked_honestly(self):
        # Alvdal can't be resolved -> honest Uavklart, not a guess.
        self.assertIn('"uavklart":true', self.html)
        self.assertIn("ikke avgjort fra DNS", self.html)

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
