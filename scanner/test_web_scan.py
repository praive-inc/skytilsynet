#!/usr/bin/env python3
"""
Tests for the kommune web-infrastructure sovereignty scanner (second axis).

Like test_scan.py, every network-dependent function takes an injectable seam
(`http_get` / `dig` / `tls`) so the whole pipeline runs offline against the real
seams — deterministic, no network. Run:  python3 -m unittest -v
"""
import unittest
import web_scan as ws


def dig_from(table):
    """Fake dig: (name, rtype) -> list of records, else []."""
    return lambda name, rtype: table.get((name, rtype), [])


class HostOf(unittest.TestCase):
    def test_absolute_url(self):
        self.assertEqual(ws.host_of("https://www.example.com/a/b?x=1"), "www.example.com")

    def test_protocol_relative(self):
        self.assertEqual(ws.host_of("//cdn.example.com/x.js"), "cdn.example.com")

    def test_relative_url_has_no_host(self):
        self.assertIsNone(ws.host_of("/local/app.js"))
        self.assertIsNone(ws.host_of("app.css"))

    def test_data_uri_has_no_host(self):
        self.assertIsNone(ws.host_of("data:image/png;base64,AAAA"))

    def test_strips_port_and_userinfo(self):
        self.assertEqual(ws.host_of("https://u@host.test:8443/x"), "host.test")


class ThirdPartyDomains(unittest.TestCase):
    HTML = """
      <link href="/local/site.css" rel="stylesheet">
      <link href="https://fonts.googleapis.com/css?family=Roboto" rel="stylesheet">
      <script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
      <script src="//cdn.jsdelivr.net/npm/x.js"></script>
      <img src="https://static.hemsedal.kommune.no/logo.png">
      <iframe src="https://www.youtube.com/embed/abc"></iframe>
      <img src="data:image/gif;base64,AAAA">
    """

    def test_collects_distinct_external_hosts_only(self):
        got = ws.third_party_domains(self.HTML, "www.hemsedal.kommune.no")
        self.assertIn("fonts.googleapis.com", got)
        self.assertIn("www.googletagmanager.com", got)
        self.assertIn("cdn.jsdelivr.net", got)
        self.assertIn("www.youtube.com", got)

    def test_same_site_subdomain_is_internal(self):
        got = ws.third_party_domains(self.HTML, "www.hemsedal.kommune.no")
        self.assertNotIn("static.hemsedal.kommune.no", got)

    def test_another_kommune_is_external_not_collapsed_to_apex(self):
        html = '<script src="https://www.oslo.kommune.no/x.js"></script>'
        got = ws.third_party_domains(html, "www.hemsedal.kommune.no")
        self.assertEqual(got, ["www.oslo.kommune.no"])

    def test_no_external_resources(self):
        self.assertEqual(ws.third_party_domains('<a href="/about">a</a>', "x.no"), [])


class ClassifyResource(unittest.TestCase):
    def test_google_analytics_is_us_analytics(self):
        cat, jur, flags = ws.classify_resource("www.google-analytics.com")
        self.assertEqual(jur, ws.US_JURISDICTION)
        self.assertIn("analytics", flags)

    def test_google_fonts_is_us(self):
        self.assertEqual(ws.classify_resource("fonts.googleapis.com")[1], ws.US_JURISDICTION)

    def test_specific_subdomain_wins_over_generic_googleapis(self):
        # fonts.googleapis.com must match the fonts entry, not the generic api one.
        self.assertEqual(ws.classify_resource("fonts.googleapis.com")[0], "fonts")
        self.assertEqual(ws.classify_resource("ajax.googleapis.com")[0], "cdn")

    def test_us_cdn(self):
        self.assertEqual(ws.classify_resource("d111.cloudfront.net")[1], ws.US_JURISDICTION)
        self.assertEqual(ws.classify_resource("cdnjs.cloudflare.com")[1], ws.US_JURISDICTION)

    def test_facebook_tracker_flag(self):
        self.assertIn("tracker", ws.classify_resource("connect.facebook.net")[2])

    def test_unknown_domain_is_undetermined(self):
        cat, jur, flags = ws.classify_resource("some.random-eu-host.de")
        self.assertEqual(cat, "other")
        self.assertEqual(jur, "Undetermined")
        self.assertEqual(flags, [])


class CountryJurisdiction(unittest.TestCase):
    def test_us_is_cloud_act(self):
        self.assertEqual(ws.country_jurisdiction("US"), ws.US_JURISDICTION)

    def test_eu_member(self):
        self.assertEqual(ws.country_jurisdiction("de"), "DE (EU)")

    def test_norway_is_eea(self):
        self.assertEqual(ws.country_jurisdiction("NO"), "NO (EEA)")

    def test_non_eu_europe(self):
        self.assertEqual(ws.country_jurisdiction("GB"), "United Kingdom (non-EU)")
        self.assertEqual(ws.country_jurisdiction("CH"), "Switzerland (non-EU)")

    def test_missing_is_undetermined(self):
        self.assertEqual(ws.country_jurisdiction(None), "Undetermined")


class AsnLookup(unittest.TestCase):
    def test_parses_cymru_origin_and_name(self):
        dig = dig_from({
            ("4.4.8.8.origin.asn.cymru.com", "TXT"):
                ['"15169 | 8.8.8.0/24 | us | arin | 1992-12-01"'],
            ("as15169.asn.cymru.com", "TXT"):
                ['"15169 | us | arin | 2000-03-30 | google, us"'],
        })
        info = ws.asn_lookup("8.8.4.4", dig=dig)
        self.assertEqual(info["asn"], "15169")
        self.assertEqual(info["country"], "US")
        self.assertIn("google", info["name"])

    def test_no_record_returns_empty(self):
        info = ws.asn_lookup("10.0.0.1", dig=dig_from({}))
        self.assertIsNone(info["asn"])
        self.assertIsNone(info["country"])


class BuildRecord(unittest.TestCase):
    def _html(self):
        return ('<script src="https://www.google-analytics.com/ga.js"></script>'
                '<link href="https://fonts.googleapis.com/css">'
                '<script src="//cdn.jsdelivr.net/x.js"></script>')

    def test_derives_signal_jurisdictions_and_flags(self):
        hosting = {"ip": "13.1.1.1", "asn": "8075", "country": "US",
                   "name": "microsoft", "jurisdiction": ws.US_JURISDICTION}
        rec = ws.build_record(
            "Demo", "https://www.demo.no/",
            {"server": "Microsoft-IIS/10.0", "x-powered-by": "ASP.NET", "csp": None},
            self._html(), hosting, "Let's Encrypt", True, "2026-06-28")
        self.assertEqual(rec["axis"], "web")
        self.assertEqual(rec["sourceDate"], "2026-06-28")
        self.assertEqual(rec["hosting"]["jurisdiction"], ws.US_JURISDICTION)
        # each third party carries a derived jurisdiction
        jurs = {t["domain"]: t["jurisdiction"] for t in rec["third_parties"]}
        self.assertEqual(jurs["www.google-analytics.com"], ws.US_JURISDICTION)
        self.assertEqual(jurs["fonts.googleapis.com"], ws.US_JURISDICTION)
        # signals
        self.assertTrue(rec["analytics"])
        self.assertIn("us_hosted", rec["flags"])
        self.assertIn("analytics", rec["flags"])
        self.assertEqual(rec["evidence"]["server"], "Microsoft-IIS/10.0")
        self.assertEqual(rec["evidence"]["security_txt"], True)
        self.assertEqual(rec["evidence"]["tls_issuer"], "Let's Encrypt")

    def test_us_resource_fraction(self):
        hosting = {"ip": None, "asn": None, "country": None, "name": None,
                   "jurisdiction": "Undetermined"}
        # 2 of 3 external resources are US (jsdelivr is Undetermined).
        rec = ws.build_record("Demo", "https://x.no/", {}, self._html(),
                              hosting, None, False, "2026-06-28")
        self.assertAlmostEqual(rec["us_resource_fraction"], round(2 / 3, 3))
        self.assertNotIn("us_hosted", rec["flags"])

    def test_no_third_parties_fraction_is_zero(self):
        hosting = {"jurisdiction": "NO (EEA)"}
        rec = ws.build_record("Clean", "https://clean.no/", {},
                              '<a href="/x">x</a>', hosting, None, True, "2026-06-28")
        self.assertEqual(rec["us_resource_fraction"], 0.0)
        self.assertEqual(rec["third_parties"], [])
        self.assertFalse(rec["analytics"])


class ScanOne(unittest.TestCase):
    def test_end_to_end_with_injected_seams(self):
        html = ('<script src="https://www.googletagmanager.com/gtag/js"></script>'
                '<link href="/local.css">')
        http = {
            "https://www.demo.kommune.no/": (200, {"server": "nginx"}, html),
            "https://www.demo.kommune.no/.well-known/security.txt": (404, {}, ""),
        }
        dig = dig_from({
            ("www.demo.kommune.no", "A"): ["13.107.21.200"],
            ("200.21.107.13.origin.asn.cymru.com", "TXT"):
                ['"8075 | 13.107.0.0/16 | us | arin | 2015-01-01"'],
            ("as8075.asn.cymru.com", "TXT"):
                ['"8075 | us | arin | 1997-01-01 | microsoft-corp, us"'],
        })
        rec = ws.scan_one("Demo", "https://www.demo.kommune.no/", "2026-06-28",
                          http_get=lambda u: http.get(u, (None, {}, "")),
                          dig=dig, tls=lambda h: "DigiCert")
        self.assertEqual(rec["hosting"]["country"], "US")
        self.assertEqual(rec["hosting"]["jurisdiction"], ws.US_JURISDICTION)
        self.assertIn("us_hosted", rec["flags"])
        self.assertTrue(rec["analytics"])
        self.assertEqual(rec["evidence"]["security_txt"], False)
        self.assertEqual(rec["evidence"]["tls_issuer"], "DigiCert")
        self.assertEqual([t["domain"] for t in rec["third_parties"]],
                         ["www.googletagmanager.com"])

    def test_unreachable_homepage_records_no_signal(self):
        rec = ws.scan_one("Down", "https://down.no/", "2026-06-28",
                          http_get=lambda u: (None, {}, ""),
                          dig=dig_from({}), tls=lambda h: None)
        self.assertEqual(rec["third_parties"], [])
        self.assertEqual(rec["us_resource_fraction"], 0.0)
        self.assertIn("unreachable", rec["flags"])


class JoinDomain(unittest.TestCase):
    def test_record_carries_apex_join_domain(self):
        # Each record carries a www-stripped apex `domain` so build.py can join the
        # web axis onto an entity by its website domain (== email's website_domain).
        rec = ws.build_record("Demo", "https://www.demo.kommune.no/", {},
                              '<a href="/x">x</a>', {"jurisdiction": "NO (EEA)"},
                              None, False, "2026-06-28")
        self.assertEqual(rec["domain"], "demo.kommune.no")


class ScanEntitySafe(unittest.TestCase):
    """main() runs 358 homepages concurrently; one entity blowing up must not abort
    the whole run. _scan_entity turns an unexpected error into a flagged record."""

    def test_normal_scan_passes_through(self):
        rec = ws._scan_entity("Ok", "https://ok.no/", "2026-06-28",
                              scan=lambda n, u, d: {"kommune": n, "flags": []})
        self.assertEqual(rec["kommune"], "Ok")
        self.assertNotIn("scan_error", rec["flags"])

    def test_exception_becomes_flagged_record_not_a_crash(self):
        def boom(name, url, date):
            raise RuntimeError("dns blew up")
        rec = ws._scan_entity("Demo", "https://www.demo.no/", "2026-06-28", scan=boom)
        self.assertEqual(rec["kommune"], "Demo")
        self.assertIn("scan_error", rec["flags"])
        self.assertEqual(rec["domain"], "demo.no")
        self.assertEqual(rec["us_resource_fraction"], 0.0)
        self.assertEqual(rec["third_parties"], [])


class Aggregate(unittest.TestCase):
    def test_counts_and_average_fraction(self):
        recs = [
            {"flags": ["us_hosted", "analytics"], "us_resource_fraction": 1.0,
             "analytics": True},
            {"flags": ["us_hosted"], "us_resource_fraction": 0.5, "analytics": False},
            {"flags": [], "us_resource_fraction": 0.0, "analytics": False},
        ]
        agg = ws.aggregate(recs)
        self.assertEqual(agg["total"], 3)
        self.assertEqual(agg["us_hosted"], 2)
        self.assertEqual(agg["analytics"], 1)
        self.assertAlmostEqual(agg["avg_us_resource_fraction"], 0.5)


if __name__ == "__main__":
    unittest.main()
