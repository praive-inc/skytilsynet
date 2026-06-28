#!/usr/bin/env python3
"""
Tests for the kommune email-sovereignty scanner.

The DNS-dependent parts take an injectable resolver (`fetch`/`dig`) so the whole
pipeline is exercised offline against the actual seams — no network, fully
deterministic. Run:  python3 -m unittest -v
"""
import unittest
import scan


def ev(mx=None, spf="", auto=""):
    """Build the evidence dict classify_evidence/make_record consume."""
    mx = mx or []
    mx_hosts = " ".join(scan.re.sub(r"^\d+\s+", "", m) for m in mx)
    return {"mx": mx, "mx_hosts": mx_hosts.strip(), "spf": spf, "autodiscover": auto}


class ClassifyEvidence(unittest.TestCase):
    def test_microsoft_via_mx(self):
        e = ev(mx=["0 kvam-kommune-no.mail.protection.outlook.com"])
        self.assertEqual(scan.classify_evidence(e), ("US_MICROSOFT", "mx/spf"))

    def test_microsoft_new_mx_microsoft_format(self):
        # The newer "*.mx.microsoft" MX host must classify as Microsoft.
        e = ev(mx=["0 kvam-kommune-no.h-v1.mx.microsoft"])
        self.assertEqual(scan.classify_evidence(e)[0], "US_MICROSOFT")

    def test_microsoft_unmasked_behind_gateway_via_spf(self):
        # Vanity MX (Trend Micro gateway) but SPF still declares Microsoft.
        e = ev(mx=["10 vtds.in.tmes.trendmicro.eu"],
               spf="v=spf1 include:spf.protection.outlook.com -all")
        self.assertEqual(scan.classify_evidence(e), ("US_MICROSOFT", "mx/spf"))

    def test_microsoft_via_autodiscover_only(self):
        e = ev(auto="autodiscover.outlook.com")
        self.assertEqual(scan.classify_evidence(e), ("US_MICROSOFT", "autodiscover"))

    def test_google(self):
        e = ev(mx=["1 aspmx.l.google.com"])
        self.assertEqual(scan.classify_evidence(e)[0], "US_GOOGLE")

    def test_eu_sovereign(self):
        e = ev(mx=["10 mx.domeneshop.no"])
        self.assertEqual(scan.classify_evidence(e)[0], "EU_SOVEREIGN")

    def test_other_regional(self):
        e = ev(mx=["10 mail1.hedmark-ikt.no"])
        self.assertEqual(scan.classify_evidence(e)[0], "OTHER")

    def test_none_when_no_signal(self):
        self.assertEqual(scan.classify_evidence(ev())[0], "NONE")

    def test_null_spf_only_is_none(self):
        # A bare "v=spf1 -all" null-sending record means mail is elsewhere.
        self.assertEqual(scan.classify_evidence(ev(spf="v=spf1 -all"))[0], "NONE")


class Candidates(unittest.TestCase):
    def test_slugify_norwegian_letters(self):
        self.assertEqual(scan.slugify("Hå kommune"), "ha")
        self.assertEqual(scan.slugify("Øygarden"), "oygarden")
        self.assertEqual(scan.slugify("Aurskog-Høland"), "aurskog-holand")

    def test_website_first_then_slug_kommune_no(self):
        c = scan.candidates("Hå", "ha.no", {})
        self.assertEqual(c[0], "ha.no")
        self.assertIn("ha.kommune.no", c)

    def test_parent_walk_skips_category_domains(self):
        # Stripping labels must never probe the shared "kommune.no" apex.
        c = scan.candidates("Kautokeino", "guovdageainnu.suohkan.no", {})
        self.assertNotIn("kommune.no", c)
        self.assertNotIn("no", c)
        self.assertIn("kautokeino.kommune.no", c)

    def test_override_wins_outright(self):
        c = scan.candidates("Aurskog-Høland", "aurskog-holand.kommune.no",
                            {"Aurskog-Høland": "ahk.no"})
        self.assertEqual(c, ["ahk.no"])


class Resolve(unittest.TestCase):
    def test_falls_back_to_mail_domain_when_website_has_no_mail(self):
        # Website domain is a null-SPF dead end; real mail lives on slug.kommune.no.
        responses = {
            "ha.no": ev(spf="v=spf1 -all"),
            "ha.kommune.no": ev(mx=["10 mail.ha.kommune.no"],
                                spf="v=spf1 include:spf.protection.outlook.com -all"),
        }
        fake = lambda d: responses.get(d, ev())
        rec = scan.resolve("Hå", "ha.no", {}, "2026-06-28", fetch=fake)
        self.assertEqual(rec["domain"], "ha.kommune.no")
        self.assertEqual(rec["platform"], "US_MICROSOFT")
        self.assertIn("mail_domain_differs_from_website", rec["flags"])

    def test_keeps_website_domain_when_it_resolves(self):
        responses = {"x.kommune.no": ev(mx=["0 x.mail.protection.outlook.com"])}
        rec = scan.resolve("X", "x.kommune.no", {}, "2026-06-28",
                           fetch=lambda d: responses.get(d, ev()))
        self.assertEqual(rec["domain"], "x.kommune.no")
        self.assertNotIn("mail_domain_differs_from_website", rec["flags"])


class Record(unittest.TestCase):
    def test_carries_evidence_and_sourcedate(self):
        e = ev(mx=["0 x.mail.protection.outlook.com"],
               spf="v=spf1 include:spf.protection.outlook.com -all",
               auto="autodiscover.outlook.com")
        rec = scan.make_record("X", "x.kommune.no", "x.kommune.no", e, "2026-06-28")
        self.assertEqual(rec["sourceDate"], "2026-06-28")
        self.assertEqual(rec["evidence"]["mx"], ["0 x.mail.protection.outlook.com"])
        self.assertEqual(rec["evidence"]["spf"],
                         "v=spf1 include:spf.protection.outlook.com -all")
        self.assertEqual(rec["evidence"]["autodiscover"], "autodiscover.outlook.com")
        self.assertEqual(rec["jurisdiction"], "United States (CLOUD Act)")
        self.assertTrue(rec["alternative"])  # a European switch target is offered

    def test_gateway_fronted_other_flags_unmasked_backend(self):
        # Trend Micro gateway, backend not revealed by SPF -> floor flag.
        e = ev(mx=["10 vtds.in.tmes.trendmicro.eu"], spf="v=spf1 -all")
        rec = scan.make_record("Vinje", "vinje.kommune.no", "vinje.kommune.no",
                               e, "2026-06-28")
        self.assertEqual(rec["platform"], "OTHER")
        self.assertTrue(rec["behind_gateway"])
        self.assertIn("backend_unmasked", rec["flags"])

    def test_eu_owned_provider_has_no_washing_flag(self):
        e = ev(mx=["10 mx.domeneshop.no"])
        rec = scan.make_record("Vest-Lofoten", "x.no", "x.no", e, "2026-06-28")
        self.assertEqual(rec["jurisdiction"], "Norway (EEA)")
        self.assertEqual(rec["flags"], [])

    def test_non_eu_provider_jurisdiction_is_flagged(self):
        # EU-located != EU-owned: Proton (Switzerland) is outside EU jurisdiction.
        e = ev(mx=["10 mail.proton.me"])
        rec = scan.make_record("Demo", "x.no", "x.no", e, "2026-06-28")
        self.assertEqual(rec["jurisdiction"], "Switzerland (non-EU)")
        self.assertIn("non_eu_jurisdiction", rec["flags"])


class Aggregate(unittest.TestCase):
    def test_floor_is_stated_and_unmasked_counted(self):
        recs = [
            {"platform": "US_MICROSOFT", "flags": []},
            {"platform": "US_MICROSOFT", "flags": []},
            {"platform": "OTHER", "flags": ["backend_unmasked"]},
            {"platform": "EU_SOVEREIGN", "flags": []},
        ]
        agg = scan.aggregate(recs)
        self.assertEqual(agg["total"], 4)
        self.assertEqual(agg["backend_unmasked"], 1)
        self.assertEqual(agg["microsoft_pct"], 50.0)
        self.assertIn("floor", agg["floor_note"].lower())


if __name__ == "__main__":
    unittest.main()
