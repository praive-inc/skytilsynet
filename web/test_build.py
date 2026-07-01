#!/usr/bin/env python3
"""
Tests for the Skybarometeret static-site build.

The build is a pure transform (scan data + history + snapshots -> one HTML
file). We exercise the real seams: the honest trend computation against the
actual snapshot shape, and a full render against the real dataset asserting the
acceptance criteria are present in the output. Run:  python3 -m unittest -v
"""
import json
import os
import re
import unittest

import build


def snap(date, platforms, version=None):
    """A minimal snapshot dict shaped like scanner/snapshots/*.json.

    `version` is the methodology version the snapshot was produced under; omit it
    to model a pre-versioning snapshot (treated as version 1)."""
    s = {
        "date": date,
        "kommuner": [{"kommune": n, "platform": p} for n, p in platforms.items()],
    }
    if version is not None:
        s["methodology_version"] = version
    return s


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

    def test_cross_version_is_new_baseline_not_a_count(self):
        # Issue #24: a scanner classification change must not be reported as
        # movement. Across versions we honestly declare a new baseline.
        old = snap("2026-06-27", {"A": "OTHER", "B": "OTHER"}, version=1)
        new = snap("2026-06-28", {"A": "US_MICROSOFT", "B": "US_MICROSOFT"}, version=2)
        t = build.compute_trend(old, new)
        self.assertTrue(t["new_baseline"])
        self.assertEqual(t["baseline_date"], "2026-06-28")
        # No spurious "joined Microsoft" count across the recalibration.
        self.assertNotIn("joined_microsoft", t)
        self.assertNotIn("left_microsoft", t)

    def test_same_version_computes_movement(self):
        old = snap("2026-06-28", {"A": "OTHER"}, version=2)
        new = snap("2026-06-29", {"A": "US_MICROSOFT"}, version=2)
        t = build.compute_trend(old, new)
        self.assertFalse(t["new_baseline"])
        self.assertEqual(t["joined_microsoft"], ["A"])

    def test_missing_version_defaults_consistently(self):
        # Two pre-versioning snapshots (no field) compare normally as one version.
        old = snap("2026-06-27", {"A": "US_MICROSOFT"})
        new = snap("2026-06-28", {"A": "EU_SOVEREIGN"})
        t = build.compute_trend(old, new)
        self.assertFalse(t["new_baseline"])
        self.assertEqual(t["left_microsoft"], ["A"])


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
                "us_microsoft": 1, "eu_sovereign": 1, "other": 1},
    "kommuner": [
        {"kommune": "Oslo", "domain": "oslo.kommune.no", "platform": "US_MICROSOFT",
         "jurisdiction": "United States (CLOUD Act)",
         "governance": {"country": "United States",
                        "index": "Freedom House (Freedom in the World)", "score": 81,
                        "status": "Free", "tier": "democracy",
                        "sourceUrl": "https://freedomhouse.org/country/united-states/freedom-world/2026",
                        "year": 2026},
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
         "jurisdiction": "Undetermined", "governance": None, "alternative": None,
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

# Second category: statlige organ. Records use `name`/`category` (no `kommune` key).
STAT = {
    "meta": {"sourceDate": "2026-06-28", "title": "stat", "license": "CC BY 4.0"},
    "summary": {"total": 2, "us_total": 2, "us_pct": 100.0, "microsoft_pct": 100.0,
                "us_microsoft": 2, "eu_sovereign": 0, "other": 0},
    "organ": [
        {"name": "NAV (Arbeids- og velferdsetaten)", "category": "stat", "domain": "nav.no",
         "platform": "US_MICROSOFT", "jurisdiction": "United States (CLOUD Act)",
         "alternative": "openDesk (Open-Xchange + Nextcloud) / LibreOffice",
         "behind_gateway": True, "flags": [], "fingerprint": "dkim",
         "evidence": {"mx": ["5 mxa-003ba702.gslb.pphosted.com"],
                      "spf": "v=spf1 include:spf.protection.outlook.com -all",
                      "autodiscover": None},
         "sourceDate": "2026-06-28"},
        {"name": "Skatteetaten", "category": "stat", "domain": "skatteetaten.no",
         "platform": "US_MICROSOFT", "jurisdiction": "United States (CLOUD Act)",
         "alternative": "openDesk (Open-Xchange + Nextcloud) / LibreOffice",
         "behind_gateway": False, "flags": [], "fingerprint": "mx/spf",
         "evidence": {"mx": ["10 skatteetaten-no.mail.protection.outlook.com"],
                      "spf": "v=spf1 include:spf.protection.outlook.com -all",
                      "autodiscover": None},
         "sourceDate": "2026-06-28"},
    ],
}


# The web axis (issue #13): website-infrastructure sovereignty, a SECOND distinct
# axis joined onto each entity by website domain. Only some entities are covered.
WEB = {
    "meta": {"sourceDate": "2026-06-28", "title": "web", "license": "CC BY 4.0",
             "axis_note": "Distinct axis from the email scan."},
    "summary": {"total": 2, "us_hosted": 1, "us_hosted_pct": 50.0, "analytics": 1},
    "kommuner": [
        {"kommune": "Oslo", "axis": "web", "domain": "oslo.kommune.no",
         "url": "https://www.oslo.kommune.no/", "host": "www.oslo.kommune.no",
         "hosting": {"ip": "13.1.1.1", "asn": "8075", "country": "US",
                     "name": "microsoft-corp", "jurisdiction": "United States (CLOUD Act)"},
         "third_parties": [
             {"domain": "www.google-analytics.com", "category": "analytics",
              "jurisdiction": "United States (CLOUD Act)", "flags": ["analytics"]}],
         "us_resource_fraction": 1.0, "analytics": True,
         "flags": ["us_hosted", "analytics"],
         "evidence": {"server": "Microsoft-IIS/10.0", "x_powered_by": None,
                      "csp": None, "tls_issuer": "DigiCert", "security_txt": False},
         "sourceDate": "2026-06-28"},
        {"kommune": "Vest-Lofoten", "axis": "web", "domain": "nykommuneilofoten.no",
         "url": "https://nykommuneilofoten.no/", "host": "nykommuneilofoten.no",
         "hosting": {"ip": "194.63.1.1", "asn": "2116", "country": "NO",
                     "name": "domeneshop", "jurisdiction": "NO (EEA)"},
         "third_parties": [], "us_resource_fraction": 0.0, "analytics": False,
         "flags": [],
         "evidence": {"server": "nginx", "x_powered_by": None, "csp": None,
                      "tls_issuer": "Let's Encrypt", "security_txt": True},
         "sourceDate": "2026-06-28"},
    ],
}


class WebAxisJoin(unittest.TestCase):
    """Issue #13: web-axis records joined onto each entity by website domain and
    surfaced as their OWN axis (distinct from email), cited."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB)
        # The web axis is joined onto the full entity, which now lives in the
        # on-demand per-category detail file (#34), not inlined on the page.
        self.cats = build.build_categories(DATA, STAT, WEB)
        self.files = build.detail_files(self.cats)

    def _kommune(self, name):
        arr = json.loads(self.files["detail-kommune.json"])
        return next(k for k in arr if k["name"] == name)

    def test_web_record_joined_onto_entity_by_domain(self):
        oslo = self._kommune("Oslo")
        self.assertIsNotNone(oslo["web"])
        self.assertEqual(oslo["web"]["hosting"]["jurisdiction"], "United States (CLOUD Act)")
        self.assertTrue(oslo["web"]["analytics"])

    def test_entity_without_web_scan_has_none(self):
        # Bærum is in the email dataset but not in the web dataset.
        self.assertIsNone(self._kommune("Bærum")["web"])

    def test_web_axis_is_distinct_not_conflated_with_email(self):
        # The web axis must not alter the email verdict/platform of the entity.
        oslo = self._kommune("Oslo")
        self.assertEqual(oslo["platform"], "US_MICROSOFT")
        self.assertEqual(oslo["verdict"]["platform"], "US_MICROSOFT")

    def test_web_axis_rendered_as_own_cited_section(self):
        # The detail view renders a dedicated, clearly-separate web-axis section.
        self.assertIn("k.web", self.html)              # detail iterates the joined record
        self.assertIn("Web-akse", self.html)           # its own heading
        self.assertIn("nettstedets infrastruktur", self.html)

    def test_kommune_only_still_builds_without_web(self):
        # Backward compatible: the web dataset is optional.
        html = build.build_html(DATA, HISTORY, TREND, STAT)
        self.assertIn("Oslo", html)
        cats = build.build_categories(DATA, STAT)
        arr = json.loads(build.detail_files(cats)["detail-kommune.json"])
        self.assertIsNone(next(k for k in arr if k["name"] == "Oslo")["web"])


class BuildHtml(unittest.TestCase):
    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT)
        # The full per-entity records (evidence, governance, web) are no longer
        # inlined — they live in the on-demand per-category detail files (#34).
        self.cats = build.build_categories(DATA, STAT)
        self.files = build.detail_files(self.cats)

    def _detail(self, key):
        return json.loads(self.files["detail-%s.json" % key])

    def _payload(self):
        start = self.html.index('id="data"')
        open_tag = self.html.index(">", start) + 1
        close = self.html.index("</script>", open_tag)
        return json.loads(self.html[open_tag:close].replace("<\\/", "</"))

    def test_disclaimer_present(self):
        # CLAUDE.md rule 2 — load-bearing, must be in the document.
        self.assertIn("ikke et offentlig organ", self.html)
        self.assertIn("not a government body", self.html)

    def test_disclaimer_is_a_collapsed_accordion(self):
        # Issue #45 — the load-bearing headline stays visible, the supporting
        # detail collapses. A real <button> with aria-expanded, collapsed by
        # default (aria-expanded="false"), keyboard/SR accessible.
        self.assertRegex(self.html, r'<button[^>]*aria-expanded="false"')
        # The headline lives inside the toggle button, always visible.
        btn = self.html[self.html.index('class="disclaimer'):]
        btn = btn[:btn.index("</button>")]
        self.assertIn("ikke et offentlig organ", btn)
        # An expand affordance is present.
        self.assertIn("vis mer", self.html)

    def test_disclaimer_detail_is_present_but_controlled(self):
        # Full supporting text still shipped (expands on click), and the
        # button controls the detail region via aria-controls.
        self.assertIn("Datatilsynet", self.html)
        self.assertIn("Metode og kilder", self.html)
        self.assertRegex(self.html, r'aria-controls="([^"]+)"')

    def test_every_kommune_is_in_the_page(self):
        for k in DATA["kommuner"]:
            self.assertIn(k["kommune"], self.html)

    def test_baked_data_is_valid_json_and_round_trips(self):
        payload = self._payload()
        cats = {c["key"]: c for c in payload["categories"]}
        self.assertEqual(len(cats["kommune"]["entities"]), 4)
        self.assertEqual(len(cats["stat"]["entities"]), 2)
        self.assertEqual(payload["trend"]["joined_microsoft"], ["Kautokeino"])

    def test_both_categories_with_labels(self):
        # Acceptance: kommuner + statlige organ shown as categories.
        payload = self._payload()
        keys = [c["key"] for c in payload["categories"]]
        self.assertEqual(keys, ["kommune", "stat"])
        labels = [c["label"] for c in payload["categories"]]
        self.assertIn("Kommuner", labels)
        self.assertIn("Statlige organ", labels)
        self.assertIn("Statlige organ", self.html)

    def test_statlige_organ_names_in_page(self):
        for name in ["NAV", "Skatteetaten"]:
            self.assertIn(name, self.html)

    def test_combined_headline_covers_both_categories(self):
        # Acceptance: top-line % is the COMBINED scanned public sector.
        payload = self._payload()
        c = payload["combined"]
        self.assertEqual(c["total"], 6)                 # 4 kommuner + 2 organ
        # microsoft: 1 of 4 kommuner + 2 of 2 organ = 3 / 6 = 50.0 %
        self.assertEqual(c["microsoft_pct"], 50.0)
        # Each category keeps its own %.
        cats = {x["key"]: x for x in payload["categories"]}
        self.assertEqual(cats["kommune"]["summary"]["microsoft_pct"], 25.0)
        self.assertEqual(cats["stat"]["summary"]["microsoft_pct"], 100.0)

    def test_every_entity_carries_cited_evidence(self):
        # Acceptance: every verdict still links to cited evidence — now carried in
        # the on-demand per-category detail file (#34), not inlined on the page.
        for c in self.cats:
            for e in self._detail(c["key"]):
                self.assertIn("evidence", e)
                self.assertTrue(e["sourceDate"])

    def test_inline_payload_is_light_not_the_full_evidence(self):
        # #34 performance: the inlined entities carry only the light grid/search
        # fields — never the heavy evidence/governance/web/verdict (those load on
        # demand). This is what keeps first paint small.
        payload = self._payload()
        for c in payload["categories"]:
            for e in c["entities"]:
                self.assertEqual(set(e), set(build.LIGHT_FIELDS))
                for heavy in ("evidence", "governance", "web", "verdict"):
                    self.assertNotIn(heavy, e)

    def test_kommune_only_still_builds_without_stat(self):
        # Backward compatible: omitting the second category renders kommune-only.
        html = build.build_html(DATA, HISTORY, TREND)
        self.assertIn("ikke et offentlig organ", html)
        self.assertIn("Oslo", html)

    def test_four_facts_framing(self):
        # platform / jurisdiction / data residency / contract value
        for label in ["Plattform", "jurisdiksjon", "oppholdssted", "Kontraktsverdi"]:
            self.assertIn(label, self.html)

    def test_states_the_fact_never_bad(self):
        self.assertIn("CLOUD Act", self.html)
        # Never moralizes.
        self.assertNotIn("dårlig", self.html.lower())

    def test_governance_frame_is_rendered_and_cited(self):
        # issue #9: the per-kommune verdict gains a governance frame, factual.
        self.assertIn("Styresett", self.html)          # the governance fact label
        self.assertIn("Freedom House", self.html)       # the cited index
        self.assertIn("freedomhouse.org", self.html)    # the source link

    def test_governance_is_baked_per_kommune(self):
        # Governance rides along the full entity in the on-demand detail file (#34).
        by_name = {k["name"]: k for k in self._detail("kommune")}
        self.assertEqual(by_name["Oslo"]["governance"]["tier"], "democracy")
        self.assertEqual(by_name["Oslo"]["governance"]["score"], 81)
        self.assertIsNone(by_name["Alvdal"]["governance"])  # Undetermined -> none

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
        # Accurate Larvik framing: case-handling doc production off Word/Google Docs
        # via the editor built into Acos WebSak — NOT the (conflated) NOK-10M claim.
        self.assertIn("Acos", self.html)
        self.assertNotIn("10 mill", self.html)         # the 10M was a separate 2019 move

    def test_evidence_trail_is_baked_per_signal_with_source_and_date(self):
        # Issue #8: every signal is a citable record (source query + observed_at) —
        # in the on-demand detail file (#34), fetched when a card is opened.
        blob = self.files["detail-kommune.json"]
        self.assertIn('"signal_type"', blob)
        self.assertIn('"observed_at"', blob)
        self.assertIn("dig MX oslo.kommune.no", blob)             # exact query cited
        self.assertIn("dig CNAME autodiscover.oslo.kommune.no", blob)

    def test_detail_renders_evidence_trail_and_confidence(self):
        # The detail view must iterate the per-signal trail and show confidence.
        self.assertIn("k.evidence", self.html)
        self.assertIn("konfidens", self.html.lower())
        self.assertIn("Vis hvordan vi vet det", self.html)        # 'show your work' heading

    def test_matched_ms_ip_signal_is_highlighted(self):
        # The spf_ip signal carries the matched MS IP; the template marks it (the
        # rendering stays inline), the IP itself rides the on-demand detail file.
        self.assertIn("spf_ip", self.html)
        self.assertIn("40.92.1.5", self.files["detail-kommune.json"])

    def test_uavklart_verdict_is_baked_honestly(self):
        # Alvdal can't be resolved -> honest Uavklart, not a guess. The verdict is
        # in the detail file; the "ikke avgjort fra DNS" copy is rendered inline.
        self.assertIn('"uavklart":true', self.files["detail-kommune.json"])
        self.assertIn("ikke avgjort fra DNS", self.files["detail-kommune.json"])
        # The inline detail template still renders the honest fallback copy.
        self.assertIn("Ikke avgjort fra DNS", self.html)

    def test_trend_is_data_driven_not_hardcoded(self):
        # The honest trend object must be baked in; no fabricated "3 left".
        self.assertIn('"joined_microsoft":["Kautokeino"]', self.html)

    def test_new_baseline_trend_renders_honest_copy(self):
        # Issue #24: at a methodology change the card says "ny baseline", not a
        # movement count. Bake a cross-version trend and assert the copy is wired.
        baseline = {"new_baseline": True, "baseline_date": "2026-06-28",
                    "methodology_version": 2}
        html = build.build_html(DATA, HISTORY, baseline, STAT)
        self.assertIn('"new_baseline":true', html)
        self.assertIn("ny baseline", html)
        # No fabricated movement count across a methodology recalibration.
        self.assertIn("ikke et bytte-tall", html)

    def test_no_us_managed_serving_dependency(self):
        # RFC-001 P5: no external fetches — no CDN, fonts, map tiles.
        for bad in ["googleapis", "gstatic", "jsdelivr", "unpkg", "cloudflare",
                    "cdnjs", "mapbox", "<script src", "<link rel=\"stylesheet\""]:
            self.assertNotIn(bad, self.html)


class GoalSection(unittest.TestCase):
    """Issue #14 — the 'Målet' campaign centerpiece: current sovereign share +
    progress bar toward 25 % (2030), a client-side countdown to 17. mai 2027, and
    the milestone ladder. Numbers come from the live dataset, not hardcoded."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT)

    def _goal(self):
        start = self.html.index('id="data"')
        open_tag = self.html.index(">", start) + 1
        close = self.html.index("</script>", open_tag)
        payload = json.loads(self.html[open_tag:close].replace("<\\/", "</"))
        return payload["goal"]

    def test_sovereign_share_comes_from_the_dataset(self):
        # Combined: 1 eu_sovereign of 6 scanned (4 kommuner + 2 organ) = 16.7 %.
        g = self._goal()
        self.assertEqual(g["sovereign_pct"], round(100 * 1 / 6, 1))
        self.assertEqual(g["sovereign_count"], 1)
        self.assertEqual(g["total"], 6)

    def test_targets_are_baked(self):
        # The goal definition (campaign constants) rides along the data.
        g = self._goal()
        self.assertEqual(g["target_pct"], 25)
        self.assertEqual(g["target_year"], 2030)
        self.assertEqual(g["first_target"], "2027-05-17")

    def test_ladder_rungs_are_baked(self):
        years = [r["year"] for r in self._goal()["ladder"]]
        self.assertEqual(years, [2026, 2027, 2028, 2030, 2035])

    def test_section_heading_and_milestone_names_render(self):
        self.assertIn("Suverenitetsmålet", self.html)
        self.assertIn("Erkjennelsen", self.html)
        self.assertIn("Den første", self.html)
        self.assertIn("Vendepunktet", self.html)

    def test_progress_bar_is_present(self):
        self.assertIn("goal-bar", self.html)

    def test_countdown_is_computed_client_side_from_a_baked_date(self):
        # The page stays static: the target date is baked, the days remaining are
        # computed in the browser (new Date()), never baked as a stale number.
        self.assertIn('"first_target":"2027-05-17"', self.html)
        self.assertIn("new Date", self.html)
        self.assertIn("dager", self.html)

    def test_target_share_is_not_hardcoded_in_markup(self):
        # The big current number is rendered from goal.sovereign_pct, so the static
        # shell must not carry a baked-in share string.
        self.assertNotIn("0,3 %", self._TEMPLATE_SHELL())

    def _TEMPLATE_SHELL(self):
        # The static template before the data blob is substituted.
        return build._TEMPLATE


class DesignPolish(unittest.TestCase):
    """Issue #15: a cohesive visual system + an accessibility pass. The page
    stays a single self-contained file with no external/US-managed serving deps,
    so every assertion is structural/textual against the generated HTML."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB)

    def test_skip_link_to_main_content(self):
        # WCAG 2.4.1 bypass blocks: a skip link targeting the main landmark.
        self.assertIn('class="skip"', self.html)
        self.assertIn('href="#main"', self.html)

    def test_semantic_landmarks_present(self):
        # Screen-reader navigation needs real landmarks, not div soup.
        self.assertIn('<main', self.html)
        self.assertIn('id="main"', self.html)
        self.assertIn("<header", self.html)
        self.assertIn("<nav", self.html)
        self.assertIn("<footer", self.html)

    def test_focus_is_visible_for_keyboard_users(self):
        # WCAG 2.4.7 — a visible focus indicator for interactive elements.
        self.assertIn(":focus-visible", self.html)

    def test_respects_reduced_motion_preference(self):
        # The progress fill + live countdown must yield to a motion preference.
        self.assertIn("prefers-reduced-motion", self.html)

    def test_design_tokens_define_a_type_and_space_scale(self):
        # A coherent system: shared scale tokens, not ad-hoc px sprinkled around.
        self.assertIn("--space-", self.html)
        self.assertIn("--text-", self.html)

    def test_dataviz_carries_a_text_alternative(self):
        # The sparkline + goal progress encode data visually — label them.
        self.assertIn('role="img"', self.html)
        self.assertIn("aria-label", self.html)

    def test_english_about_block_is_marked_lang_en(self):
        # The English summary is in another language than the nb document.
        self.assertIn('lang="en"', self.html)

    def test_toggle_controls_expose_their_pressed_state(self):
        # Filter chips + category tabs are toggle buttons (WCAG 4.1.2).
        self.assertIn("aria-pressed", self.html)

    def test_live_countdown_is_not_announced_every_second(self):
        # The ticking seconds must not spam assistive tech.
        self.assertIn('aria-hidden="true"', self.html)


class ActivismFunnel(unittest.TestCase):
    """Issue #3: the activism funnel. Each entity detail view emits copy-ready
    citizen tooling — a pre-filled offentleglova innsyn request for BOTH
    categories, plus the category-specific lever (kommune: minsak.no
    innbyggerforslag; statlig organ: skriftlig spørsmål via a Stortinget rep).
    Templates are baked client-side from the entity name; no per-citizen data is
    collected; the page stays plain static."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB)

    def test_funnel_is_attached_to_the_detail_view(self):
        # The detail render must emit the funnel for every entity it shows.
        self.assertIn("renderFunnel", self.html)
        self.assertIn("renderFunnel(k", self.html)

    def test_innsyn_request_is_offentleglova_grounded(self):
        # Copy-ready innsyn (FOI) request, cited to offentleglova.
        self.assertIn("offentleglova", self.html)
        self.assertIn("Innsynskrav", self.html)
        # The M365/cloud contract + DPIA are the documents requested.
        self.assertIn("databehandleravtale", self.html)
        self.assertIn("personvernkonsekvensvurdering", self.html.lower())

    def test_innsyn_cites_the_five_working_day_response(self):
        # The statutory response window is cited, not invented.
        self.assertIn("fem", self.html.lower())
        self.assertIn("§ 29", self.html)

    def test_innsyn_is_offered_for_both_categories(self):
        # The innsyn lever works for state bodies too, so it must not be gated to
        # the kommune branch — it sits in the shared part of the funnel.
        self.assertIn("buildInnsyn", self.html)
        self.assertIn("buildInnsyn(k", self.html)

    def test_kommune_lever_is_minsak_innbyggerforslag(self):
        self.assertIn("innbyggerforslag", self.html)
        self.assertIn("minsak.no", self.html)
        self.assertIn("300", self.html)               # 300 signatures forces a position
        self.assertIn("kommuneloven", self.html)

    def test_stat_lever_is_written_question_via_stortinget(self):
        self.assertIn("skriftlig spørsmål", self.html)
        self.assertIn("stortinget.no", self.html)
        # The category-specific lever is chosen by catKey.
        self.assertIn('catKey==="kommune"', self.html)

    def test_messaging_frames_the_durable_procurement_ask(self):
        # CLAUDE.md rule 6 / Munich LiMux: the ask is a procurement-rule / strategy
        # change, never a personal attack on named officials.
        self.assertIn("anskaffelse", self.html.lower())
        self.assertIn("strategi", self.html.lower())

    def test_funnel_keeps_no_per_citizen_records(self):
        # Rule 5: aggregate-only. The funnel must not collect/submit citizen data —
        # no form, no e-mail capture field, copy-to-clipboard / mailto only.
        self.assertNotIn("<form", self.html)
        self.assertNotIn('type="email"', self.html)
        self.assertIn("clipboard", self.html)

    def test_copy_button_is_present(self):
        self.assertIn("Kopier", self.html)


class ShareCard(unittest.TestCase):
    """Issue #25: a per-entity shareable 'del'-card that turns visitors into
    distributors. A screenshot-friendly card (name + email verdict +
    jurisdiction + governance + the headline floor + skytilsynet.no), one-tap
    Web Share where available with a copy-link fallback, plus a fully
    client-side SVG -> canvas -> PNG download (no external image service). The
    fact is the provocation — no editorializing. Page stays plain static."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB)

    def test_share_card_is_attached_to_the_detail_view(self):
        # Rendered for every entity the detail view shows (both categories).
        self.assertIn("renderShareCard", self.html)
        self.assertIn("renderShareCard(k", self.html)
        self.assertIn('id="sharecard"', self.html)

    def test_share_card_is_factual_and_carries_the_url(self):
        # Name + email verdict + jurisdiction + the brand URL, factual framing.
        self.assertIn("sc-verdict", self.html)
        self.assertIn("E-POST SVARER TIL", self.html)
        self.assertIn("skytilsynet.no", self.html)
        # The headline floor (combined US share) rides along as context.
        self.assertIn("amerikansk jurisdiksjon", self.html)

    def test_governance_frame_is_on_the_card_when_known(self):
        # The governance tier rides along when the jurisdiction is determined.
        self.assertIn("sc-gov", self.html)
        self.assertIn("Styresett", self.html)

    def test_web_share_api_with_copy_link_fallback(self):
        # One-tap Web Share where available, else copy the deep link.
        self.assertIn("navigator.share", self.html)
        self.assertIn("navigator.clipboard", self.html)
        self.assertIn("location.href", self.html)

    def test_image_download_is_client_side_svg_to_canvas(self):
        # "Last ned bilde": render the card to an SVG, rasterize via canvas, no
        # external image/canvas service.
        self.assertIn("Last ned bilde", self.html)
        self.assertIn("image/svg+xml", self.html)
        self.assertIn("toBlob", self.html)
        self.assertIn("createObjectURL", self.html)

    def test_share_card_states_the_fact_never_editorializes(self):
        # The fact is the provocation; the card must not moralize.
        self.assertNotIn("dårlig", self.html.lower())
        self.assertNotIn("skammelig", self.html.lower())

    def test_no_external_loads_added_by_the_card(self):
        # RFC-001 P5: still fully self-contained, no social SDK / external image
        # service / web font.
        for bad in ["facebook", "twitter.com", "x.com/intent", "platform.linkedin",
                    "addthis", "sharethis", "googleapis", "htmlcsstoimage"]:
            self.assertNotIn(bad, self.html.lower())


def _png_size(path):
    """Width, height of a PNG read straight from its IHDR — stdlib only, no PIL,
    so the dimension assertion does not depend on an image library."""
    import struct
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path + " is not a PNG"
    return struct.unpack(">II", head[16:24])


class SocialPreview(unittest.TestCase):
    """Issue #32: Open Graph + Twitter card meta in <head> so a shared link
    renders a compelling, factual preview, plus a self-hosted, on-brand
    1200x630 og:image served straight from web/ (no external image service, no
    build step in prod)."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB)
        # The meta only matters inside <head>.
        self.head = self.html[:self.html.index("</head>")]

    def test_open_graph_core_tags_present(self):
        for prop in ["og:title", "og:description", "og:image", "og:url",
                     "og:type", "og:locale"]:
            self.assertIn('property="' + prop + '"', self.head, prop + " missing")
        # Norwegian Bokmål locale, factual title.
        self.assertIn('content="nb_NO"', self.head)
        self.assertIn('property="og:type" content="website"', self.head)

    def test_twitter_summary_large_image_card(self):
        self.assertIn('name="twitter:card" content="summary_large_image"', self.head)
        for name in ["twitter:title", "twitter:description", "twitter:image"]:
            self.assertIn('name="' + name + '"', self.head, name + " missing")

    def test_og_image_is_an_absolute_self_hosted_url(self):
        # Scrapers need an absolute URL; it must point at our own host, not an
        # external image service (RFC-001 P5).
        m = re.search(r'property="og:image" content="([^"]+)"', self.head)
        self.assertIsNotNone(m)
        url = m.group(1)
        self.assertTrue(url.startswith("https://skytilsynet.no/"), url)
        self.assertTrue(url.endswith(build.OG_IMAGE), url)
        self.assertEqual(url, "https://skytilsynet.no/" + build.OG_IMAGE)

    def test_og_url_is_the_canonical_site(self):
        self.assertIn('property="og:url" content="https://skytilsynet.no/"', self.head)

    def test_title_and_description_are_factual_and_striking(self):
        # The hook is the fact (the US floor), never a moral judgement.
        haystack = self.head.lower()
        self.assertIn("9 av 10", self.head)
        self.assertIn("usa", haystack)
        self.assertIn("cloud act", haystack)
        for bad in ["dårlig", "skammelig", "forræderi", "skandale"]:
            self.assertNotIn(bad, haystack)

    def test_og_image_file_is_committed_and_1200x630(self):
        path = os.path.join(build.HERE, build.OG_IMAGE)
        self.assertTrue(os.path.exists(path), build.OG_IMAGE + " not committed in web/")
        self.assertEqual(_png_size(path), (1200, 630))

    def test_no_external_image_host_in_head(self):
        # The preview image must be served from web/ via Caddy, never a third party.
        for bad in ["i.imgur", "cloudinary", "imgix", "googleusercontent",
                    "htmlcsstoimage", "og-image.vercel"]:
            self.assertNotIn(bad, self.head.lower())


class OmOgMetode(unittest.TestCase):
    """Issue #30: a public, readable Om & Metode section — the credibility
    backbone for launch/press scrutiny. It restates the disclaimer, explains the
    email signals + the gateway unmasking + the FLOOR caveat + the web second
    axis, cites the governance source with its year, states the methodology
    version (trend compares same-version only), links the CC-BY datasets per
    category, and points to the corrections channel. Plain static, no build
    step (build_html is a pure transform)."""

    def setUp(self):
        trend = {**TREND, "new_baseline": False, "methodology_version": 2}
        self.html = build.build_html(DATA, HISTORY, trend, STAT, WEB)

    def test_section_present_and_linked_from_the_page(self):
        self.assertIn('id="om"', self.html)
        self.assertIn("Om &amp; Metode", self.html)
        self.assertIn('href="#om"', self.html)   # header + footer link target

    def test_disclaimer_restated_in_the_about_block(self):
        self.assertIn("Hva dette er", self.html)
        # Load-bearing disclaimer appears here too, not only in the top banner.
        self.assertGreaterEqual(self.html.count("ikke et offentlig organ"), 2)

    def test_method_explains_every_email_signal(self):
        for s in ["MX", "SPF", "autodiscover", "getuserrealm", "DKIM", "EOP"]:
            self.assertIn(s, self.html)

    def test_gateway_unmasking_and_floor_caveat(self):
        self.assertIn("gateway", self.html.lower())
        self.assertIn("Gulv, ikke tak", self.html)        # the floor caveat, named
        self.assertIn("uavdekket", self.html.lower())

    def test_web_axis_described_as_a_distinct_second_axis(self):
        self.assertIn("Web-akse", self.html)
        self.assertIn("andre akse", self.html.lower())

    def test_governance_source_cited_with_year_and_tiers(self):
        self.assertIn("Freedom House", self.html)
        self.assertIn("freedomhouse.org", self.html)
        self.assertIn("2026", self.html)
        for tier in ["Demokrati", "Delvis fritt", "Autoritært"]:
            self.assertIn(tier, self.html)

    def test_methodology_version_stated_and_baked(self):
        self.assertIn("Metodikk-versjon", self.html)
        self.assertIn('"methodology_version":2', self.html)   # baked, not hardcoded
        self.assertIn('id="methodology-version"', self.html)  # JS fills it
        self.assertIn("samme", self.html.lower())             # same-version caveat

    def test_ccby_per_category_download_links(self):
        self.assertIn("CC BY 4.0", self.html)
        for fn, _ in build.DATA_DOWNLOADS:
            self.assertIn('href="data/' + fn + '"', self.html)
        self.assertIn("Attribusjon", self.html)

    def test_corrections_channel_noted(self):
        self.assertIn("Retting", self.html)
        self.assertIn("kommer ved lansering", self.html)

    def test_builds_when_trend_carries_no_methodology_version(self):
        # Too few snapshots -> trend is None; the section must still render.
        html = build.build_html(DATA, HISTORY, None, STAT, WEB)
        self.assertIn("Metodikk-versjon", html)
        self.assertIn('"methodology_version":null', html)


class DataDownloads(unittest.TestCase):
    """The deploy rsyncs only web/ to Caddy, so the per-category open datasets the
    Om & Metode page links to must be copied into web/data/ by the build — no
    external host, no prod build step."""

    def test_downloads_list_maps_to_real_source_datasets(self):
        for fn, label in build.DATA_DOWNLOADS:
            self.assertTrue(os.path.exists(os.path.join(build.ROOT, "data", fn)),
                            fn + " is not a real published dataset")
            self.assertTrue(label)

    def test_copy_downloads_writes_each_dataset_to_target(self):
        import tempfile
        dest = tempfile.mkdtemp()
        build.copy_downloads(dest)
        for fn, _ in build.DATA_DOWNLOADS:
            self.assertTrue(os.path.exists(os.path.join(dest, fn)),
                            fn + " was not copied for serving")


class ReLadder(unittest.TestCase):
    """Issue #34 — re-ladder the page: a zero-scroll hero (the verdict as a
    sentence + one dominant gauge + the shock names), a co-equal find-your-entity
    search, a tight narrative, and the 486-organ grid + methodology demoted behind
    a click. Performance: only a LIGHT per-entity slice is inlined; the full
    evidence loads on demand same-origin. Plain static, no build step, no deps."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB)

    # --- Layer 1: the verdict sentence + gauge ---------------------------------
    def test_h1_is_the_verdict_as_a_sentence_not_a_question(self):
        # OWID rule: the title IS the finding. The old question H1 is gone.
        self.assertNotIn("Hvor avhengig er Norge", self.html)
        m = re.search(r'id="verdict-h1">([^<]+)<', self.html)
        self.assertIsNotNone(m)
        h1 = m.group(1)
        self.assertIn("kjører e-posten i USA", h1)
        self.assertRegex(h1, r"\d+ av 10")          # "X av 10 …"

    def test_verdict_sentence_is_derived_from_the_data(self):
        # The "X av 10" is the combined US share floored to a tenth, not hardcoded.
        # Fixture: 2 organ + 2 kommuner US of 6 -> us_pct 66.7 -> "6 av 10".
        c = build.combine_summaries([DATA["summary"], STAT["summary"]])
        self.assertEqual(c["us_pct"], round(100 * 4 / 6, 1))
        self.assertIn("%d av 10" % int(c["us_pct"] // 10), self.html)

    def test_dominant_gauge_is_baked_static_and_labelled(self):
        # A single dial, readable at a glance, with a text alternative — and baked
        # (no JS, no external dep) so it is on the first screen with no fetch.
        self.assertIn('class="gauge"', self.html)
        self.assertIn('role="img"', self.html)
        self.assertIn("USA-kontrollert sky", self.html)
        self.assertIn("gauge-num", self.html)          # the big % readout

    def test_shock_names_proof_line_links_to_cited_cards(self):
        # The shock names are one-line proof, each a link to its own cited card.
        self.assertIn("hero-proof", self.html)
        self.assertIn("Datatilsynet", self.html)
        self.assertIn('href="#org/stat/skatteetaten"', self.html)

    def test_proof_line_only_shows_present_us_bodies(self):
        # Honesty (rule 1): a curated name renders only if present AND US in data.
        cats = build.build_categories(DATA, STAT)
        links = build.proof_links(cats)
        # Fixture has Skatteetaten + NAV (present, US) but not Datatilsynet/Forsvaret.
        self.assertIn("Skatteetaten", links)
        self.assertIn('href="#org/stat/skatteetaten"', links)
        self.assertNotIn("Datatilsynet", links)        # absent -> never fabricated
        self.assertNotIn("Forsvaret", links)

    # --- Layer 1b: find-your-entity search -------------------------------------
    def test_find_your_entity_search_is_present(self):
        self.assertIn('id="find"', self.html)
        self.assertIn("Finn din kommune", self.html)
        self.assertIn('role="combobox"', self.html)
        self.assertIn("FIND_INDEX", self.html)         # the JS autosuggest index
        self.assertIn('location.hash = "org/"', self.html)  # resolves to one card

    # --- Layer 3: grid + methodology demoted behind a click --------------------
    def test_full_grid_is_behind_a_click(self):
        # The 486-grid sits inside a panel hidden until the explore toggle is hit.
        self.assertIn('id="explore-toggle"', self.html)
        self.assertIn('id="explore" class="hidden"', self.html)
        self.assertIn('aria-expanded="false"', self.html)

    def test_methodology_is_a_separate_view_behind_a_click(self):
        # Om & metode moves to its own #om view, hidden by default, linked from nav.
        self.assertIn('id="view-om" class="hidden"', self.html)
        self.assertIn('href="#om"', self.html)
        self.assertIn('hash==="om"', self.html)        # the route handles it
        # The methodology content moved there (not on the first screen).
        self.assertIn("Slik måler vi", self.html)

    def test_disclaimer_present_on_every_view(self):
        # Rule 2: load-bearing, rendered once outside the routed views.
        self.assertIn("ikke et offentlig organ", self.html)
        self.assertIn("not a government body", self.html)
        # It sits outside the three swappable view sections.
        disc = self.html.index("ikke et offentlig organ")
        self.assertLess(disc, self.html.index('id="view-home"'))

    # --- Performance: light inline + on-demand same-origin ---------------------
    def test_initial_payload_is_markedly_smaller(self):
        # Was ~1.7 MB with all evidence inlined; the light page must be far under.
        self.assertLess(len(self.html), 400_000)

    def test_detail_files_are_emitted_per_category_with_full_evidence(self):
        cats = build.build_categories(DATA, STAT, WEB)
        files = build.detail_files(cats)
        self.assertEqual(set(files), {"detail-kommune.json", "detail-stat.json"})
        kommune = json.loads(files["detail-kommune.json"])
        oslo = next(k for k in kommune if k["name"] == "Oslo")
        # Full record: evidence trail + governance + web axis, the heavy stuff.
        self.assertTrue(oslo["evidence"])
        self.assertEqual(oslo["governance"]["tier"], "democracy")
        self.assertIsNotNone(oslo["web"])

    def test_detail_view_loads_evidence_on_demand_same_origin(self):
        # The detail view fetches its category's file from our OWN origin (data/),
        # never an external/US-managed serving dep (RFC-001 P5).
        self.assertIn('fetch("data/detail-"', self.html)
        self.assertIn("loadCategory", self.html)
        # No external script/style/CDN was introduced by the lazy load.
        for bad in ["<script src", "googleapis", "jsdelivr", "unpkg", "cloudflare"]:
            self.assertNotIn(bad, self.html)

    def test_write_detail_files_writes_each_category(self):
        import tempfile
        dest = tempfile.mkdtemp()
        cats = build.build_categories(DATA, STAT, WEB)
        build.write_detail_files(cats, dest)
        for c in cats:
            path = os.path.join(dest, "detail-%s.json" % c["key"])
            self.assertTrue(os.path.exists(path))
            json.loads(open(path).read())           # valid JSON


class TrustArmor(unittest.TestCase):
    """Issue #35: the credibility move that survives well-funded pushback.

    Datalagring ≠ jurisdiksjon stated precisely (EU/Norway-resident data does NOT
    remove US CLOUD-Act jurisdiction) on BOTH the method view and the per-entity
    view, with the three severities kept distinct (bruker Microsoft / ingen
    EU-datagrense / US-jurisdiksjon). Plus the OWID/Faktisk trust floor: every
    evidence record carries 'Kontrollert den <dato>', a plain-language 'kva dette
    ikkje beviser' box, a loud link to the open classifier code, a named
    methodology author + the 'uavhengig prosjekt fra BetterWorld' line, and a
    public corrections log (endringslogg)."""

    def setUp(self):
        trend = {**TREND, "new_baseline": False, "methodology_version": 2}
        self.html = build.build_html(DATA, HISTORY, trend, STAT, WEB)

    def _om_view(self):
        # The methodology view (#om) region of the page.
        start = self.html.index('id="view-om"')
        return self.html[start:]

    # --- residency != jurisdiction (method + entity views) --------------------
    def test_residency_vs_jurisdiction_explained_on_method_view(self):
        om = self._om_view()
        self.assertIn("Datalagring", om)
        self.assertIn("jurisdiksjon", om.lower())
        self.assertIn("CLOUD Act", om)
        # The precise, load-bearing claim: residency does not remove jurisdiction.
        self.assertIn("opphever ikke", om.lower())

    def test_three_severities_kept_distinct_not_conflated(self):
        # "bruker Microsoft" ≠ "ingen EU-datagrense" ≠ "US-jurisdiksjon".
        low = self.html.lower()
        self.assertIn("bruker microsoft", low)
        self.assertIn("datagrense", low)
        self.assertIn("us-jurisdiksjon", low)

    def test_residency_framing_is_rendered_on_the_entity_view(self):
        # The detail render must carry the residency≠jurisdiction callout, not only
        # the method page — gated to US verdicts (where the distinction bites).
        self.assertIn("renderJurisdictionNote", self.html)
        self.assertIn("renderJurisdictionNote(k", self.html)
        self.assertIn("Datalagring ≠ jurisdiksjon", self.html)

    # --- per-record "Kontrollert den <dato>" ----------------------------------
    def test_every_evidence_record_surfaces_kontrollert_den(self):
        # OWID/Faktisk trust floor: every per-signal record shows its observed_at
        # as an explicit 'Kontrollert den <dato>' label, computed from the data.
        self.assertIn("Kontrollert den", self.html)
        self.assertIn("noDate(s.observed_at)", self.html)

    # --- "kva dette ikkje beviser" box ----------------------------------------
    def test_not_proven_box_on_entity_view(self):
        self.assertIn("renderNotProven", self.html)
        self.assertIn("renderNotProven(k", self.html)
        # Plain-language: a gateway ≠ all workloads in the US.
        self.assertIn("ikke beviser", self.html.lower())
        self.assertIn("gateway", self.html.lower())

    def test_not_proven_box_on_method_view(self):
        om = self._om_view()
        self.assertIn("ikke beviser", om.lower())

    # --- loud link to the open classifier code --------------------------------
    def test_open_classifier_code_linked_loudly(self):
        om = self._om_view()
        # Not just the repo root — the actual classifier source the verdicts run on.
        self.assertIn("scanner/scan.py", om)
        self.assertIn("github.com/praive-inc/skytilsynet", om)

    # --- named author + independence line -------------------------------------
    def test_named_methodology_author_and_independence_line(self):
        om = self._om_view()
        self.assertIn("Metodikk-ansvarlig", om)
        self.assertIn("Jøran Bjerksetmyr", om)
        # The 'uavhengig prosjekt fra BetterWorld' line is stated near the author.
        self.assertIn("uavhengig prosjekt", om.lower())
        self.assertIn("BetterWorld", om)

    # --- public corrections log (endringslogg) --------------------------------
    def test_corrections_log_section_present(self):
        om = self._om_view()
        self.assertIn("Endringslogg", om)
        self.assertIn("renderCorrections", self.html)

    def test_corrections_log_empty_state_is_honest(self):
        # Seeded empty: the log is public and states it has no entries yet — never
        # a fabricated correction.
        self.assertIn('"corrections":[]', self.html)
        self.assertIn("Ingen rettelser", self.html)

    def test_corrections_log_renders_entries_when_present(self):
        corr = [{"date": "2026-06-30", "entity": "Oslo",
                 "summary": "Rettet feilklassifisert MX."}]
        html = build.build_html(DATA, HISTORY, TREND, STAT, WEB, corrections=corr)
        self.assertIn('"corrections":[', html)
        self.assertIn("Rettet feilklassifisert MX.", html)
        self.assertIn("2026-06-30", html)

    def test_corrections_data_is_loaded_from_an_open_file(self):
        # Open by default: the log lives in a CC-BY data file the build bakes in.
        self.assertTrue(os.path.exists(build.CORRECTIONS))
        self.assertIsInstance(json.load(open(build.CORRECTIONS)), list)

    # --- still self-contained -------------------------------------------------
    def test_no_external_serving_dependency_added(self):
        for bad in ["googleapis", "jsdelivr", "unpkg", "cloudflare", "<script src",
                    "<link rel=\"stylesheet\""]:
            self.assertNotIn(bad, self.html)


# A fylkeskommune category, the unit the Norway cartogram is drawn over (issue
# #36). One US body, one fully-locked-in (federated) US body, and one OTHER
# (uavklart) — enough to exercise dependency ordering and the hall of fame.
FYLKE = {
    "meta": {"sourceDate": "2026-06-28", "title": "fylke", "license": "CC BY 4.0"},
    "summary": {"total": 3, "us_total": 2, "us_pct": 66.7, "microsoft_pct": 66.7,
                "us_microsoft": 2, "eu_sovereign": 0, "other": 1},
    "organ": [
        {"name": "Agder fylkeskommune", "category": "fylke", "domain": "agderfk.no",
         "platform": "US_MICROSOFT", "jurisdiction": "United States (CLOUD Act)",
         "behind_gateway": False, "flags": ["federated"], "fingerprint": "autodiscover",
         "sourceDate": "2026-06-28"},
        {"name": "Vestland fylkeskommune", "category": "fylke", "domain": "vlfk.no",
         "platform": "US_MICROSOFT", "jurisdiction": "United States (CLOUD Act)",
         "behind_gateway": True, "flags": [], "fingerprint": "dkim",
         "sourceDate": "2026-06-28"},
        {"name": "Akershus fylkeskommune", "category": "fylke", "domain": "afk.no",
         "platform": "OTHER", "jurisdiction": "Undetermined",
         "behind_gateway": False, "flags": [], "fingerprint": None,
         "sourceDate": "2026-06-28"},
    ],
}
SEEDED = [(FYLKE, "fylke", "Fylkeskommuner")]


class DependencyOrdering(unittest.TestCase):
    """Issue #36 — a transparent, deterministic dependency sort key built only from
    the light signals (platform + federated flag + unmasking), NOT a reimplemented
    SovereigntyScore (CLAUDE.md rule 3). Higher = more dependent on US jurisdiction."""

    def _e(self, platform, behind_gateway=False, flags=()):
        return {"platform": platform, "behind_gateway": behind_gateway, "flags": list(flags)}

    def test_us_outranks_uavklart_outranks_sovereign(self):
        ms = build.dep_score(self._e("US_MICROSOFT"))
        goog = build.dep_score(self._e("US_GOOGLE"))
        other = build.dep_score(self._e("OTHER"))
        eu = build.dep_score(self._e("EU_SOVEREIGN"))
        self.assertGreater(ms, goog)
        self.assertGreater(goog, other)
        self.assertGreater(other, eu)

    def test_federated_is_more_dependent_than_plain_microsoft(self):
        self.assertGreater(build.dep_score(self._e("US_MICROSOFT", flags=["federated"])),
                           build.dep_score(self._e("US_MICROSOFT")))

    def test_unmasked_backend_is_more_certain_than_gateway_fronted(self):
        # A fully-unmasked Microsoft body ranks above one still behind a gateway
        # (the latter is a floor, less certain) — honesty about limits.
        self.assertGreater(build.dep_score(self._e("US_MICROSOFT", behind_gateway=False)),
                           build.dep_score(self._e("US_MICROSOFT", behind_gateway=True)))


class LeagueTable(unittest.TestCase):
    """Issue #36 — pinned worst-10 + best-10. The hall of fame is honest: it holds
    ONLY genuinely non-US bodies, never padded with US ones pretending to be
    sovereign (credibility is the product)."""

    def setUp(self):
        self.cats = build.build_categories(DATA, STAT, None, SEEDED)
        self.lg = build.league(self.cats)

    def test_worst_are_all_us_and_dependency_ordered(self):
        worst = self.lg["worst"]
        self.assertTrue(all(r["platform"] in
                            ("US_MICROSOFT", "US_GOOGLE", "US_MIXED") for r in worst))
        deps = [r["dep"] for r in worst]
        self.assertEqual(deps, sorted(deps, reverse=True))

    def test_hall_of_fame_holds_only_non_us_bodies(self):
        for r in self.lg["best"]:
            self.assertNotIn(r["platform"], ("US_MICROSOFT", "US_GOOGLE", "US_MIXED"))

    def test_hall_of_fame_leads_with_the_truly_sovereign(self):
        # Vest-Lofoten (EU_SOVEREIGN) outranks the merely-undetermined ones.
        self.assertEqual(self.lg["best"][0]["platform"], "EU_SOVEREIGN")

    def test_honest_counts(self):
        # 1 EU_SOVEREIGN + 2 OTHER (Alvdal kommune + Akershus fylke) = 3 non-US,
        # of 4 kommuner + 2 stat + 3 fylke = 9 scanned.
        self.assertEqual(self.lg["sovereign_count"], 3)
        self.assertEqual(self.lg["total"], 9)

    def test_every_row_carries_a_permalink_and_a_date(self):
        for r in self.lg["worst"] + self.lg["best"]:
            self.assertTrue(r["slug"])
            self.assertIn(r["cat"], ("kommune", "stat", "fylke"))
            self.assertEqual(r["date"], "2026-06-28")


class NorwayCartogram(unittest.TestCase):
    """Issue #36 — an equal-area hex cartogram of Norway's fylker, rendered from
    committed geometry as inline SVG (no external map tiles, RFC-001 P5). Each hex
    links to that county's own entity card."""

    def setUp(self):
        self.cats = build.build_categories(DATA, STAT, None, SEEDED)
        self.svg = build.cartogram_svg(self.cats)

    def test_is_inline_svg_with_no_external_reference(self):
        self.assertIn("<svg", self.svg)
        self.assertIn("<polygon", self.svg)
        self.assertNotIn("http://", self.svg)
        self.assertNotIn("https://", self.svg)
        self.assertNotIn("<image", self.svg)  # no raster map tiles

    def test_county_hex_links_to_its_fylkeskommune_card(self):
        # Agder fylkeskommune is in the seeded data → its hex is a permalink.
        self.assertIn('href="#org/fylke/agder-fylkeskommune"', self.svg)

    def test_oslo_hex_links_to_oslo_kommune(self):
        # Oslo has no separate fylkeskommune; its hex points at the kommune card.
        self.assertIn('href="#org/kommune/oslo-kommune"', self.svg)

    def test_hex_is_coloured_by_platform(self):
        # US bodies are red, the undetermined one is not — colour carries the verdict.
        self.assertIn("var(--red)", self.svg)
        self.assertIn("var(--grey)", self.svg)


class NorwayChoropleth(unittest.TestCase):
    """Issue #46 — a REAL geographic Norway map (choropleth) alongside the hex
    cartogram, from committed simplified fylke geometry, rendered inline (no
    external tiles, RFC-001 P5). Same colour legend, same click→entity card."""

    def setUp(self):
        self.cats = build.build_categories(DATA, STAT, None, SEEDED)
        self.svg = build.choropleth_svg(self.cats)

    def test_geometry_is_committed_for_all_fifteen_fylker(self):
        import fylke_geo
        counties = {c for c, *_ in build._FYLKE_HEXES}
        self.assertEqual(len(counties), 15)
        for county in counties:
            self.assertIn(county, fylke_geo.FYLKE_PATHS)
        self.assertTrue(fylke_geo.FYLKE_VIEWBOX)

    def test_is_inline_svg_with_real_paths_no_external(self):
        self.assertIn("<svg", self.svg)
        self.assertIn("<path", self.svg)          # real geometry, not hex polygons
        self.assertNotIn("http://", self.svg)
        self.assertNotIn("https://", self.svg)
        self.assertNotIn("<image", self.svg)       # no raster map tiles

    def test_fylke_shape_links_to_its_fylkeskommune_card(self):
        self.assertIn('href="#org/fylke/agder-fylkeskommune"', self.svg)

    def test_oslo_shape_links_to_oslo_kommune(self):
        self.assertIn('href="#org/kommune/oslo-kommune"', self.svg)

    def test_shape_is_coloured_by_platform(self):
        self.assertIn("var(--red)", self.svg)
        self.assertIn("var(--grey)", self.svg)


class NorwayMapAndLeagueInPage(unittest.TestCase):
    """Issue #36 — the map + league table land in the built page, prominently and
    self-contained."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, None, SEEDED)

    def test_cartogram_rendered_into_the_home_view(self):
        self.assertIn("<svg", self.html)
        self.assertIn('href="#org/fylke/agder-fylkeskommune"', self.html)

    def test_league_panes_and_pinned_rows_present(self):
        self.assertIn("Ligatabellen", self.html)
        self.assertIn("Æresgalleriet", self.html)
        self.assertIn("Mest avhengige", self.html)
        # Pinned rows are real permalinks to evidence (entity cards).
        self.assertIn('href="#org/kommune/vest-lofoten"', self.html)

    def test_hall_of_fame_states_the_brutal_count_honestly(self):
        # "Bare 3 av 9" — the finding, not a padded top-10.
        self.assertIn("Bare 3 av 9", self.html)

    def test_rows_are_date_stamped(self):
        self.assertIn("28. juni 2026", self.html)

    def test_full_table_is_sortable(self):
        # A sortable league table over all entities (client-side, progressive).
        self.assertIn("data-sort", self.html)

    def test_no_external_map_dependency(self):
        for bad in ["openstreetmap", "mapbox", "leaflet", "tile.", "googleapis"]:
            self.assertNotIn(bad, self.html.lower())

    def test_geographic_choropleth_rendered_into_the_home_view(self):
        # Issue #46 — the real map is baked into the page too (inline <path>,
        # same click→entity permalink as the cartogram).
        self.assertIn("choropleth", self.html)
        self.assertIn("<path", self.html)

    def test_map_type_toggle_between_geographic_and_cartogram(self):
        # Both views are kept; the toggle labels name each type and expose state.
        self.assertIn("Geografisk kart", self.html)
        self.assertIn("Kartogram (likeareal)", self.html)
        self.assertIn("aria-pressed", self.html)

    def test_geographic_view_carries_the_honesty_note(self):
        # The area-vs-population caveat that justifies the cartogram existing.
        self.assertIn("map-geo-note", self.html)
        self.assertIn("nordlige", self.html)


class BuildMainOnRealData(unittest.TestCase):
    """Smoke test the real pipeline against the committed datasets."""
    def test_real_data_renders_both_categories(self):
        data = json.load(open(build.DATA))
        stat = json.load(open(build.STAT_DATA)) if os.path.exists(build.STAT_DATA) else None
        history = json.load(open(build.HISTORY))
        old, new = build.load_snapshots()
        html = build.build_html(data, history, build.compute_trend(old, new), stat)
        self.assertIn("ikke et offentlig organ", html)
        self.assertEqual(html.count('class="cell"'), 0)  # cells are rendered client-side
        # Every kommune name survives into the baked JSON.
        for k in data["kommuner"]:
            self.assertIn(k["kommune"], html)
        # And every state body, if the second category is present.
        if stat:
            for o in stat["organ"]:
                self.assertIn(o["name"], html)


class ForPresse(unittest.TestCase):
    """Issue #37: the /for-presse press kit. A journalist must be able to file in
    30 minutes — so one page gathers the CSV + stable API URLs, the "Suverenitet
    i tall" figures (all data-driven), a one-page method, same-origin embeds
    (iframe + web-component) with a frozen-as-of-date option, downloadable
    PNG/SVG graphics, and a CC-BY citation with a named contact. All baked
    static, no external dep, embeds served same-origin (RFC-001 P5)."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT)
        self.cats = build.build_categories(DATA, STAT)

    # ---- figures (data-driven, never a slogan) ------------------------------
    def test_figures_are_data_driven_5_to_8(self):
        figs = build.press_figures(self.cats)
        self.assertGreaterEqual(len(figs), 5)
        self.assertLessEqual(len(figs), 8)
        for f in figs:
            self.assertTrue(f["value"])
            self.assertTrue(f["label"])
        # The combined US floor (66,7 % over 6 organ) is computed, not hardcoded.
        combined = build.combine_summaries([c["summary"] for c in self.cats])
        self.assertIn(build._no_pct(combined["us_pct"]) + " %",
                      [f["value"] for f in figs])

    def test_i_tall_box_is_on_the_page(self):
        self.assertIn("Suverenitet i tall", self.html)
        # A computed figure value survives into the rendered box.
        combined = build.combine_summaries([c["summary"] for c in self.cats])
        self.assertIn(build._no_pct(combined["us_pct"]) + " %", self.html)

    def test_figures_state_the_fact_never_moralize(self):
        hay = " ".join(f["label"] + " " + (f.get("note") or "")
                       for f in build.press_figures(self.cats)).lower()
        for bad in ["dårlig", "skammelig", "forræderi", "skandale", "svik"]:
            self.assertNotIn(bad, hay)
        self.assertIn("gulv", hay)   # the floor caveat is carried

    # ---- the view + routing -------------------------------------------------
    def test_view_and_route_and_nav_link(self):
        self.assertIn('id="view-presse"', self.html)
        self.assertIn('id="for-presse"', self.html)
        self.assertIn('href="#for-presse"', self.html)            # masthead nav
        self.assertIn('"view-presse"', self.html)                 # showView list
        self.assertIn('hash==="for-presse"', self.html)           # route

    # ---- downloads: CSV + stable API URLs -----------------------------------
    def test_csv_per_category_and_combined(self):
        files = build.press_csv(self.cats)
        self.assertIn("skytilsynet-kombinert.csv", files)
        for c in self.cats:
            self.assertIn("skytilsynet-%s.csv" % c["key"], files)
        # Header + one row per scanned entity, source-dated.
        comb = files["skytilsynet-kombinert.csv"].strip().splitlines()
        self.assertEqual(comb[0], ",".join(build._CSV_HEADER))
        n = sum(len(c["entities"]) for c in self.cats)
        self.assertEqual(len(comb) - 1, n)
        self.assertIn("2026-06-28", files["skytilsynet-kommune.csv"])

    def test_csv_and_api_links_on_page(self):
        self.assertIn("Last ned data", self.html)
        self.assertIn('href="data/skytilsynet-kombinert.csv"', self.html)
        self.assertIn('href="data/skytilsynet-kommune.csv"', self.html)
        # The stable JSON API URLs (the published per-category datasets) are linked.
        for fn, _ in build.DATA_DOWNLOADS:
            self.assertIn('href="data/' + fn + '"', self.html)

    # ---- one-page method ----------------------------------------------------
    def test_one_page_method_present(self):
        self.assertIn("Metode på éi side", self.html)
        for term in ["MX", "SPF", "jurisdiksjon", "Gulv", "Dekning"]:
            self.assertIn(term, self.html)
        self.assertIn("scanner/scan.py", self.html)

    # ---- citation + named contact -------------------------------------------
    def test_ccby_citation_and_named_contact(self):
        self.assertIn("CC BY 4.0", self.html)
        self.assertIn("Slik siterer du oss", self.html)
        self.assertIn(build.PRESS_CONTACT_NAME, self.html)
        # Contact is the open GitHub channel — the domain has no mailbox, so a
        # mailto:presse@ would bounce. The named person stays; the channel works.
        self.assertIn("github.com/praive-inc/skytilsynet/issues", self.html)
        self.assertNotIn("mailto:presse@", self.html)

    # ---- graphics: PNG + SVG ------------------------------------------------
    def test_graphics_links_png_and_svg(self):
        for fn in ["graphics/gauge.svg", "graphics/gauge.png",
                   "graphics/kart.svg", "graphics/kart.png"]:
            self.assertIn('href="' + fn + '"', self.html)

    def test_graphics_svg_are_standalone_concrete(self):
        gfx = build.press_graphics(self.cats)
        self.assertIn("gauge.svg", gfx)
        self.assertIn("kart.svg", gfx)
        for svg in gfx.values():
            self.assertTrue(svg.startswith("<svg"))
            self.assertIn("xmlns=", svg)
            self.assertNotIn("var(--", svg)   # no CSS-var dependency on the site

    # ---- embeds: iframe + web-component, frozen-as-of-date ------------------
    def test_embed_snippets_iframe_and_web_component_on_page(self):
        self.assertIn("Bygg inn figurene", self.html)
        # iframe snippet (escaped inside the textarea) for the gauge and the map.
        self.assertIn("&lt;iframe", self.html)
        self.assertIn("skytilsynet.no/embed/gauge.html", self.html)
        self.assertIn("skytilsynet.no/embed/kart.html", self.html)
        # web-component snippet.
        self.assertIn("skytilsynet-embed.js", self.html)
        self.assertIn("skytilsynet-gauge", self.html)
        self.assertIn("skytilsynet-kart", self.html)
        # the frozen-as-of-date option.
        self.assertIn("frys-toggle", self.html)
        self.assertIn("Frys per", self.html)

    def test_embed_files_render_standalone(self):
        files = build.embed_files(self.cats, DATA["meta"])
        self.assertEqual(set(files), {"embed/gauge.html", "embed/kart.html",
                                      "embed/skytilsynet-embed.js"})
        gauge = files["embed/gauge.html"]
        self.assertIn('id="arc"', gauge)
        self.assertIn("URLSearchParams", gauge)        # reads ?pct/?date
        self.assertIn("CC BY 4.0", gauge)              # citation line
        self.assertIn("skytilsynet.no", gauge)
        kart = files["embed/kart.html"]
        self.assertIn("createElementNS", kart)
        self.assertIn("CC BY 4.0", kart)
        comp = files["embed/skytilsynet-embed.js"]
        self.assertIn("customElements.define", comp)
        self.assertIn("skytilsynet-gauge", comp)
        self.assertIn("skytilsynet-kart", comp)

    def test_embeds_have_no_external_dependency(self):
        # No external host, CDN, font, map tile or script (RFC-001 P5). The only
        # off-site URL allowed is the same-origin skytilsynet.no citation link;
        # the SVG xmlns (w3.org namespace) is a spec identifier, not a fetch.
        for text in build.embed_files(self.cats, DATA["meta"]).values():
            low = text.lower().replace("http://www.w3.org/2000/svg", "")
            for bad in ["googleapis", "cdn.", "unpkg", "jsdelivr", "cloudflare",
                        "http://", "mapbox", "leaflet", "openstreetmap"]:
                self.assertNotIn(bad, low)

    def test_colorstring_is_15_chars_of_known_classes(self):
        cs = build.cartogram_colorstring(self.cats)
        self.assertEqual(len(cs), 15)
        self.assertTrue(all(ch in "ragx" for ch in cs))

    def test_frozen_embed_pins_the_cited_figure(self):
        # The frozen iframe snippet carries the exact figure + date in its URL so a
        # published article's embed never silently changes.
        us_pct = build.combine_summaries([c["summary"] for c in self.cats])["us_pct"]
        snip = build.press_snippets_html(DATA["meta"], us_pct,
                                         build.cartogram_colorstring(self.cats))
        self.assertIn("gauge.html?pct=%s&amp;date=2026-06-28" % ("%.1f" % us_pct),
                      snip)
        # and the web-component frozen form pins it via attributes.
        self.assertIn('pct=&quot;%.1f&quot;' % us_pct, snip)

    # ---- asset writing ------------------------------------------------------
    def test_write_press_assets_emits_csv_graphics_and_embeds(self):
        import tempfile
        dest = tempfile.mkdtemp()
        build.write_press_assets(self.cats, DATA["meta"], dest)
        self.assertTrue(os.path.exists(
            os.path.join(dest, "data", "skytilsynet-kombinert.csv")))
        self.assertTrue(os.path.exists(os.path.join(dest, "graphics", "gauge.svg")))
        self.assertTrue(os.path.exists(os.path.join(dest, "graphics", "kart.svg")))
        for rel in ["embed/gauge.html", "embed/kart.html",
                    "embed/skytilsynet-embed.js"]:
            self.assertTrue(os.path.exists(os.path.join(dest, rel)), rel)

    def test_kommune_only_still_builds_the_press_page(self):
        html = build.build_html(DATA, HISTORY, TREND)
        self.assertIn("Suverenitet i tall", html)
        self.assertIn('id="view-presse"', html)


class PressGraphicsCommitted(unittest.TestCase):
    """The committed PNG press graphics exist and are real PNGs — the deploy
    rsyncs web/ verbatim, so these must be in the tree (generated by the
    dev-time make_press_graphics.py, like og-image.png)."""

    def test_png_graphics_committed(self):
        for fn in ["gauge.png", "kart.png"]:
            path = os.path.join(build.HERE, "graphics", fn)
            self.assertTrue(os.path.exists(path), fn + " not committed")
            w, h = _png_size(path)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)


class SovereigntyScore(unittest.TestCase):
    """Issue #38: a transparent per-entity sovereignty score (0-100, higher = more
    sovereign), a visible formula, a national ranking, a per-entity trend, and a
    concrete "what to change" ask. The score is Skytilsynet's PRESENTATION of the
    axes we already have (CLAUDE.md rule 3) — a fixed open weighting, never a fork
    of BetterWorld's engine."""

    def _entity(self, **kw):
        base = {"name": "X", "platform": "US_MICROSOFT", "flags": [],
                "behind_gateway": False, "governance": None, "web": None,
                "alternative": None}
        base.update(kw)
        return base

    def test_sovereign_scores_high(self):
        e = self._entity(platform="EU_SOVEREIGN",
                         governance={"score": 99}, web=None)
        s = build.sovereignty_score(e)
        self.assertGreaterEqual(s["score"], 95)
        self.assertLessEqual(s["score"], 100)

    def test_us_microsoft_scores_low(self):
        # Email axis (60 %) near zero dominates even when the US governs freely.
        e = self._entity(platform="US_MICROSOFT", governance={"score": 81})
        s = build.sovereignty_score(e)
        self.assertLess(s["score"], 40)

    def test_federated_lockin_scores_below_plain_us(self):
        # "+federated nuance": an Azure-federated tenant is deeper lock-in.
        plain = build.sovereignty_score(
            self._entity(platform="US_MICROSOFT", governance={"score": 81}))
        fed = build.sovereignty_score(
            self._entity(platform="US_MICROSOFT", governance={"score": 81},
                         flags=["federated"]))
        self.assertLess(fed["score"], plain["score"])

    def test_components_are_the_visible_formula(self):
        # Every present axis carries its weight and sub-score so the page can show
        # the open method; contributions sum to the score.
        e = self._entity(platform="EU_SOVEREIGN", governance={"score": 99},
                         web={"hosting": {"jurisdiction": "NO (EEA)"},
                              "us_resource_fraction": 0.0})
        s = build.sovereignty_score(e)
        axes = {c["axis"] for c in s["components"]}
        self.assertEqual(axes, {"email", "web", "governance"})
        for c in s["components"]:
            self.assertIn("weight", c)
            self.assertIn("sub", c)
        self.assertEqual(round(sum(c["points"] for c in s["components"])), s["score"])

    def test_missing_axes_are_dropped_not_zeroed(self):
        # No web scan and no governance rating: the score reflects ONLY the email
        # axis, not an axis silently counted as zero.
        e = self._entity(platform="OTHER", governance=None, web=None)
        s = build.sovereignty_score(e)
        self.assertEqual([c["axis"] for c in s["components"]], ["email"])
        # OTHER/uavklart email sits at the honest middle, not 0.
        self.assertEqual(s["score"], 50)

    def test_web_axis_lowers_a_us_hosted_site(self):
        host_eu = build.sovereignty_score(self._entity(
            platform="EU_SOVEREIGN", governance={"score": 99},
            web={"hosting": {"jurisdiction": "NO (EEA)"}, "us_resource_fraction": 0.0}))
        host_us = build.sovereignty_score(self._entity(
            platform="EU_SOVEREIGN", governance={"score": 99},
            web={"hosting": {"jurisdiction": "United States (CLOUD Act)"},
                 "us_resource_fraction": 1.0}))
        self.assertLess(host_us["score"], host_eu["score"])

    def test_change_ask_for_us_entity_is_concrete_and_quantified(self):
        e = self._entity(platform="US_MICROSOFT", governance={"score": 81},
                         alternative="openDesk (Open-Xchange + Nextcloud)")
        ask = build.change_ask(e)
        self.assertIn("openDesk", ask["text"])
        # It quantifies the lever: moving email to EU jurisdiction lifts the score.
        self.assertGreater(ask["potentialScore"], build.sovereignty_score(e)["score"])

    def test_change_ask_for_sovereign_entity_holds(self):
        e = self._entity(platform="EU_SOVEREIGN", governance={"score": 99})
        ask = build.change_ask(e)
        self.assertEqual(ask["potentialScore"], build.sovereignty_score(e)["score"])

    def test_national_rank_assigned_across_all_categories(self):
        cats = build.build_categories(DATA, STAT)
        ranks = {}
        for c in cats:
            for e in c["entities"]:
                self.assertIn("score", e)
                self.assertIn("nationalRank", e)
                self.assertEqual(e["nationalTotal"], 6)  # 4 kommuner + 2 organ
                ranks[e["name"]] = (e["score"], e["nationalRank"])
        # The most sovereign body ranks #1; ranks are 1..N.
        best = max(ranks.values(), key=lambda v: v[0])
        self.assertEqual(best[1], 1)
        # Vest-Lofoten (EU_SOVEREIGN) outranks Oslo (US_MICROSOFT).
        self.assertLess(ranks["Vest-Lofoten"][1], ranks["Oslo"][1])

    def test_score_and_rank_baked_into_light_data(self):
        # The home grid / league need the score without fetching a detail file.
        cats = build.build_categories(DATA, STAT)
        light = build.light_categories(cats)
        oslo = next(e for e in light[0]["entities"] if e["name"] == "Oslo")
        self.assertIn("score", oslo)
        self.assertIn("nationalRank", oslo)

    def test_detail_carries_formula_trend_and_ask(self):
        cats = build.build_categories(DATA, STAT)
        build.attach_trends(cats, _snap_fixture_dir())
        oslo = next(e for e in cats[0]["entities"] if e["name"] == "Oslo")
        self.assertIn("scoreDetail", oslo)
        self.assertTrue(oslo["scoreDetail"]["components"])
        self.assertIn("changeAsk", oslo)
        self.assertIn("trend", oslo)


def _snap_fixture_dir():
    """A temp snapshot dir with a two-point kommune series for trend tests."""
    import tempfile
    d = tempfile.mkdtemp()
    for date, plats, ver in [("2026-06-28", {"Oslo": "OTHER"}, 2),
                             ("2026-06-29", {"Oslo": "US_MICROSOFT"}, 2)]:
        with open(os.path.join(d, date + ".json"), "w") as f:
            json.dump(snap(date, plats, ver), f)
    return d


class EntityTrend(unittest.TestCase):
    """Issue #38: a per-entity platform trend so one good scan can't hide a record.
    Honest about methodology versioning (issue #24) — a reclassification across a
    version bump is a new baseline, never a real migration."""

    def _snaps(self, *points):
        return [snap(d, {"Oslo": p}, v) for d, p, v in points]

    def test_trend_collects_points_in_date_order(self):
        snaps = self._snaps(("2026-06-29", "US_MICROSOFT", 2),
                            ("2026-06-27", "OTHER", 2))
        t = build.entity_trend("Oslo", snaps)
        self.assertEqual([p["date"] for p in t], ["2026-06-27", "2026-06-29"])
        self.assertEqual([p["platform"] for p in t], ["OTHER", "US_MICROSOFT"])

    def test_trend_skips_snapshots_missing_the_entity(self):
        snaps = self._snaps(("2026-06-27", "OTHER", 2))
        snaps.append(snap("2026-06-29", {"Bergen": "US_MICROSOFT"}, 2))
        t = build.entity_trend("Oslo", snaps)
        self.assertEqual([p["date"] for p in t], ["2026-06-27"])

    def test_trend_marks_methodology_version(self):
        snaps = self._snaps(("2026-06-27", "OTHER", 1),
                            ("2026-06-29", "US_MICROSOFT", 2))
        t = build.entity_trend("Oslo", snaps)
        self.assertEqual([p["methodology_version"] for p in t], [1, 2])

    def test_attach_trends_uses_per_category_snapshot_series(self):
        cats = build.build_categories(DATA, STAT)
        build.attach_trends(cats, _snap_fixture_dir())
        oslo = next(e for e in cats[0]["entities"] if e["name"] == "Oslo")
        self.assertEqual([p["platform"] for p in oslo["trend"]],
                         ["OTHER", "US_MICROSOFT"])


class EnglishPage(unittest.TestCase):
    """Issue #44: a self-contained English entry page served at /en/ for
    international press. Same data, English copy — the verdict + gauge, the
    by-the-numbers figures, the CLOUD-Act / residency≠jurisdiction mechanism, a
    short method + the language-neutral CSV/embeds, and the load-bearing
    disclaimer in English. Plain static, no external dep."""

    def setUp(self):
        self.cats = build.build_categories(DATA, STAT, None, SEEDED)
        self.html = build.render_en_html(DATA["meta"], self.cats)

    # ---- shell + language wiring --------------------------------------------
    def test_page_declares_english_and_canonical_en_url(self):
        self.assertIn('<html lang="en"', self.html)
        self.assertIn('<link rel="canonical" href="https://skytilsynet.no/en/"',
                      self.html)

    def test_hreflang_links_both_directions(self):
        self.assertIn('hreflang="nb" href="https://skytilsynet.no/"', self.html)
        self.assertIn('hreflang="en" href="https://skytilsynet.no/en/"', self.html)

    def test_visible_language_link_back_to_norwegian(self):
        # A journalist must be able to hop back to the full Norwegian site.
        self.assertRegex(self.html, r'href="\.\./"[^>]*hreflang="nb"')
        self.assertIn("Norsk", self.html)

    def test_english_og_meta(self):
        self.assertIn('property="og:locale" content="en"', self.html)
        self.assertIn('property="og:url" content="https://skytilsynet.no/en/"',
                      self.html)
        # English, not the Norwegian description copied verbatim.
        m = re.search(r'property="og:description" content="([^"]+)"', self.html)
        self.assertIsNotNone(m)
        self.assertNotIn("kjører", m.group(1))

    # ---- verdict + gauge (English, data-driven) -----------------------------
    def test_verdict_h1_is_english_and_derived_from_the_data(self):
        m = re.search(r'id="verdict-h1">([^<]+)<', self.html)
        self.assertIsNotNone(m)
        h1 = m.group(1)
        self.assertIn("email in the USA", h1)
        c = build.combine_summaries([c["summary"] for c in self.cats])
        self.assertIn("%d of 10" % int(c["us_pct"] // 10), h1)

    def test_gauge_is_english_and_baked(self):
        self.assertIn('class="gauge"', self.html)
        self.assertIn('role="img"', self.html)
        # The Norwegian caption must not leak into the English gauge.
        self.assertNotIn("USA-kontrollert sky", self.html)
        self.assertIn("US-controlled cloud", self.html)

    def test_floor_caveat_present(self):
        self.assertIn("floor", self.html.lower())

    def test_shock_names_present_and_glossed_for_internationals(self):
        # Only bodies present AND US in the data render, each glossed in English so
        # a non-Norwegian reader gets why the name lands (Skatteetaten is US in the
        # fixture; the Tax Administration gloss travels).
        self.assertIn("hero-proof", self.html)
        self.assertIn("Skatteetaten", self.html)
        self.assertIn("Tax Administration", self.html)

    # ---- by the numbers ------------------------------------------------------
    def test_by_the_numbers_is_data_driven(self):
        figs = build.press_figures_en(self.cats)
        self.assertGreaterEqual(len(figs), 5)
        for f in figs:
            self.assertTrue(f["value"])
            self.assertTrue(f["label"])
        c = build.combine_summaries([c["summary"] for c in self.cats])
        self.assertTrue(any(build._no_pct(c["us_pct"]) in f["value"] for f in figs))

    def test_by_the_numbers_labels_are_english(self):
        blob = " ".join(f["label"] for f in build.press_figures_en(self.cats))
        self.assertNotIn("skannet", blob)
        self.assertIn("CLOUD Act", blob)

    # ---- the mechanism -------------------------------------------------------
    def test_residency_is_not_jurisdiction_keystone(self):
        low = self.html.lower()
        self.assertIn("cloud act", low)
        self.assertIn("residency", low)
        self.assertIn("jurisdiction", low)

    def test_what_good_looks_like(self):
        self.assertIn("Schleswig-Holstein", self.html)
        self.assertIn("Denmark", self.html)
        self.assertIn("Larvik", self.html)

    # ---- method + language-neutral artifacts --------------------------------
    def test_links_open_code_and_methodology(self):
        self.assertIn("github.com/praive-inc/skytilsynet", self.html)
        # The fuller methodology lives on the Norwegian site.
        self.assertIn("../#om", self.html)

    def test_reuses_neutral_csv_and_embeds(self):
        self.assertIn("../data/skytilsynet-kombinert.csv", self.html)
        self.assertIn("../embed/", self.html)

    # ---- disclaimer (load-bearing, CLAUDE.md rule 2) ------------------------
    def test_english_disclaimer_is_present_and_load_bearing(self):
        low = self.html.lower()
        self.assertIn("not a government body", low)
        self.assertIn("not affiliated", low)

    # ---- no external dependency ---------------------------------------------
    def test_no_external_serving_dependency(self):
        for bad in ("http://", "cdn.", "googleapis", "fonts.g", "unpkg", "jsdelivr"):
            self.assertNotIn(bad, self.html.replace("https://skytilsynet.no", "")
                                            .replace("https://github.com", "")
                                            .replace("https://creativecommons.org", ""))


class EnglishPageWiredIntoMain(unittest.TestCase):
    """The main Norwegian page must point to /en/ and carry hreflang, and main()
    must write web/en/index.html alongside index.html (prod has no build step)."""

    def test_main_page_has_hreflang_and_english_link(self):
        html = build.build_html(DATA, HISTORY, TREND, STAT)
        self.assertIn('hreflang="en" href="https://skytilsynet.no/en/"', html)
        self.assertRegex(html, r'href="en/"[^>]*hreflang="en"')

    def test_main_writes_en_index(self):
        import tempfile
        dest = tempfile.mkdtemp()
        build.write_en_file(self.cats, DATA["meta"], web_dir=dest)
        self.assertTrue(os.path.exists(os.path.join(dest, "en", "index.html")))

    def setUp(self):
        self.cats = build.build_categories(DATA, STAT, None, SEEDED)


# Issue #50: the saksbehandling / arkiv axis. A synthetic intake mirroring the
# CSV shape (domain -> row). Exercises each rendering branch: FOI-confirmed,
# table-inferred, and a vendor claim with no source (rule 1 → not a verdict).
SAK = {
    # Table-inferred hosting: a vendor in the table, no hosting_method → the
    # vendor→hosting table supplies an *inferred*, flagged jurisdiction.
    "oslo.kommune.no": {
        "domain": "oslo.kommune.no", "category": "kommune",
        "vendor": "Tietoevry Public 360", "vendor_method": "portal-fingerprint",
        "vendor_source": "https://example.org/oslo-360", "vendor_date": "2026-06-01",
        "hosting": "", "hosting_jurisdiction": "", "hosting_method": "",
        "hosting_source": "", "hosting_date": "", "note": ""},
    # FOI-confirmed hosting: hosting_method=innsyn-foi earns "bekreftet via innsyn".
    "baerum.kommune.no": {
        "domain": "baerum.kommune.no", "category": "kommune",
        "vendor": "Documaster", "vendor_method": "vendor-statement",
        "vendor_source": "https://example.org/baerum", "vendor_date": "2026-06-02",
        "hosting": "Documaster (Norge)", "hosting_jurisdiction": "Norge / EØS",
        "hosting_method": "innsyn-foi", "hosting_source": "https://example.org/baerum-innsyn",
        "hosting_date": "2026-06-15", "note": "Bekreftet i innsynssvar."},
    # A vendor claim with NO source → not rendered as a verdict (CLAUDE.md rule 1).
    "nykommuneilofoten.no": {
        "domain": "nykommuneilofoten.no", "category": "kommune",
        "vendor": "Acos WebSak", "vendor_method": "curated", "vendor_source": "",
        "vendor_date": "", "hosting": "", "hosting_jurisdiction": "",
        "hosting_method": "", "hosting_source": "", "hosting_date": "", "note": ""},
    # (alvdal.kommune.no deliberately absent → "ikke kartlagt ennå".)
}


class SaksbehandlingResolve(unittest.TestCase):
    """The pure resolver: a raw CSV row → the rendered saksbehandling record."""

    def test_table_inferred_hosting_is_flagged_never_confirmed(self):
        rec = build.resolve_saksbehandling(SAK["oslo.kommune.no"])
        self.assertEqual(rec["vendor"], "Tietoevry Public 360")
        self.assertEqual(rec["vendor_source"], "https://example.org/oslo-360")
        h = rec["hosting"]
        self.assertFalse(h["confirmed"])
        self.assertEqual(h["method"], "vendor-table")
        self.assertEqual(h["jurisdiction"], "United States (CLOUD Act)")
        self.assertEqual(h["confidence"], "confirmed")     # the TABLE's confidence
        self.assertIsNotNone(h["source"])                  # the table row's source

    def test_foi_confirmed_hosting_earns_bekreftet(self):
        rec = build.resolve_saksbehandling(SAK["baerum.kommune.no"])
        h = rec["hosting"]
        self.assertTrue(h["confirmed"])
        self.assertEqual(h["method"], "innsyn-foi")
        self.assertEqual(h["jurisdiction"], "Norge / EØS")
        self.assertEqual(h["source"], "https://example.org/baerum-innsyn")
        self.assertEqual(h["date"], "2026-06-15")

    def test_vendor_without_source_is_not_a_verdict(self):
        # Rule 1 is binding: a claim without a source URL is not rendered.
        self.assertIsNone(build.resolve_saksbehandling(SAK["nykommuneilofoten.no"]))

    def test_vendor_not_in_table_has_no_inferred_hosting(self):
        row = {"domain": "x", "vendor": "Ukjent Sakssystem AS",
               "vendor_source": "https://example.org/x", "vendor_date": "2026-06-01",
               "hosting_method": ""}
        rec = build.resolve_saksbehandling(row)
        self.assertEqual(rec["vendor"], "Ukjent Sakssystem AS")
        self.assertIsNone(rec["hosting"])

    def test_every_table_row_with_a_jurisdiction_carries_a_source(self):
        # Rule 1 across the whole inference table: any row that asserts a real
        # (non-"Uavklart") jurisdiction MUST carry a source URL.
        for vendor, t in build.VENDOR_HOSTING.items():
            if t["jurisdiction"] != "Uavklart":
                self.assertTrue(t.get("source"), "%s asserts a jurisdiction with no source" % vendor)


class SaksbehandlingAttach(unittest.TestCase):
    """The join onto entities + the aggregate, and that it stays out of light data."""

    def setUp(self):
        self.cats = build.build_categories(DATA, STAT, WEB, sak=SAK)
        self.files = build.detail_files(self.cats)

    def _kommune(self, name):
        arr = json.loads(self.files["detail-kommune.json"])
        return next(k for k in arr if k["name"] == name)

    def test_record_joined_by_domain(self):
        self.assertEqual(self._kommune("Oslo")["saksbehandling"]["vendor"],
                         "Tietoevry Public 360")

    def test_entity_without_row_has_none(self):
        self.assertIsNone(self._kommune("Alvdal")["saksbehandling"])

    def test_sourceless_row_yields_none(self):
        self.assertIsNone(self._kommune("Vest-Lofoten")["saksbehandling"])

    def test_axis_does_not_alter_email_verdict(self):
        oslo = self._kommune("Oslo")
        self.assertEqual(oslo["platform"], "US_MICROSOFT")     # unchanged
        self.assertEqual(oslo["verdict"]["platform"], "US_MICROSOFT")

    def test_saksbehandling_not_in_light_data(self):
        # Heavy evidence rides only in the detail files, never inline (issue #34).
        light = build.light_categories(self.cats)
        oslo = next(e for c in light for e in c["entities"] if e["name"] == "Oslo")
        self.assertNotIn("saksbehandling", oslo)

    def test_aggregate_counts_mapped_and_confirmed(self):
        agg = build.sak_aggregate(self.cats)
        # Oslo (table-inferred) + Bærum (FOI) are mapped; Vest-Lofoten sourceless,
        # Alvdal + the 2 stat organ absent. 6 entities total.
        self.assertEqual(agg["total"], 6)
        self.assertEqual(agg["mapped"], 2)
        self.assertEqual(agg["confirmed"], 1)                  # only Bærum via innsyn


class SaksbehandlingRender(unittest.TestCase):
    """The per-entity block and the aggregate line, in the built page + detail JS."""

    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT, WEB, sak=SAK)

    def test_axis_has_its_own_block_and_intake_cta(self):
        self.assertIn("k.saksbehandling", self.html)           # detail iterates it
        self.assertIn("Saksbehandling / arkiv", self.html)     # its own heading
        self.assertIn("Ikke kartlagt ennå", self.html)         # unmapped empty state
        self.assertIn("Krev innsyn", self.html)                # intake CTA

    def test_render_distinguishes_confirmed_from_inferred(self):
        self.assertIn("bekreftet via innsyn", self.html)       # FOI path
        self.assertIn("utledet fra leverandør", self.html)     # table path

    def test_aggregate_line_present_and_derived(self):
        agg = build.sak_aggregate(build.build_categories(DATA, STAT, WEB, sak=SAK))
        self.assertIn("Saksarkiv kartlagt for %d av %d" % (agg["mapped"], agg["total"]),
                      self.html)
        self.assertIn("%d med hosting bekreftet via innsyn" % agg["confirmed"], self.html)

    def test_builds_without_sak_data(self):
        # Backward compatible: the saksbehandling intake is optional.
        html = build.build_html(DATA, HISTORY, TREND, STAT)
        self.assertIn("Oslo", html)
        cats = build.build_categories(DATA, STAT)
        oslo = next(e for e in cats[0]["entities"] if e["name"] == "Oslo")
        self.assertIsNone(oslo["saksbehandling"])


class SaksbehandlingSeed(unittest.TestCase):
    """The committed seed CSV: citable rows only, parsed by the loader."""

    def test_seed_csv_loads_and_has_larvik_acos_row(self):
        idx = build.load_saksbehandling()
        self.assertIn("larvik.kommune.no", idx)
        row = idx["larvik.kommune.no"]
        self.assertEqual(row["vendor"], "Acos WebSak")
        self.assertTrue(row["vendor_source"].startswith("http"))
        self.assertTrue(row["vendor_date"])

    def test_real_build_surfaces_larvik_saksarkiv(self):
        idx = build.load_saksbehandling()
        data = json.load(open(build.DATA))
        cats = build.build_categories(data, sak=idx)
        larvik = next(e for e in cats[0]["entities"]
                      if e.get("domain") == "larvik.kommune.no")
        self.assertEqual(larvik["saksbehandling"]["vendor"], "Acos WebSak")
        # Acos hosting is customer-choosable → table says Uavklart, never a verdict.
        self.assertEqual(larvik["saksbehandling"]["hosting"]["jurisdiction"], "Uavklart")


if __name__ == "__main__":
    unittest.main()
