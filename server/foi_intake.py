#!/usr/bin/env python3
"""FOI-answer intake service for Skytilsynet.

A small EU-hosted (Helsinki devbox, RFC-001 P5) HTTP service that accepts
crowd-submitted innsyn/offentleglova answers about a body's sak-/arkivsystem
and hosting, stores them to SQLite for the operator to review BY HAND, and
exposes the pending queue behind an operator secret.

The hard rule (CLAUDE.md, this project's standing no-untrusted-input policy):
submissions are UNTRUSTED public input. They are stored as INERT text, never
rendered/executed server-side, and MUST NEVER be fed to any agent/LLM workflow.
Nothing auto-promotes into the published dataset — a human accepts each row via
scripts/foi_review.py and pastes it into the human-curated data/saksbehandling.csv.

Stdlib only (http.server + sqlite3): the site gains a small backend, not a
framework. Reads PORT + an operator secret from the environment. Put Caddy in
front for TLS + rate limiting (see server/README.md)."""

import glob
import hashlib
import html
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(HERE, "data", "foi_submissions.db")

# Whitelisted fields and their length caps. Anything else in the body is dropped;
# each value is truncated to its cap before it ever touches the DB (defence in
# depth on top of parameterized queries — inert, bounded text).
FIELD_CAPS = {
    "domain": 253,        # a DNS name can't be longer than this
    "vendor": 200,
    "hosting": 200,
    "jurisdiction": 120,
    "source": 2000,       # a URL or a short free-text reference to the answer
    "note": 4000,
}
# The honeypot: a real field name that no human ever sees (hidden in the form).
# Bots that autofill every input trip it; we accept the request but store nothing.
HONEYPOT_FIELD = "company"
MAX_BODY = 32 * 1024                      # reject oversized posts outright
THROTTLE_WINDOW = 3600                    # seconds
THROTTLE_MAX = 8                          # stored submissions per identity / window
# We sit behind exactly one trusted proxy (Caddy). It appends the real peer as
# the LAST X-Forwarded-For entry; every earlier entry is client-supplied and
# forgeable. Count the trusted hops from the right so we never key abuse
# throttling off an attacker-chosen value (issue #84).
TRUSTED_PROXY_HOPS = 1


def known_entities(data_dir=DATA_DIR):
    """{domain: {'name', 'category'}} for every scanned body, read straight from
    the published latest.json snapshots. This is the whitelist: a submission whose
    domain isn't here is rejected — we only take answers about bodies we track."""
    out = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "*email-sovereignty.latest.json"))):
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = doc.get("organ") or doc.get("kommuner") or []
        for e in rows:
            dom = e.get("domain")
            if dom:
                out[dom] = {"name": e.get("name") or e.get("kommune") or dom,
                            "category": e.get("category") or ""}
    return out


def init_db(path=DB_PATH):
    """Create the submissions table if absent. INERT storage: every column is text
    the operator reads by eye; ident_hash is a salted hash of ip+ua for abuse
    throttling only — never the raw ip (rule 5, retain nothing personal)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # check_same_thread=False: ThreadingHTTPServer serves each request on its own
    # thread but shares one connection (sqlite serializes writes internally).
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS submissions (
               id           INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at   TEXT NOT NULL,
               domain       TEXT NOT NULL,
               entity_name  TEXT NOT NULL,
               vendor       TEXT,
               hosting      TEXT,
               jurisdiction TEXT,
               source       TEXT,
               note         TEXT,
               status       TEXT NOT NULL DEFAULT 'new',
               ident_hash   TEXT
           )""")
    conn.commit()
    return conn


def _client_ip(fwd, peer):
    """The proxy-attested client IP for throttling. Caddy appends the real peer as
    the last X-Forwarded-For entry, so we take the TRUSTED_PROXY_HOPS-th hop from
    the right — never index [0], which a client can forge. Falls back to the socket
    peer when there is no usable XFF (direct connection, e.g. tests/local)."""
    hops = [h.strip() for h in fwd.split(",") if h.strip()]
    if len(hops) >= TRUSTED_PROXY_HOPS:
        return hops[-TRUSTED_PROXY_HOPS]
    return peer


def ident_hash(ip, ua, salt):
    """Salted sha256 of the client identity. One-way: for per-identity throttling
    and abuse triage only, never reversible back to an address (rule 5)."""
    return hashlib.sha256(("%s|%s|%s" % (salt, ip or "", ua or "")).encode("utf-8")).hexdigest()


def _clean(value, cap):
    """A submitted value → inert, length-capped, single-line-ish text. Strips NULs
    and trims to the cap. No markup interpretation — it is stored verbatim (bounded)
    and only ever emitted through escaping/quoting at a boundary."""
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()[:cap]


def validate(fields, entities):
    """(clean_record | None, error). Whitelist + cap every field; the domain MUST
    match a known body or the whole submission is rejected. Returns the record to
    store (with entity_name/category resolved) or a human error string."""
    domain = _clean(fields.get("domain"), FIELD_CAPS["domain"]).lower()
    if not domain:
        return None, "domain er påkravd"
    ent = entities.get(domain)
    if ent is None:
        return None, "ukjent domene — vi tek berre imot svar om organ vi sporer"
    rec = {k: _clean(fields.get(k), cap) for k, cap in FIELD_CAPS.items()}
    rec["domain"] = domain
    rec["entity_name"] = ent["name"]
    rec["category"] = ent["category"]
    return rec, None


def store(conn, rec, ident):
    """Insert one validated record. Parameterized query ONLY — the untrusted values
    are bound, never string-formatted into SQL."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = conn.execute(
        """INSERT INTO submissions
               (created_at, domain, entity_name, vendor, hosting, jurisdiction,
                source, note, status, ident_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
        (now, rec["domain"], rec["entity_name"], rec["vendor"], rec["hosting"],
         rec["jurisdiction"], rec["source"], rec["note"], ident))
    conn.commit()
    return cur.lastrowid


def throttled(conn, ident, now=None):
    """True when this identity has stored >= THROTTLE_MAX submissions in the last
    window. Caddy rate-limits too; this is a cheap second gate."""
    if not ident:
        return False
    now = now if now is not None else time.time()
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - THROTTLE_WINDOW))
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE ident_hash = ? AND created_at >= ?",
        (ident, cutoff)).fetchone()
    return n >= THROTTLE_MAX


def pending(conn):
    """New (unreviewed) submissions as a list of dicts, oldest first — the operator
    review queue. Excludes ident_hash (abuse-only, never surfaced)."""
    cols = ["id", "created_at", "domain", "entity_name", "vendor", "hosting",
            "jurisdiction", "source", "note", "status"]
    rows = conn.execute(
        "SELECT %s FROM submissions WHERE status = 'new' ORDER BY id" % ", ".join(cols)
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


# Leading characters a spreadsheet treats as the start of a formula (CWE-1236).
_CSV_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutralize a single CSV cell against spreadsheet formula injection: prefix a
    leading formula character with a `'` so Excel/LibreOffice/Sheets treat the cell
    as text. Submissions are untrusted public input and the operator who exports and
    opens the CSV is the intended victim, so apply this to every emitted cell."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_CSV_FORMULA_LEADERS) else s


def _pending_csv(records):
    import csv
    import io
    buf = io.StringIO()
    cols = ["id", "created_at", "domain", "entity_name", "vendor", "hosting",
            "jurisdiction", "source", "note", "status"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in records:
        w.writerow({k: csv_safe(v) for k, v in r.items()})
    return buf.getvalue()


_THANKS_HTML = """<!doctype html>
<html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Takk — Skytilsynet</title></head>
<body style="font-family:sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem">
<h1>Takk for svaret</h1>
<p>Svaret ditt er lagra for <b>manuell gjennomgang</b> av ein operatør. Vi legg
det inn i det opne datasettet med kjelde dersom det sjekkar ut. Vi lagrar ingen
personopplysningar om deg.</p>
<p><a href="/bidra">Send eit svar til</a> · <a href="/">Til Skybarometeret</a></p>
</body></html>"""


def make_app(conn, entities, token, salt):
    """Build a BaseHTTPRequestHandler bound to this DB/config. Kept as a factory so
    tests can spin it up on an ephemeral port against a temp DB (real seam)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "SkytilsynetFOI/1"

        def log_message(self, *a):        # keep stdout quiet; the operator has journald
            pass

        # ---- helpers ----
        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _wants_json(self, ctype):
            accept = self.headers.get("Accept", "")
            return "application/json" in accept or "application/json" in ctype

        def _client_ident(self):
            # Behind Caddy the real client is the proxy-attested X-Forwarded-For
            # hop; fall back to the socket peer. Never trust the client-supplied
            # first hop (issue #84).
            ip = _client_ip(self.headers.get("X-Forwarded-For", ""), self.client_address[0])
            return ident_hash(ip, self.headers.get("User-Agent", ""), salt)

        # ---- routes ----
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/foi/pending":
                return self._pending()
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if urlparse(self.path).path != "/api/foi":
                return self._send(404, json.dumps({"error": "not found"}))
            self._intake()

        def _intake(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, json.dumps({"error": "for stor"}))
            raw = self.rfile.read(length) if length else b""
            ctype = self.headers.get("Content-Type", "")
            fields = _parse_body(raw, ctype)
            wants_json = self._wants_json(ctype)

            # Honeypot: a filled hidden field means a bot. Accept (so it can't probe
            # by watching for errors) but store nothing.
            if _clean(fields.get(HONEYPOT_FIELD), 200):
                return self._thanks(wants_json)

            rec, err = validate(fields, entities)
            if err:
                if wants_json:
                    return self._send(400, json.dumps({"error": err}))
                return self._send(400, "<!doctype html><meta charset=utf-8><p>Feil: "
                                  + html.escape(err) + " <a href=/bidra>tilbake</a>",
                                  "text/html; charset=utf-8")

            ident = self._client_ident()
            if throttled(conn, ident):
                return self._send(429, json.dumps({"error": "for mange innsendingar, prov igjen seinare"}))

            new_id = store(conn, rec, ident)
            return self._thanks(wants_json, new_id)

        def _thanks(self, wants_json, new_id=None):
            if wants_json:
                return self._send(200, json.dumps({"ok": True, "id": new_id}))
            # No-JS form post: a redirect keeps the browser off the POST URL.
            self._send(303, "", "text/html; charset=utf-8", {"Location": "/bidra?sendt=1"})

        def _pending(self):
            if not _authorized(self.headers.get("Authorization", ""), token):
                return self._send(401, json.dumps({"error": "operator secret required"}),
                                  "application/json; charset=utf-8",
                                  {"WWW-Authenticate": 'Basic realm="foi"'})
            recs = pending(conn)
            if urlparse(self.path).query and "format=csv" in urlparse(self.path).query:
                return self._send(200, _pending_csv(recs), "text/csv; charset=utf-8")
            self._send(200, json.dumps({"pending": recs}, ensure_ascii=False))

    return Handler


def _parse_body(raw, ctype):
    """Untrusted body → flat {field: value} dict. Accepts JSON or form-encoded.
    Never eval'd, never templated — parsed into plain strings only."""
    text = raw.decode("utf-8", "replace")
    if "application/json" in ctype:
        try:
            obj = json.loads(text or "{}")
            return {k: v for k, v in obj.items()} if isinstance(obj, dict) else {}
        except ValueError:
            return {}
    return {k: v[0] for k, v in parse_qs(text, keep_blank_values=True).items()}


def _authorized(header, token):
    """The operator gate for /api/foi/pending. HTTP Basic (any user, password ==
    token) or `Bearer <token>`. If no token is configured, the endpoint is closed."""
    if not token:
        return False
    header = header or ""
    if header.startswith("Bearer "):
        return _consteq(header[7:].strip(), token)
    if header.startswith("Basic "):
        import base64
        try:
            decoded = base64.b64decode(header[6:].strip()).decode("utf-8", "replace")
        except Exception:
            return False
        _, _, pw = decoded.partition(":")
        return _consteq(pw, token)
    return False


def _consteq(a, b):
    import hmac
    return hmac.compare_digest(str(a), str(b))


def run():
    port = int(os.environ.get("PORT", "8781"))
    # Bind host: 127.0.0.1 by default (host-systemd behind Caddy). In a container on
    # an internal-only docker network (no host port mapping), set HOST=0.0.0.0 so the
    # sibling Caddy container can reach it by name — still not exposed to the host/net.
    host = os.environ.get("HOST", "127.0.0.1")
    token = os.environ.get("FOI_OPERATOR_TOKEN", "")
    salt = os.environ.get("FOI_HASH_SALT") or token or "skytilsynet-foi"
    if not token:
        raise SystemExit("FOI_OPERATOR_TOKEN must be set (guards /api/foi/pending)")
    conn = init_db()
    handler = make_app(conn, known_entities(), token, salt)
    httpd = ThreadingHTTPServer((host, port), handler)
    print("FOI intake listening on %s:%d" % (host, port))
    httpd.serve_forever()


if __name__ == "__main__":
    run()
