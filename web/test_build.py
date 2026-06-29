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

    def _payload(self):
        start = self.html.index('id="data"')
        open_tag = self.html.index(">", start) + 1
        close = self.html.index("</script>", open_tag)
        return json.loads(self.html[open_tag:close].replace("<\\/", "</"))

    def _kommune(self, name):
        cat = next(c for c in self._payload()["categories"] if c["key"] == "kommune")
        return next(k for k in cat["entities"] if k["kommune"] == name)

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
        payload_start = html.index('id="data"')
        open_tag = html.index(">", payload_start) + 1
        close = html.index("</script>", open_tag)
        payload = json.loads(html[open_tag:close].replace("<\\/", "</"))
        cat = next(c for c in payload["categories"] if c["key"] == "kommune")
        self.assertIsNone(next(k for k in cat["entities"] if k["kommune"] == "Oslo")["web"])


class BuildHtml(unittest.TestCase):
    def setUp(self):
        self.html = build.build_html(DATA, HISTORY, TREND, STAT)

    def _payload(self):
        start = self.html.index('id="data"')
        open_tag = self.html.index(">", start) + 1
        close = self.html.index("</script>", open_tag)
        return json.loads(self.html[open_tag:close].replace("<\\/", "</"))

    def test_disclaimer_present(self):
        # CLAUDE.md rule 2 — load-bearing, must be in the document.
        self.assertIn("ikke et offentlig organ", self.html)
        self.assertIn("not a government body", self.html)

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
        # Acceptance: every verdict still links to cited evidence.
        payload = self._payload()
        for c in payload["categories"]:
            for e in c["entities"]:
                self.assertIn("evidence", e)
                self.assertTrue(e["sourceDate"])

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
        start = self.html.index('id="data"')
        open_tag = self.html.index(">", start) + 1
        close = self.html.index("</script>", open_tag)
        payload = json.loads(self.html[open_tag:close].replace("<\\/", "</"))
        kommune_cat = next(c for c in payload["categories"] if c["key"] == "kommune")
        by_name = {k["kommune"]: k for k in kommune_cat["entities"]}
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

    def test_new_baseline_trend_renders_honest_copy(self):
        # Issue #24: at a methodology change the card says "ny baseline", not a
        # movement count. Bake a cross-version trend and assert the copy is wired.
        baseline = {"new_baseline": True, "baseline_date": "2026-06-28",
                    "methodology_version": 2}
        html = build.build_html(DATA, HISTORY, baseline, STAT)
        self.assertIn('"new_baseline":true', html)
        self.assertIn("ny baseline", html)
        self.assertIn("Metodikk forbedret", html)

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


if __name__ == "__main__":
    unittest.main()
