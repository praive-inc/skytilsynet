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
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from server import foi_crypto  # noqa: E402
from server import foi_intake  # noqa: E402
from scripts import foi_review  # noqa: E402

# A deterministic 32-byte urlsafe-base64 Fernet key for the encryption tests.
ENC_KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

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

    # ---- free-text PII minimization (#114) ------------------------------
    def test_emails_redacted_from_free_text_on_intake(self):
        # A submitter may paste an official's e-mail out of an innsyn answer into
        # the free-text fields. We strip e-mails at the boundary so no such PII is
        # ever stored (GDPR Art. 5(1)(c) data minimization).
        self.post({"domain": "oslo.kommune.no", "vendor": "Acos",
                   "source": "sjå svaret frå ola.nordmann@oslo.kommune.no",
                   "note": "ring Kari (kari.hansen@example.com) for meir"})
        row = self._rows()[0]
        self.assertNotIn("@", row["source"])
        self.assertNotIn("@", row["note"])
        self.assertIn(foi_intake.EMAIL_PLACEHOLDER, row["source"])
        self.assertIn(foi_intake.EMAIL_PLACEHOLDER, row["note"])

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

    def test_csv_export_neutralizes_formula_injection_end_to_end(self):
        # Issue #90 (CWE-1236): an untrusted submission with formula-leading fields
        # must come back inert from the real POST-intake → GET-csv seam, while the
        # stored value stays byte-for-byte unchanged (only the CSV rendering differs).
        import csv as _csv
        import io as _io
        self.post({"domain": "larvik.kommune.no",
                   "vendor": "=HYPERLINK(\"http://evil\")", "hosting": "@evil",
                   "source": "+cmd|' /c calc'!A0", "note": "-2+3"})
        cred = base64.b64encode(b"operator:" + TOKEN.encode()).decode()
        req = Request(self.url("/api/foi/pending?format=csv"),
                      headers={"Authorization": "Basic " + cred})
        body = urlopen(req).read().decode()

        row = next(_csv.DictReader(_io.StringIO(body)))  # valid, correctly-quoted CSV
        self.assertEqual(row["vendor"], "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(row["hosting"], "'@evil")
        self.assertEqual(row["source"], "'+cmd|' /c calc'!A0")
        self.assertEqual(row["note"], "'-2+3")
        self.assertEqual(row["domain"], "larvik.kommune.no")  # ordinary value untouched

        # Stored data is unchanged — neutralization is render-only.
        self.assertEqual(self._rows()[0]["vendor"], "=HYPERLINK(\"http://evil\")")

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

    def test_accept_neutralizes_formula_fields_in_emitted_row(self):
        # Issue #95 (CWE-1236): a submission whose answer fields lead with a
        # spreadsheet formula char must reach the pasteable row neutralized —
        # exercised through the real review path (saksbehandling_row → _row_csv
        # via cmd_accept), not a hand-built row.
        import io
        from contextlib import redirect_stdout
        rec = {"domain": "larvik.kommune.no", "entity_name": "Larvik kommune",
               "vendor": "=HYPERLINK(\"http://evil\")", "hosting": "@x",
               "jurisdiction": "-y", "source": "https://ex.org/svar",
               "note": "+cmd|' /c calc'!A0"}
        sid = foi_intake.store(self.conn, rec, "identhash-formula")
        buf = io.StringIO()
        with redirect_stdout(buf):
            foi_review.cmd_accept(self.conn, _Args(id=sid))
        out = buf.getvalue()
        self.assertIn("'=HYPERLINK", out)
        self.assertIn("'@x", out)
        self.assertIn("'-y", out)
        self.assertIn("'+cmd", out)
        self.assertNotIn(",=HYPERLINK", out)

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
    formulas when the operator opens an exported CSV in Excel/LibreOffice/Sheets.

    The unit tests for the csv_safe helper itself live with it in
    shared/test_csv_safe.py; these cover the exporters that apply it."""

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


class EncryptionTest(unittest.TestCase):
    """Field-level encryption at rest for source/note (#114). Opt-in via a key."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "foi.db")
        self.conn = foi_intake.init_db(self.db)
        self.cipher = foi_crypto.Cipher(ENC_KEY)
        self.rec = {"domain": "larvik.kommune.no", "entity_name": "Larvik kommune",
                    "vendor": "Acos WebSak", "hosting": "Norge",
                    "jurisdiction": "Norge (EØS)", "source": "https://ex.org/svar",
                    "note": "eit notat"}

    def _raw(self, sid):
        return self.conn.execute(
            "SELECT source, note FROM submissions WHERE id = ?", (sid,)).fetchone()

    def test_source_note_ciphertext_at_rest_plaintext_on_read(self):
        sid = foi_intake.store(self.conn, self.rec, "ident", cipher=self.cipher)
        raw_source, raw_note = self._raw(sid)
        # On disk: marked ciphertext, no readable value.
        self.assertTrue(raw_source.startswith(foi_crypto.ENC_PREFIX))
        self.assertNotIn("ex.org", raw_source)
        self.assertNotIn("notat", raw_note)
        # Through the read path with the key: transparent plaintext.
        row = foi_intake.pending(self.conn, cipher=self.cipher)[0]
        self.assertEqual(row["source"], "https://ex.org/svar")
        self.assertEqual(row["note"], "eit notat")

    def test_other_columns_are_not_encrypted(self):
        sid = foi_intake.store(self.conn, self.rec, "ident", cipher=self.cipher)
        vendor, = self.conn.execute(
            "SELECT vendor FROM submissions WHERE id = ?", (sid,)).fetchone()
        self.assertEqual(vendor, "Acos WebSak")

    def test_legacy_plaintext_rows_read_through(self):
        # A row written before a key was configured (plaintext) must still read
        # back correctly once encryption is switched on.
        sid = foi_intake.store(self.conn, self.rec, "ident")           # no cipher
        row = foi_intake.pending(self.conn, cipher=self.cipher)[0]
        self.assertEqual(row["source"], "https://ex.org/svar")
        # And the review CLI fetch decrypts too.
        sub = foi_review._fetch(self.conn, sid, cipher=self.cipher)
        self.assertEqual(sub["source"], "https://ex.org/svar")

    def test_review_fetch_decrypts_encrypted_rows(self):
        sid = foi_intake.store(self.conn, self.rec, "ident", cipher=self.cipher)
        sub = foi_review._fetch(self.conn, sid, cipher=self.cipher)
        self.assertEqual(sub["source"], "https://ex.org/svar")
        self.assertEqual(sub["note"], "eit notat")

    def test_cipher_from_env_off_by_default(self):
        self.assertIsNone(foi_crypto.cipher_from_env({}))
        self.assertIsNotNone(foi_crypto.cipher_from_env({"FOI_ENCRYPTION_KEY": ENC_KEY}))


class RetentionTest(unittest.TestCase):
    """TTL purge: submissions are deleted N days after a decision, with a backstop
    for undecided rows so nothing persists indefinitely (#114, GDPR Art. 5(1)(e))."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "foi.db")
        self.conn = foi_intake.init_db(self.db)
        self.rec = {"domain": "larvik.kommune.no", "entity_name": "Larvik kommune",
                    "vendor": "Acos", "hosting": "Norge", "jurisdiction": "Norge",
                    "source": "https://ex.org/svar", "note": "n"}

    def _ids(self):
        return [r[0] for r in self.conn.execute("SELECT id FROM submissions")]

    def test_decision_stamps_decided_at(self):
        sid = foi_intake.store(self.conn, self.rec, "ident")
        foi_review.cmd_reject(self.conn, _Args(id=sid))
        decided, = self.conn.execute(
            "SELECT decided_at FROM submissions WHERE id = ?", (sid,)).fetchone()
        self.assertTrue(decided)

    def test_purge_deletes_decided_past_retention(self):
        sid = foi_intake.store(self.conn, self.rec, "ident")
        foi_review.cmd_reject(self.conn, _Args(id=sid))
        later = time.time() + (foi_intake.RETENTION_DECIDED_DAYS + 1) * 86400
        n = foi_intake.purge_expired(self.conn, now=later)
        self.assertEqual(n, 1)
        self.assertEqual(self._ids(), [])

    def test_purge_keeps_recently_decided(self):
        sid = foi_intake.store(self.conn, self.rec, "ident")
        foi_review.cmd_accept(self.conn, _Args(id=sid))
        n = foi_intake.purge_expired(self.conn)                 # now = today
        self.assertEqual(n, 0)
        self.assertEqual(self._ids(), [sid])

    def test_purge_keeps_recent_undecided(self):
        sid = foi_intake.store(self.conn, self.rec, "ident")
        n = foi_intake.purge_expired(self.conn)
        self.assertEqual(n, 0)
        self.assertEqual(self._ids(), [sid])

    def test_purge_deletes_stale_undecided_backstop(self):
        sid = foi_intake.store(self.conn, self.rec, "ident")
        later = time.time() + (foi_intake.RETENTION_NEW_DAYS + 1) * 86400
        n = foi_intake.purge_expired(self.conn, now=later)
        self.assertEqual(n, 1)
        self.assertEqual(self._ids(), [])


class OperatorAuditTest(unittest.TestCase):
    """Every operator read/decision in the review CLI is logged with who/when/what
    (#114 MED — operator-access audit)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "foi.db")
        self.conn = foi_intake.init_db(self.db)
        rec = {"domain": "larvik.kommune.no", "entity_name": "Larvik kommune",
               "vendor": "Acos", "hosting": "Norge", "jurisdiction": "Norge",
               "source": "https://ex.org/svar", "note": "n"}
        self.sid = foi_intake.store(self.conn, rec, "ident")

    def _log(self):
        return self.conn.execute(
            "SELECT action, sub_id, actor, at FROM operator_access_log ORDER BY id"
        ).fetchall()

    def test_accept_is_audited(self):
        foi_review.cmd_accept(self.conn, _Args(id=self.sid))
        entries = self._log()
        self.assertIn(("accept", self.sid), [(a, s) for a, s, _, _ in entries])
        self.assertTrue(all(actor and at for _, _, actor, at in entries))

    def test_reject_is_audited(self):
        foi_review.cmd_reject(self.conn, _Args(id=self.sid))
        self.assertIn(("reject", self.sid), [(a, s) for a, s, _, _ in self._log()])

    def test_show_read_is_audited(self):
        foi_review.cmd_show(self.conn, _Args(id=self.sid))
        self.assertIn(("show", self.sid), [(a, s) for a, s, _, _ in self._log()])

    def test_list_read_is_audited(self):
        foi_review.cmd_list(self.conn, _Args(all=False))
        self.assertIn("list", [a for a, _, _, _ in self._log()])

    def test_actor_recorded_from_env(self):
        os.environ["FOI_OPERATOR"] = "alice"
        try:
            foi_review.cmd_reject(self.conn, _Args(id=self.sid))
        finally:
            del os.environ["FOI_OPERATOR"]
        self.assertEqual(self._log()[-1][2], "alice")


class FormWarningTest(unittest.TestCase):
    """The intake form must warn submitters off pasting personal data (#114)."""

    def test_form_carries_pii_warning(self):
        path = os.path.join(os.path.dirname(HERE), "web", "bidra", "index.html")
        html = open(path, encoding="utf-8").read()
        self.assertIn("personopplysningar", html.lower())
        # The warning is explicit about not pasting names/e-mails.
        self.assertIn("Ikkje lim inn", html)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


if __name__ == "__main__":
    unittest.main()
