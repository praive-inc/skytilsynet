#!/usr/bin/env python3
"""
Tests for the kommune email-sovereignty scanner.

The DNS-dependent parts take an injectable resolver (`fetch`/`dig`) so the whole
pipeline is exercised offline against the actual seams — no network, fully
deterministic. Run:  python3 -m unittest -v
"""
import unittest
import scan


def ev(mx=None, spf="", auto="", dkim="", dkim_google=False, realm=None):
    """Build the evidence dict classify_evidence/make_record consume."""
    mx = mx or []
    mx_hosts = " ".join(scan.re.sub(r"^\d+\s+", "", m) for m in mx)
    return {"mx": mx, "mx_hosts": mx_hosts.strip(), "spf": spf, "autodiscover": auto,
            "dkim": dkim, "dkim_google": dkim_google, "realm": realm}


class FakeResp:
    """Minimal context-manager HTTP response for getuserrealm tests."""
    def __init__(self, body): self._b = body.encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def opener_returning(body):
    return lambda url, timeout=None: FakeResp(body)


def dig_from(table):
    """Fake dig: (name, rtype) -> list of records, else []."""
    return lambda name, rtype: table.get((name, rtype), [])


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


class DeepSignals(unittest.TestCase):
    """Gateway/co-op unmasking via DKIM, SPF-IP and the Azure AD realm."""

    def test_realm_managed_is_hard_microsoft(self):
        # getuserrealm=Managed proves a cloud M365 tenant.
        self.assertEqual(scan.classify_evidence(ev(realm="Managed")),
                         ("US_MICROSOFT", "realm-managed"))

    def test_realm_federated_is_microsoft_federated(self):
        # Federated proves a tenant exists; email inferred (don't overclaim).
        self.assertEqual(scan.classify_evidence(ev(realm="Federated")),
                         ("US_MICROSOFT", "realm-federated"))

    def test_realm_unknown_alone_is_none(self):
        self.assertEqual(scan.classify_evidence(ev(realm="Unknown"))[0], "NONE")

    def test_realm_unknown_behind_gateway_stays_other(self):
        # No tenant + a masking gateway MX -> still genuinely unresolved.
        e = ev(mx=["10 vtds.in.tmes.trendmicro.eu"], realm="Unknown")
        self.assertEqual(scan.classify_evidence(e), ("OTHER", None))

    def test_dkim_onmicrosoft_is_hard_microsoft(self):
        e = ev(dkim="selector1-x-no._domainkey.contoso.onmicrosoft.com")
        self.assertEqual(scan.classify_evidence(e), ("US_MICROSOFT", "dkim"))

    def test_dkim_google_selector_is_google(self):
        self.assertEqual(scan.classify_evidence(ev(dkim_google=True)),
                         ("US_GOOGLE", "dkim-google"))

    def test_spf_ms_ip_range_is_hard_microsoft(self):
        # Flattened SPF replaces the outlook include with raw EOP IPs (Alvdal).
        e = ev(spf="v=spf1 ip4:40.92.1.5 ip4:104.47.50.0/24 -all")
        self.assertEqual(scan.classify_evidence(e), ("US_MICROSOFT", "spf-ms-ip"))

    def test_spf_ms_ip_match(self):
        self.assertTrue(scan.spf_ms_ip_match("v=spf1 ip4:40.92.1.5 -all"))
        self.assertTrue(scan.spf_ms_ip_match("v=spf1 ip4:104.47.50.0/24 -all"))
        self.assertIsNone(scan.spf_ms_ip_match("v=spf1 ip4:8.8.8.8 -all"))
        self.assertIsNone(
            scan.spf_ms_ip_match("v=spf1 include:spf.protection.outlook.com -all"))

    def test_dkim_probe_reads_onmicrosoft_cname(self):
        dig = dig_from({
            ("selector1._domainkey.x.no", "CNAME"):
                ["selector1-x-no._domainkey.contoso.onmicrosoft.com"],
        })
        out = scan.dkim_probe("x.no", dig=dig)
        self.assertIn("onmicrosoft.com", out["dkim"])
        self.assertFalse(out["dkim_google"])

    def test_dkim_probe_detects_google_selector(self):
        dig = dig_from({("google._domainkey.x.no", "TXT"): ["v=DKIM1; k=rsa; p=AAA"]})
        out = scan.dkim_probe("x.no", dig=dig)
        self.assertTrue(out["dkim_google"])

    def test_getuserrealm_parses_namespacetype(self):
        body = "<RealmInfo><NameSpaceType>Managed</NameSpaceType></RealmInfo>"
        self.assertEqual(
            scan.getuserrealm("x.no", opener=opener_returning(body)), "Managed")

    def test_getuserrealm_network_failure_returns_none(self):
        def boom(url, timeout=None): raise OSError("no network")
        self.assertIsNone(scan.getuserrealm("x.no", opener=boom))


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

    def test_deep_probe_unmasks_gateway_backend_to_microsoft(self):
        # Base pass sees only a masking gateway MX -> OTHER, then the deep probe
        # (realm=Managed) resolves it to Microsoft with no floor flag left.
        gw = ev(mx=["10 cust.iphmx.com"], spf="v=spf1 -all")
        rec = scan.resolve("Masked", "masked.kommune.no", {}, "2026-06-28",
                           fetch=lambda d: gw,
                           deep=lambda d: {"dkim": "", "dkim_google": False,
                                           "realm": "Managed"})
        self.assertEqual(rec["platform"], "US_MICROSOFT")
        self.assertEqual(rec["fingerprint"], "realm-managed")
        self.assertNotIn("backend_unmasked", rec["flags"])
        self.assertEqual(sig_of(rec["evidence"], "getuserrealm")["observation"],
                         "Managed")

    def test_deep_probe_not_run_when_base_pass_resolves(self):
        # A clean Microsoft MX must not trigger the (costly) HTTPS realm probe.
        def deep_must_not_run(d): raise AssertionError("deep probe should be skipped")
        rec = scan.resolve("X", "x.kommune.no", {}, "2026-06-28",
                           fetch=lambda d: ev(mx=["0 x.mail.protection.outlook.com"]),
                           deep=deep_must_not_run)
        self.assertEqual(rec["platform"], "US_MICROSOFT")

    def test_deep_probe_leaves_genuinely_none_as_other(self):
        # Co-op MX, no Azure tenant, no DKIM -> stays OTHER (floor flag absent:
        # not a gateway substring, but genuinely unresolved Microsoft-wise).
        coop = ev(mx=["10 post.ssikt.no"], spf="v=spf1 -all")
        rec = scan.resolve("Coop", "coop.kommune.no", {}, "2026-06-28",
                           fetch=lambda d: coop,
                           deep=lambda d: {"dkim": "", "dkim_google": False,
                                           "realm": "Unknown"})
        self.assertEqual(rec["platform"], "OTHER")


def sig_of(trail, signal_type):
    """First evidence record of a given signal_type in a trail, else None."""
    return next((s for s in trail if s["signal_type"] == signal_type), None)


class EvidenceTrail(unittest.TestCase):
    """The per-kommune 'show your work' audit trail: one citable record per
    signal, each carrying its raw observation, exact source query, date,
    inference and a 0..1 confidence weight."""

    SCHEMA = {"signal_type", "observation", "source", "observed_at",
              "inference", "confidence", "platform"}

    def test_each_signal_becomes_a_record_with_full_schema(self):
        e = ev(mx=["0 x.mail.protection.outlook.com"],
               spf="v=spf1 include:spf.protection.outlook.com -all",
               auto="autodiscover.outlook.com")
        trail = scan.evidence_trail(e, "x.kommune.no", "2026-06-28")
        self.assertEqual({s["signal_type"] for s in trail} & {"mx", "spf", "autodiscover"},
                         {"mx", "spf", "autodiscover"})
        for s in trail:
            self.assertEqual(set(s), self.SCHEMA)
            self.assertEqual(s["observed_at"], "2026-06-28")   # point-in-time
            self.assertTrue(0.0 <= s["confidence"] <= 1.0)
            self.assertIn("x.kommune.no", s["source"])         # cites the exact query

    def test_mx_observation_is_the_raw_record(self):
        e = ev(mx=["0 x.mail.protection.outlook.com"])
        s = sig_of(scan.evidence_trail(e, "x.no", "2026-06-28"), "mx")
        self.assertIn("x.mail.protection.outlook.com", s["observation"])
        self.assertEqual(s["platform"], "US_MICROSOFT")
        self.assertTrue(s["source"].startswith("dig MX"))

    def test_matched_ms_ip_is_its_own_highlighted_signal(self):
        # The flattened-SPF MS EOP IP gets its own record so the UI can highlight it.
        e = ev(spf="v=spf1 ip4:40.92.1.5 ip4:8.8.8.8 -all")
        trail = scan.evidence_trail(e, "x.no", "2026-06-28")
        s = sig_of(trail, "spf_ip")
        self.assertIsNotNone(s)
        self.assertIn("40.92.1.5", s["observation"])
        self.assertNotIn("8.8.8.8", s["observation"])
        self.assertEqual(s["platform"], "US_MICROSOFT")

    def test_realm_and_dkim_are_captured_with_confidence(self):
        e = ev(dkim="selector1-x._domainkey.contoso.onmicrosoft.com", realm="Managed")
        trail = scan.evidence_trail(e, "x.no", "2026-06-28")
        self.assertEqual(sig_of(trail, "dkim")["platform"], "US_MICROSOFT")
        realm = sig_of(trail, "getuserrealm")
        self.assertEqual(realm["observation"], "Managed")
        self.assertEqual(realm["confidence"], 1.0)
        self.assertIn("getuserrealm", realm["source"])

    def test_federated_realm_is_lower_confidence_than_managed(self):
        fed = sig_of(scan.evidence_trail(ev(realm="Federated"), "x.no", "2026-06-28"),
                     "getuserrealm")
        self.assertEqual(fed["platform"], "US_MICROSOFT")
        self.assertLess(fed["confidence"], 1.0)

    def test_no_signals_yields_empty_trail(self):
        self.assertEqual(scan.evidence_trail(ev(), "x.no", "2026-06-28"), [])


class Verdict(unittest.TestCase):
    """Confidence-weighted platform verdict; honest 'Uavklart' when thin."""

    def test_microsoft_verdict_high_confidence(self):
        rec = scan.make_record("X", "x.no", "x.no",
                               ev(mx=["0 x.mail.protection.outlook.com"]), "2026-06-28")
        v = rec["verdict"]
        self.assertEqual(v["platform"], "US_MICROSOFT")
        self.assertFalse(v["uavklart"])
        self.assertGreaterEqual(v["confidence"], 0.9)

    def test_unresolved_backend_is_uavklart(self):
        # Gateway MX, backend not unmasked -> honest Uavklart, not a guess.
        e = ev(mx=["10 vtds.in.tmes.trendmicro.eu"], spf="v=spf1 -all")
        v = scan.make_record("X", "x.no", "x.no", e, "2026-06-28")["verdict"]
        self.assertTrue(v["uavklart"])
        self.assertEqual(v["platform"], "UAVKLART")
        self.assertTrue(v["note"])

    def test_federated_verdict_is_qualified_lower_confidence(self):
        v = scan.make_record("Fed", "x.no", "x.no", ev(realm="Federated"),
                             "2026-06-28")["verdict"]
        self.assertEqual(v["platform"], "US_MICROSOFT")
        self.assertFalse(v["uavklart"])
        self.assertLess(v["confidence"], 0.9)   # qualified, not airtight M365
        self.assertTrue(v["note"])


class Record(unittest.TestCase):
    def test_carries_evidence_trail_and_sourcedate(self):
        e = ev(mx=["0 x.mail.protection.outlook.com"],
               spf="v=spf1 include:spf.protection.outlook.com -all",
               auto="autodiscover.outlook.com")
        rec = scan.make_record("X", "x.kommune.no", "x.kommune.no", e, "2026-06-28")
        self.assertEqual(rec["sourceDate"], "2026-06-28")
        self.assertIsInstance(rec["evidence"], list)            # evidence[] schema
        self.assertIn("x.mail.protection.outlook.com",
                      sig_of(rec["evidence"], "mx")["observation"])
        self.assertEqual(sig_of(rec["evidence"], "spf")["observation"],
                         "v=spf1 include:spf.protection.outlook.com -all")
        self.assertEqual(sig_of(rec["evidence"], "autodiscover")["observation"],
                         "autodiscover.outlook.com")
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

    def test_federated_is_a_visible_qualifier(self):
        # Federated -> Microsoft, but flagged so it is not merged into hard M365.
        rec = scan.make_record("Fed", "x.no", "x.no", ev(realm="Federated"),
                               "2026-06-28")
        self.assertEqual(rec["platform"], "US_MICROSOFT")
        self.assertIn("federated", rec["flags"])
        self.assertEqual(sig_of(rec["evidence"], "getuserrealm")["observation"],
                         "Federated")

    def test_managed_is_not_federated_flagged(self):
        rec = scan.make_record("Man", "x.no", "x.no", ev(realm="Managed"),
                               "2026-06-28")
        self.assertEqual(rec["platform"], "US_MICROSOFT")
        self.assertNotIn("federated", rec["flags"])


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
