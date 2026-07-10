#!/usr/bin/env python3
"""Tests for the FOI intake service + operator review CLI (issue #54).

Exercises the real seams: a live http.server on an ephemeral port against a temp
SQLite DB (endpoint validation, honeypot, throttle, operator gate) and the review
CLI's accept/reject → saksbehandling.csv emission."""

import base64
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from server import foi_intake  # noqa: E402
from scripts import foi_review  # noqa: E402

ENTITIES = {
    "larvik.kommune.no": {"name": "Larvik kommune", "category": "kommune"},
    "oslo.kommune.no": {"name": "Oslo kommune", "category": "kommune"},
}
TOKEN = "s3cret-operator-token"
SALT = "test-salt"


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "foi.db")
        self.conn = foi_intake.init_db(self.db)
        handler = foi_intake.make_app(self.conn, ENTITIES, TOKEN, SALT)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.conn.close()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def post(self, payload, as_json=True, headers=None):
        h = dict(headers or {})
        if as_json:
            data = json.dumps(payload).encode()
            h.setdefault("Content-Type", "application/json")
            h.setdefault("Accept", "application/json")
        else:
            from urllib.parse import urlencode
            data = urlencode(payload).encode()
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = Request(self.url("/api/foi"), data=data, headers=h, method="POST")
        try:
            r = urlopen(req)
            return r.status, r.read().decode(), dict(r.headers)
        except HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def _rows(self):
        return foi_intake.pending(self.conn)

    # ---- happy path -----------------------------------------------------
    def test_valid_submission_is_stored(self):
        status, body, _ = self.post({
            "domain": "larvik.kommune.no", "vendor": "Acos WebSak",
            "hosting": "Norge", "jurisdiction": "Norge (EØS)",
            "source": "https://example.org/svar", "note": "svar per e-post"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domain"], "larvik.kommune.no")
        self.assertEqual(rows[0]["entity_name"], "Larvik kommune")
        self.assertEqual(rows[0]["vendor"], "Acos WebSak")
        self.assertEqual(rows[0]["status"], "new")

    def test_no_auth_required_to_submit(self):
        status, _, _ = self.post({"domain": "oslo.kommune.no", "vendor": "X"})
        self.assertEqual(status, 200)

    # ---- validation -----------------------------------------------------
    def test_unknown_domain_rejected(self):
        status, body, _ = self.post({"domain": "evil.example.com", "vendor": "X"})
        self.assertEqual(status, 400)
        self.assertIn("ukjent", json.loads(body)["error"])
        self.assertEqual(len(self._rows()), 0)

    def test_missing_domain_rejected(self):
        status, _, _ = self.post({"vendor": "X"})
        self.assertEqual(status, 400)
        self.assertEqual(len(self._rows()), 0)

    def test_fields_are_length_capped(self):
        big = "A" * 5000
        self.post({"domain": "oslo.kommune.no", "vendor": big, "note": big})
        row = self._rows()[0]
        self.assertEqual(len(row["vendor"]), foi_intake.FIELD_CAPS["vendor"])
        self.assertEqual(len(row["note"]), foi_intake.FIELD_CAPS["note"])

    def test_only_whitelisted_fields_stored(self):
        # An injected extra column must be ignored, not stored.
        self.post({"domain": "oslo.kommune.no", "vendor": "X", "status": "accepted",
                   "id": 999, "evil": "DROP TABLE"})
        row = self._rows()[0]
        self.assertEqual(row["status"], "new")
        self.assertNotEqual(row["id"], 999)

    def test_sql_injection_is_inert(self):
        # Parameterized queries: a SQL payload is stored verbatim, not executed.
        payload = "Robert'); DROP TABLE submissions;--"
        self.post({"domain": "oslo.kommune.no", "vendor": payload})
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vendor"], payload[:foi_intake.FIELD_CAPS["vendor"]])

    # ---- abuse controls -------------------------------------------------
    def test_honeypot_accepts_but_drops(self):
        status, body, _ = self.post({"domain": "oslo.kommune.no", "vendor": "X",
                                     foi_intake.HONEYPOT_FIELD: "botfill"})
        self.assertEqual(status, 200)
        self.assertEqual(len(self._rows()), 0)

    def test_oversized_body_rejected(self):
        status, _, _ = self.post({"domain": "oslo.kommune.no",
                                  "vendor": "A" * (foi_intake.MAX_BODY + 10)})
        self.assertEqual(status, 413)

    def test_per_identity_throttle(self):
        for _ in range(foi_intake.THROTTLE_MAX):
            s, _, _ = self.post({"domain": "oslo.kommune.no", "vendor": "X"})
            self.assertEqual(s, 200)
        s, _, _ = self.post({"domain": "oslo.kommune.no", "vendor": "X"})
        self.assertEqual(s, 429)

    def test_throttle_ignores_forged_first_xff_hop(self):
        # Issue #84: an attacker rotates the client-supplied FIRST X-Forwarded-For
        # entry to dodge the per-identity cap. Caddy appends the real peer as the
        # LAST entry, so identity keys off that — rotating the first hop must not help.
        real = "203.0.113.9"
        for i in range(foi_intake.THROTTLE_MAX):
            s, _, _ = self.post({"domain": "oslo.kommune.no", "vendor": "X"},
                                headers={"X-Forwarded-For": "10.0.0.%d, %s" % (i, real)})
            self.assertEqual(s, 200)
        s, _, _ = self.post({"domain": "oslo.kommune.no", "vendor": "X"},
                            headers={"X-Forwarded-For": "10.0.0.250, " + real})
        self.assertEqual(s, 429)

    # ---- form-encoded + no-JS fallback ---------------------------------
    def test_form_post_redirects_to_bidra(self):
        # No-JS fallback: a form post stores the row and 303-redirects the browser
        # to the Caddy-served /bidra success page (this service doesn't serve it).
        from urllib.request import build_opener, HTTPRedirectHandler
        from urllib.parse import urlencode

        class NoFollow(HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None

        data = urlencode({"domain": "oslo.kommune.no", "vendor": "X"}).encode()
        req = Request(self.url("/api/foi"), data=data, method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            r = build_opener(NoFollow).open(req)
            status, location = r.status, r.headers.get("Location")
        except HTTPError as e:
            status, location = e.code, e.headers.get("Location")
        self.assertEqual(status, 303)
        self.assertIn("/bidra", location)
        self.assertEqual(len(self._rows()), 1)

    # ---- operator-only pending endpoint --------------------------------
    def test_pending_requires_operator_secret(self):
        req = Request(self.url("/api/foi/pending"))
        with self.assertRaises(HTTPError) as cm:
            urlopen(req)
        self.assertEqual(cm.exception.code, 401)

    def test_pending_with_bearer_token(self):
        self.post({"domain": "larvik.kommune.no", "vendor": "Acos WebSak"})
        req = Request(self.url("/api/foi/pending"),
                      headers={"Authorization": "Bearer " + TOKEN})
        body = json.loads(urlopen(req).read().decode())
        self.assertEqual(len(body["pending"]), 1)
        self.assertNotIn("ident_hash", body["pending"][0])

    def test_pending_with_basic_auth_and_csv(self):
        self.post({"domain": "larvik.kommune.no", "vendor": "Acos WebSak"})
        cred = base64.b64encode(b"operator:" + TOKEN.encode()).decode()
        req = Request(self.url("/api/foi/pending?format=csv"),
                      headers={"Authorization": "Basic " + cred})
        r = urlopen(req)
        self.assertIn("text/csv", r.headers.get("Content-Type"))
        self.assertIn("larvik.kommune.no", r.read().decode())

    def test_wrong_token_rejected(self):
        req = Request(self.url("/api/foi/pending"),
                      headers={"Authorization": "Bearer wrong"})
        with self.assertRaises(HTTPError) as cm:
            urlopen(req)
        self.assertEqual(cm.exception.code, 401)


class ReviewCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "foi.db")
        self.conn = foi_intake.init_db(self.db)
        self.rec = {"domain": "larvik.kommune.no", "entity_name": "Larvik kommune",
                    "vendor": "Acos WebSak", "hosting": "Norge",
                    "jurisdiction": "Norge (EØS)", "source": "https://ex.org/svar",
                    "note": "notat"}
        self.sid = foi_intake.store(self.conn, self.rec, "identhash")

    def _sub(self):
        return foi_review._fetch(self.conn, self.sid)

    def test_accept_sets_status_and_emits_row(self):
        foi_review.cmd_accept(self.conn, _Args(id=self.sid))
        self.assertEqual(self._sub()["status"], "accepted")

    def test_saksbehandling_row_matches_header(self):
        row = foi_review.saksbehandling_row(
            self._sub(), {"larvik.kommune.no": {"category": "kommune"}})
        self.assertEqual(len(row), len(foi_review.SAKS_HEADER))
        d = dict(zip(foi_review.SAKS_HEADER, row))
        self.assertEqual(d["domain"], "larvik.kommune.no")
        self.assertEqual(d["category"], "kommune")
        self.assertEqual(d["vendor"], "Acos WebSak")
        self.assertEqual(d["vendor_method"], "innsyn-foi")
        self.assertEqual(d["hosting_method"], "innsyn-foi")
        self.assertEqual(d["hosting_jurisdiction"], "Norge (EØS)")
        self.assertEqual(d["hosting_source"], "https://ex.org/svar")

    def test_emitted_row_carries_the_source_tier(self):
        # Issue #55: the emitted row stamps the re-checkable-source tier so the
        # published dataset knows how the verdict can be re-checked.
        ents = {"larvik.kommune.no": {"category": "kommune"}}
        d = dict(zip(foi_review.SAKS_HEADER,
                     foi_review.saksbehandling_row(self._sub(), ents)))
        self.assertEqual(d["hosting_source_type"], "innsyn-pa-fil")   # default tier
        d2 = dict(zip(foi_review.SAKS_HEADER,
                      foi_review.saksbehandling_row(self._sub(), ents,
                                                    source_type="offentlig-journal")))
        self.assertEqual(d2["hosting_source_type"], "offentlig-journal")

    def test_accept_refuses_a_submission_without_a_source(self):
        # #55 §5: no verdict without a re-checkable source — accept must have one.
        rec = dict(self.rec); rec["source"] = ""
        sid = foi_intake.store(self.conn, rec, "identhash2")
        with self.assertRaises(SystemExit):
            foi_review.cmd_accept(self.conn, _Args(id=sid))
        # And it stays 'new' — nothing was promoted.
        self.assertEqual(foi_review._fetch(self.conn, sid)["status"], "new")

    def test_accept_shows_the_source_before_emitting(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            foi_review.cmd_accept(self.conn, _Args(id=self.sid))
        self.assertIn("https://ex.org/svar", buf.getvalue())         # source shown
        self.assertIn("innsyn-pa-fil", buf.getvalue())               # tier shown

    def test_accept_stamps_chosen_tier_on_emitted_row(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            foi_review.cmd_accept(self.conn,
                                  _Args(id=self.sid, source_type="offentlig-journal"))
        self.assertIn("offentlig-journal", buf.getvalue())

    def test_reject_sets_status(self):
        foi_review.cmd_reject(self.conn, _Args(id=self.sid))
        self.assertEqual(self._sub()["status"], "rejected")

    def test_nothing_auto_promotes(self):
        # A stored submission is 'new' until a human acts — never auto-accepted.
        self.assertEqual(self._sub()["status"], "new")
        self.assertEqual(len(foi_intake.pending(self.conn)), 1)


class ClientIpTest(unittest.TestCase):
    """The proxy-attested client IP used for abuse throttling (issue #84)."""

    def test_takes_trusted_last_hop_not_client_first(self):
        # "<forged>, <real peer Caddy appended>" → we key off the real one.
        self.assertEqual(
            foi_intake._client_ip("9.9.9.9, 203.0.113.5", "127.0.0.1"), "203.0.113.5")

    def test_single_hop_is_the_client(self):
        self.assertEqual(foi_intake._client_ip("203.0.113.5", "127.0.0.1"), "203.0.113.5")

    def test_falls_back_to_peer_without_xff(self):
        self.assertEqual(foi_intake._client_ip("", "198.51.100.2"), "198.51.100.2")


class KnownEntitiesTest(unittest.TestCase):
    def test_loads_real_domains(self):
        ents = foi_intake.known_entities()
        self.assertIn("larvik.kommune.no", ents)
        self.assertTrue(ents["larvik.kommune.no"]["name"])


class CsvInjectionTest(unittest.TestCase):
    """Issue #80 (CWE-1236): untrusted answer fields must not become spreadsheet
    formulas when the operator opens an exported CSV in Excel/LibreOffice/Sheets."""

    def test_csv_safe_neutralizes_formula_leaders(self):
        for bad in ("=1+1", "+1", "-1", "@SUM(A1)", "\tcmd", "\rx"):
            out = foi_intake.csv_safe(bad)
            self.assertTrue(out.startswith("'"), "%r not neutralized" % bad)
            self.assertEqual(out[1:], bad)

    def test_csv_safe_leaves_ordinary_values_untouched(self):
        for ok in ("Acos WebSak", "Norge (EØS)", "https://ex.org/svar", "", None):
            self.assertEqual(foi_intake.csv_safe(ok), "" if ok is None else ok)

    def test_pending_csv_neutralizes_formula_fields(self):
        rec = {"id": 1, "created_at": "2026-07-10T00:00:00Z",
               "domain": "larvik.kommune.no", "entity_name": "Larvik kommune",
               "vendor": "=HYPERLINK(\"http://evil\")", "hosting": "@evil",
               "jurisdiction": "-2+3", "source": "+cmd|' /c calc'!A0",
               "note": "\t=1", "status": "new"}
        out = foi_intake._pending_csv([rec])
        self.assertIn("'=HYPERLINK", out)
        self.assertIn("'@evil", out)
        self.assertIn("'-2+3", out)
        self.assertNotIn(",=HYPERLINK", out)
        self.assertNotIn(",@evil", out)

    def test_row_csv_neutralizes_formula_fields(self):
        row = ["larvik.kommune.no", "kommune", "=cmd|' /c calc'!A0", "innsyn-foi",
               "+evil", "2026-07-10", "@x", "-y", "innsyn-foi", "ok",
               "innsyn-pa-fil", "2026-07-10", "=1+1"]
        out = foi_review._row_csv(row)
        self.assertIn("'=cmd", out)
        self.assertIn("'+evil", out)
        self.assertIn("'@x", out)
        self.assertIn("'-y", out)
        self.assertNotIn(",=cmd", out)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


if __name__ == "__main__":
    unittest.main()
