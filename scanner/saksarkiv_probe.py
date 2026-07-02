#!/usr/bin/env python3
"""
Sakarkiv innsyn-portal fingerprint probe (Skytilsynet, issue #61).

Identifies which case-management/archive (NOARK-5 sakarkiv) VENDOR a public body
runs, from the third-party host its public innsyn/postliste portal answers to —
a citable, no-auth signal. A portal fingerprint identifies the VENDOR only; it
NEVER asserts a hosting jurisdiction (that stays "utledet/Uavklart" until an
offentleglova FOI answer confirms it — see web/build.py).

Two passes, cheapest first:

  1. ZERO-COST: mine the already-collected web-axis third_parties
     (data/kommune-web-sovereignty.latest.json). No new request — the homepage
     resources were already fetched by web_scan.py.
  2. PROBE (opt-in, network): for bodies the zero-cost pass missed, fetch the
     /innsyn and /postliste paths and fingerprint the hosts they link to.
     RESPECTFUL by design: honors robots.txt, rate-limited, cached across runs,
     and BACKS OFF on the first 403 (the Apr-2026 vendor bot-blocking risk — we
     do not hammer).

Output: data/saksbehandling-auto.json — per entity {domain, vendor,
vendor_method=portal-fingerprint, vendor_source, vendor_date}. build.py merges
this UNDER the human-curated data/saksbehandling.csv (manual/FOI always wins).
This script NEVER writes the human CSV.

Run:  python3 saksarkiv_probe.py            # zero-cost pass only (no network)
      SAK_PROBE=1 python3 saksarkiv_probe.py # + polite live probe of the rest
"""
import json, os, re, sys, time
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DATA = os.path.join(HERE, os.pardir, "data", "kommune-web-sovereignty.latest.json")
OUT = os.path.join(HERE, os.pardir, "data", "saksbehandling-auto.json")
CACHE = os.path.join(HERE, ".saksarkiv_probe_cache.json")

UA = "SkytilsynetBot/1.0 (+https://skytilsynet.no; civic transparency scan)"

# Portal host substring -> full sakarkiv vendor name. Full names (not family
# tokens) so the record drops straight into build.py's VENDOR_HOSTING table and
# the CSV vendor column. Ordered: first substring hit wins. Mirrors the research
# fingerprint set (issue #61 §1) and web/build.py's PORTAL_FINGERPRINTS.
PORTAL_FINGERPRINTS = [
    ("onacos.no", "Acos WebSak"),
    ("acossky.no", "Acos WebSak"),
    ("elementscloud.no", "Sikri Elements"),
    ("360online.com", "Tietoevry Public 360"),
    ("public.cloudservices.no", "Tietoevry Public 360"),
    ("ephinnsyn", "ePhorte (legacy)"),
    ("ephorte", "ePhorte (legacy)"),
]

# The public innsyn/postliste paths the probe pass tries, cheapest-first. Kept
# short — one or two polite GETs per body, only for bodies the zero-cost pass
# missed.
INNSYN_PATHS = ["/innsyn", "/postliste", "/innsyn-og-offentlighet"]

PROBE_DELAY = 1.0   # seconds between probed bodies — polite, non-negotiable


def fingerprint(host):
    """Portal host -> full sakarkiv vendor name, or None. First substring hit in
    the ordered table wins; case-insensitive."""
    if not host:
        return None
    h = host.lower()
    for needle, vendor in PORTAL_FINGERPRINTS:
        if needle in h:
            return vendor
    return None


def _host_of(url):
    """URL (absolute or protocol-relative) -> bare host, else None."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        return None
    return urlparse(url).netloc.lower().split("@")[-1].split(":")[0] or None


def link_hosts(html):
    """Distinct external hosts referenced from src/href attributes in the HTML."""
    out = set()
    for m in re.finditer(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html, re.I):
        host = _host_of(m.group(1))
        if host:
            out.add(host)
    return sorted(out)


def _record(name, domain, vendor, portal_host, date, evidence):
    """Assemble one auto record. Carries the VENDOR only — never a hosting
    jurisdiction (a fingerprint identifies the vendor, not where it runs)."""
    return {
        "domain": domain,
        "kommune": name,
        "vendor": vendor,
        "vendor_method": "portal-fingerprint",
        "vendor_source": "https://" + portal_host,
        "vendor_date": date,
        "vendor_host": portal_host,
        "vendor_evidence": evidence,
    }


def mine_web_axis(records, date):
    """ZERO-COST pass: fingerprint the third_parties ALREADY collected by the web
    axis. One record per body whose homepage already loads a recognised portal
    host — no new request."""
    out = []
    for r in records:
        dom = r.get("domain")
        if not dom:
            continue
        for t in r.get("third_parties") or []:
            host = (t.get("domain") or "")
            vendor = fingerprint(host)
            if vendor:
                out.append(_record(r.get("kommune"), dom, vendor, host, date,
                                   evidence="web-axis"))
                break
    return out


def probe_entity(name, domain, url, fetch, can_fetch=lambda u: True,
                 paths=INNSYN_PATHS, date=None):
    """PROBE one body's innsyn/postliste paths for a portal fingerprint. Honors
    robots (can_fetch), and BACKS OFF on the first 403 — we do not hammer a
    vendor that bot-blocks us (issue #61 §1). Returns an auto record or None."""
    base = (url or "").rstrip("/")
    if not base:
        return None
    for path in paths:
        target = base + path
        if not can_fetch(target):
            continue
        status, html = fetch(target)
        if status == 403:
            return None   # backoff: vendor bot-block — stop probing this body
        if status != 200 or not html:
            continue
        for host in link_hosts(html):
            vendor = fingerprint(host)
            if vendor:
                return _record(name, domain, vendor, host, date,
                               evidence="probe:" + path)
    return None


def http_fetch(url):
    """One polite GET. Returns (status, html) or (None, '') on network error.
    Body capped — we only scan links."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(600_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def robots_checker(base_url, fetch=http_fetch):
    """A can_fetch(url) for a site's robots.txt. Fail-open on an unreadable
    robots (the norm is 'allowed'), fail-closed only on an explicit disallow."""
    host = _host_of(base_url)
    rp = urllib.robotparser.RobotFileParser()
    status, text = fetch("https://" + host + "/robots.txt") if host else (None, "")
    if status == 200 and text:
        rp.parse(text.splitlines())
        return lambda u: rp.can_fetch(UA, u)
    return lambda u: True


def probe_all(records, covered, fetch, cache, date, sleep=time.sleep,
              can_fetch_for=None):
    """PROBE pass across every body the zero-cost pass missed. Cached across runs
    (a cached body is not re-fetched) and rate-limited between bodies. Returns the
    list of freshly resolved auto records."""
    out = []
    for r in records:
        dom = r.get("domain")
        if not dom or dom in covered:
            continue
        cached = cache.get(dom)
        if cached and cached.get("vendor"):
            out.append(_record(r.get("kommune"), dom, cached["vendor"],
                               cached.get("vendor_host", ""), cached["date"],
                               evidence="probe:cache"))
            continue
        sleep(PROBE_DELAY)     # polite: a real delay before every network probe
        can_fetch = can_fetch_for(r.get("url")) if can_fetch_for else (lambda u: True)
        rec = probe_entity(r.get("kommune"), dom, r.get("url"), fetch,
                           can_fetch=can_fetch, date=date)
        cache[dom] = ({"date": date, "vendor": rec["vendor"],
                       "vendor_host": rec["vendor_host"]} if rec
                      else {"date": date, "vendor": None})
        if rec:
            out.append(rec)
    return out


def build_dataset(records, date, extra=None):
    """Assemble the published auto dataset from the zero-cost records (+ any probe
    records in `extra`), with an honest coverage tally. Vendor identification
    only — flagged as such; no hosting jurisdiction is ever asserted."""
    recs = mine_web_axis(records, date) + list(extra or [])
    recs.sort(key=lambda r: (r.get("kommune") or "", r["domain"]))
    return {
        "meta": {
            "title": "Norwegian public-body sakarkiv vendor — innsyn-portal fingerprint",
            "generated": date,
            "method": "portal-fingerprint",
            "license": "CC BY 4.0",
            "attribution": "Skytilsynet / BetterWorld, skytilsynet.no",
            "note": ("Vendor identification ONLY, from the public innsyn/postliste "
                     "portal host — never a hosting-jurisdiction claim. Merged "
                     "UNDER the human-curated data/saksbehandling.csv (manual/FOI "
                     "wins). See scanner/saksarkiv_probe.py."),
            "coverage": {"fingerprinted": len(recs), "total": len(records)},
        },
        "records": recs,
    }


def main():
    date = os.environ.get("SCAN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    web = json.load(open(WEB_DATA))
    records = web["kommuner"]

    zero = mine_web_axis(records, date)
    covered = {r["domain"] for r in zero}
    print(f"[{date}] zero-cost pass: {len(zero)} of {len(records)} bodies "
          "fingerprinted from the existing web axis (no new request).",
          file=sys.stderr)

    extra = []
    if os.environ.get("SAK_PROBE"):
        cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
        print(f"[{date}] probing {len(records) - len(covered)} remaining bodies "
              "(/innsyn + /postliste, robots-aware, rate-limited, 403-backoff)…",
              file=sys.stderr)
        extra = probe_all(records, covered, http_fetch, cache, date,
                          can_fetch_for=robots_checker)
        json.dump(cache, open(CACHE, "w"), ensure_ascii=False, indent=2)
        print(f"[{date}] probe pass: +{len(extra)} bodies fingerprinted.",
              file=sys.stderr)

    dataset = build_dataset(records, date, extra=extra)
    json.dump(dataset, open(OUT, "w"), ensure_ascii=False, indent=2)
    cov = dataset["meta"]["coverage"]
    print(f"Wrote {OUT}: {cov['fingerprinted']} of {cov['total']} bodies "
          "→ sakarkiv vendor via portal fingerprint.")


if __name__ == "__main__":
    main()
