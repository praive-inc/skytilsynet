#!/usr/bin/env python3
"""
Tests for the sakarkiv innsyn-portal fingerprint probe (issue #61).

Like the other scanner tests, every network-dependent function takes an
injectable seam (`fetch` / `can_fetch`) so the whole pipeline runs offline —
deterministic, no network. Run:  python3 -m unittest -v
"""
import unittest
import saksarkiv_probe as sp


class Fingerprint(unittest.TestCase):
    def test_known_portal_hosts_map_to_full_vendor_names(self):
        self.assertEqual(sp.fingerprint("innsynpluss.onacos.no"), "Acos WebSak")
        self.assertEqual(sp.fingerprint("oygarden.acossky.no"), "Acos WebSak")
        self.assertEqual(sp.fingerprint("prod02.elementscloud.no"), "Sikri Elements")
        self.assertEqual(sp.fingerprint("opengov.360online.com"), "Tietoevry Public 360")
        self.assertEqual(sp.fingerprint("innsyn.public.cloudservices.no"),
                         "Tietoevry Public 360")
        self.assertEqual(sp.fingerprint("ephinnsyn.example.no"), "ePhorte (legacy)")
        self.assertEqual(sp.fingerprint("ephorte.example.no"), "ePhorte (legacy)")

    def test_case_insensitive(self):
        self.assertEqual(sp.fingerprint("Innsyn.OnAcos.NO"), "Acos WebSak")

    def test_unknown_host_is_none(self):
        self.assertIsNone(sp.fingerprint("www.google-analytics.com"))
        self.assertIsNone(sp.fingerprint(""))
        self.assertIsNone(sp.fingerprint(None))


class LinkHosts(unittest.TestCase):
    HTML = """
      <a href="/lokalt">lokal</a>
      <a href="https://innsyn.onacos.no/postliste">Postliste</a>
      <script src="//www.googletagmanager.com/gtag/js"></script>
      <img src="data:image/gif;base64,AAAA">
    """

    def test_extracts_external_link_hosts(self):
        got = sp.link_hosts(self.HTML)
        self.assertIn("innsyn.onacos.no", got)
        self.assertIn("www.googletagmanager.com", got)

    def test_ignores_relative_and_data_uris(self):
        self.assertNotIn(None, sp.link_hosts(self.HTML))


class MineWebAxis(unittest.TestCase):
    """The zero-cost first pass: fingerprint the ALREADY-collected web-axis
    third_parties without a single new request."""
    RECORDS = [
        {"kommune": "Øygarden", "domain": "oygarden.kommune.no",
         "url": "https://www.oygarden.kommune.no",
         "third_parties": [{"domain": "www.facebook.com"},
                           {"domain": "oygarden.acossky.no"}]},
        {"kommune": "Oslo", "domain": "oslo.kommune.no",
         "url": "https://www.oslo.kommune.no",
         "third_parties": [{"domain": "opengov.360online.com"}]},
        {"kommune": "Ukjent", "domain": "ukjent.kommune.no",
         "url": "https://www.ukjent.kommune.no",
         "third_parties": [{"domain": "www.youtube.com"}]},
    ]

    def test_fingerprints_hosts_already_in_web_axis(self):
        recs = sp.mine_web_axis(self.RECORDS, "2026-07-02")
        by_dom = {r["domain"]: r for r in recs}
        self.assertEqual(by_dom["oygarden.kommune.no"]["vendor"], "Acos WebSak")
        self.assertEqual(by_dom["oslo.kommune.no"]["vendor"], "Tietoevry Public 360")

    def test_no_fingerprint_yields_no_record(self):
        recs = sp.mine_web_axis(self.RECORDS, "2026-07-02")
        self.assertNotIn("ukjent.kommune.no", {r["domain"] for r in recs})

    def test_record_carries_portal_method_source_and_date(self):
        rec = sp.mine_web_axis(self.RECORDS, "2026-07-02")[0]
        self.assertEqual(rec["vendor_method"], "portal-fingerprint")
        self.assertEqual(rec["vendor_date"], "2026-07-02")
        self.assertIn("acossky.no", rec["vendor_source"])
        self.assertTrue(rec["vendor_source"].startswith("http"))


def fetch_from(table):
    """Fake fetch: url -> (status, html), else (404, '')."""
    return lambda url: table.get(url, (404, ""))


class ProbeEntity(unittest.TestCase):
    def test_finds_portal_on_innsyn_path(self):
        fetch = fetch_from({
            "https://www.a.kommune.no/innsyn":
                (200, '<a href="https://innsyn.acossky.no/x">Postliste</a>')})
        rec = sp.probe_entity("A", "a.kommune.no", "https://www.a.kommune.no",
                              fetch, date="2026-07-02")
        self.assertEqual(rec["vendor"], "Acos WebSak")
        self.assertEqual(rec["vendor_method"], "portal-fingerprint")
        self.assertTrue(rec["vendor_source"].startswith("http"))

    def test_backs_off_on_403_and_probes_no_further(self):
        # A 403 (the Apr-2026 vendor bot-block risk) must stop the probe for this
        # entity — even if a LATER path would have matched, we don't hammer.
        calls = []

        def fetch(url):
            calls.append(url)
            return (403, "")
        rec = sp.probe_entity("A", "a.kommune.no", "https://www.a.kommune.no",
                              fetch, paths=["/innsyn", "/postliste"],
                              date="2026-07-02")
        self.assertIsNone(rec)
        self.assertEqual(len(calls), 1)   # stopped after the first 403

    def test_honors_robots_disallow(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return (200, '<a href="https://innsyn.acossky.no/x">P</a>')
        rec = sp.probe_entity("A", "a.kommune.no", "https://www.a.kommune.no",
                              fetch, can_fetch=lambda u: False, date="2026-07-02")
        self.assertIsNone(rec)
        self.assertEqual(fetched, [])     # never fetched a disallowed path

    def test_no_portal_found_returns_none(self):
        fetch = fetch_from({
            "https://www.a.kommune.no/innsyn": (200, "<a href='/local'>x</a>")})
        self.assertIsNone(sp.probe_entity(
            "A", "a.kommune.no", "https://www.a.kommune.no", fetch,
            date="2026-07-02"))


class ProbeAll(unittest.TestCase):
    RECORDS = [
        {"kommune": "A", "domain": "a.kommune.no", "url": "https://www.a.kommune.no",
         "third_parties": []},
        {"kommune": "B", "domain": "b.kommune.no", "url": "https://www.b.kommune.no",
         "third_parties": []},
    ]

    def test_skips_already_covered_domains(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return (404, "")
        sp.probe_all(self.RECORDS, covered={"a.kommune.no"}, fetch=fetch,
                     cache={}, date="2026-07-02", sleep=lambda s: None)
        self.assertFalse(any("a.kommune.no" in u for u in fetched))

    def test_uses_cache_to_avoid_refetching(self):
        cache = {"b.kommune.no": {"date": "2026-07-02", "vendor": "Sikri Elements",
                                  "vendor_host": "prod.elementscloud.no"}}
        fetched = []

        def fetch(url):
            fetched.append(url)
            return (404, "")
        recs = sp.probe_all(self.RECORDS, covered={"a.kommune.no"}, fetch=fetch,
                            cache=cache, date="2026-07-02", sleep=lambda s: None)
        self.assertEqual(fetched, [])     # cache hit → no network
        self.assertEqual(recs[0]["vendor"], "Sikri Elements")

    def test_rate_limits_between_entities(self):
        slept = []
        sp.probe_all(self.RECORDS, covered=set(), fetch=fetch_from({}),
                     cache={}, date="2026-07-02", sleep=lambda s: slept.append(s))
        self.assertTrue(slept)            # a delay was applied


class BuildDataset(unittest.TestCase):
    RECORDS = [
        {"kommune": "Øygarden", "domain": "oygarden.kommune.no",
         "url": "https://www.oygarden.kommune.no",
         "third_parties": [{"domain": "oygarden.acossky.no"}]},
        {"kommune": "Ukjent", "domain": "ukjent.kommune.no",
         "url": "https://www.ukjent.kommune.no",
         "third_parties": [{"domain": "www.youtube.com"}]},
    ]

    def test_dataset_has_meta_records_and_coverage(self):
        ds = sp.build_dataset(self.RECORDS, "2026-07-02")
        self.assertEqual(ds["meta"]["method"], "portal-fingerprint")
        self.assertEqual(ds["meta"]["license"], "CC BY 4.0")
        self.assertEqual(ds["meta"]["coverage"]["fingerprinted"], 1)
        self.assertEqual(ds["meta"]["coverage"]["total"], 2)
        self.assertEqual(len(ds["records"]), 1)

    def test_never_asserts_a_hosting_jurisdiction(self):
        # A portal fingerprint identifies the VENDOR only — the auto dataset must
        # carry no hosting/jurisdiction claim (issue #61 §3).
        ds = sp.build_dataset(self.RECORDS, "2026-07-02")
        rec = ds["records"][0]
        self.assertNotIn("hosting", rec)
        self.assertNotIn("hosting_jurisdiction", rec)


if __name__ == "__main__":
    unittest.main()
