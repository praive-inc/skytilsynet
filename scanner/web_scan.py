#!/usr/bin/env python3
"""
Norway kommune WEB-infrastructure sovereignty scan (BetterWorld / Skytilsynet).

A SECOND, DISTINCT axis from the email scan (scan.py): where does a municipality's
public WEBSITE infrastructure answer to? Derived entirely from public, no-auth
signals on the public homepage + public DNS — never an intrusion, just what a
browser already fetches:

  1. HTTP response headers — Server, X-Powered-By, Content-Security-Policy.
  2. Embedded third-party resources — every external host in the homepage's
     <script>/<link>/<img>/<iframe> (Google Analytics/Tag Manager/Fonts, US CDNs,
     map tiles, social trackers) classified to the jurisdiction it answers to.
  3. Hosting IP -> ASN/country via Team Cymru DNS (origin.asn.cymru.com TXT,
     no key), turned into a hosting jurisdiction.
  4. TLS certificate issuer (openssl s_client) and /.well-known/security.txt.

Output per kommune: hosting jurisdiction, the per-resource jurisdiction list, the
fraction of embedded resources that are US-hosted, and an analytics y/n — kept as
its OWN axis, NOT conflated with the email score. Each record carries its evidence
(the actual headers/resources/ASN) + sourceDate: factual-over-moralizing, never a
claim without its source (CLAUDE.md rule 1).

Polite by design: ONE homepage GET + one security.txt GET per kommune, a low
worker count, short timeouts, an identifying User-Agent. Public homepage + public
DNS only — no crawl, no auth, no cost.

Run:  python3 web_scan.py                       # dated today (UTC)
      SCAN_DATE=2026-06-27 python3 web_scan.py   # pin the snapshot date
Needs: dig, openssl. Reads kommuner_wikidata.json (same Wikidata dump as scan.py).
"""
import ipaddress, json, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(HERE, "kommuner_wikidata.json")
SNAP_DIR = os.path.join(HERE, "snapshots")
HISTORY  = os.path.join(HERE, "web_history.json")
LATEST   = os.path.join(HERE, "kommune_web_sovereignty.json")
DATASET  = os.path.join(HERE, os.pardir, "data", "kommune-web-sovereignty.latest.json")

UA = "SkytilsynetBot/1.0 (+https://skytilsynet.no; civic transparency scan)"
US_JURISDICTION = "United States (CLOUD Act)"

# Embedded third-party services we recognise, mapped to the jurisdiction their
# OWNER answers to (EU-located != EU-owned — same credibility moat as the email
# PROVIDERS table). Ordered: the FIRST substring hit wins, so more specific hosts
# (fonts.googleapis.com) MUST precede generic ones (googleapis.com). Anything not
# matched is "other"/Undetermined — we never guess a jurisdiction we can't cite.
RESOURCE_PROVIDERS = [
    # analytics / trackers — flagged so the analytics signal can be derived
    ("google-analytics.com", "analytics",      US_JURISDICTION, ["analytics"]),
    ("googletagmanager.com", "tag-manager",     US_JURISDICTION, ["analytics"]),
    ("doubleclick.net",      "ad-tracker",      US_JURISDICTION, ["tracker"]),
    ("connect.facebook.net", "social-tracker",  US_JURISDICTION, ["tracker"]),
    ("facebook.net",         "social-tracker",  US_JURISDICTION, ["tracker"]),
    ("facebook.com",         "social-tracker",  US_JURISDICTION, ["tracker"]),
    ("addthis.com",          "social-tracker",  US_JURISDICTION, ["tracker"]),
    ("sharethis.com",        "social-tracker",  US_JURISDICTION, ["tracker"]),
    ("hotjar.com",           "analytics",       "Malta (EU)",    ["analytics"]),
    ("matomo",               "analytics",       "Undetermined",  ["analytics"]),
    # fonts
    ("fonts.googleapis.com", "fonts",           US_JURISDICTION, []),
    ("fonts.gstatic.com",    "fonts",           US_JURISDICTION, []),
    ("use.typekit.net",      "fonts",           US_JURISDICTION, []),
    ("fontawesome.com",      "fonts",           US_JURISDICTION, []),
    # maps / tiles
    ("maps.googleapis.com",  "maps",            US_JURISDICTION, []),
    ("maps.gstatic.com",     "maps",            US_JURISDICTION, []),
    ("mapbox.com",           "maps",            US_JURISDICTION, []),
    ("openstreetmap.org",    "maps",            "United Kingdom (non-EU)", []),
    # video / social embeds
    ("youtube-nocookie.com", "video",           US_JURISDICTION, []),
    ("youtube.com",          "video",           US_JURISDICTION, []),
    ("ytimg.com",            "video",           US_JURISDICTION, []),
    ("vimeo.com",            "video",           US_JURISDICTION, []),
    ("linkedin.com",         "social-tracker",  US_JURISDICTION, ["tracker"]),
    ("twitter.com",          "social-tracker",  US_JURISDICTION, ["tracker"]),
    # CDNs / generic Google / cloud asset hosts
    ("ajax.googleapis.com",  "cdn",             US_JURISDICTION, []),
    ("googleapis.com",       "google-api",      US_JURISDICTION, []),
    ("gstatic.com",          "google-api",      US_JURISDICTION, []),
    ("gravatar.com",         "avatar",          US_JURISDICTION, []),
    ("wp.com",               "cdn",             US_JURISDICTION, []),
    ("cdnjs.cloudflare.com", "cdn",             US_JURISDICTION, []),
    ("cloudflare.com",       "cdn",             US_JURISDICTION, []),
    ("cloudfront.net",       "cdn",             US_JURISDICTION, []),
    ("amazonaws.com",        "hosting",         US_JURISDICTION, []),
    ("akamaihd.net",         "cdn",             US_JURISDICTION, []),
    ("akamai.net",           "cdn",             US_JURISDICTION, []),
    ("fastly.net",           "cdn",             US_JURISDICTION, []),
    ("azureedge.net",        "cdn",             US_JURISDICTION, []),
    ("bootstrapcdn.com",     "cdn",             US_JURISDICTION, []),
    ("unpkg.com",            "cdn",             US_JURISDICTION, []),
    ("jsdelivr.net",         "cdn",             "Undetermined",  []),
]

# Country code -> jurisdiction the data answers to. EU-located != EU-owned: a US
# country code carries the CLOUD-Act qualifier; UK/CH are flagged non-EU.
EU_MEMBERS = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
              "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
              "RO", "SK", "SI", "ES", "SE"}
EEA_EXTRA = {"NO", "IS", "LI"}


def dig(name, rtype):
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=2", rtype, name],
                             capture_output=True, text=True, timeout=12)
        return [l.strip().lower() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def http_get(url):
    """One polite GET. Returns (status, lowercased-header-dict, body) or
    (None, {}, '') on any network error. Body capped — we only parse the <head>."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(600_000).decode("utf-8", "replace")
            headers = {k.lower(): v for k, v in r.headers.items()}
            return r.status, headers, body
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, ""
    except Exception:
        return None, {}, ""


def host_of(url):
    """URL (absolute or protocol-relative) -> bare host, else None. Relative paths
    and data: URIs have no host."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        return None
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return host or None


def third_party_domains(html, site_host):
    """Distinct EXTERNAL resource hosts referenced from the homepage's
    src/href attributes. Same-site (the host itself or any subdomain of it, after
    dropping a leading www.) is excluded — so static.x.kommune.no is internal but
    another kommune's domain, or a US CDN, is external."""
    site = site_host[4:] if site_host.startswith("www.") else site_host
    out = set()
    for m in re.finditer(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html, re.I):
        host = host_of(m.group(1))
        if not host:
            continue
        if site and (host == site or host.endswith("." + site)):
            continue
        out.add(host)
    return sorted(out)


def classify_resource(domain):
    """Resource host -> (category, jurisdiction, flags). First substring hit in the
    ordered table wins; unknown hosts are ('other', 'Undetermined', [])."""
    d = domain.lower()
    for substr, category, jurisdiction, flags in RESOURCE_PROVIDERS:
        if substr in d:
            return category, jurisdiction, list(flags)
    return "other", "Undetermined", []


def country_jurisdiction(cc):
    """ISO country code -> jurisdiction string (factual, citable)."""
    if not cc:
        return "Undetermined"
    cc = cc.upper()
    if cc == "US":
        return US_JURISDICTION
    if cc in EU_MEMBERS:
        return f"{cc} (EU)"
    if cc in EEA_EXTRA:
        return f"{cc} (EEA)"
    if cc == "GB":
        return "United Kingdom (non-EU)"
    if cc == "CH":
        return "Switzerland (non-EU)"
    return cc


def _cymru_fields(txt):
    """First Team Cymru TXT record -> the '|'-split, quote-stripped fields."""
    if not txt:
        return []
    return [p.strip().strip('"') for p in txt[0].strip('"').split("|")]


def asn_lookup(ip, dig=dig):
    """IPv4 -> origin ASN + country + ASN name via Team Cymru DNS (no key). The
    origin record is 'ASN | prefix | CC | registry | date'; the ASN record's last
    field is the org name."""
    blank = {"ip": ip, "asn": None, "country": None, "name": None}
    rev = ".".join(reversed(ip.split(".")))
    fields = _cymru_fields(dig(f"{rev}.origin.asn.cymru.com", "TXT"))
    if not fields or not fields[0]:
        return blank
    asn = fields[0].split()[0]
    country = fields[2].upper() if len(fields) > 2 and fields[2] else None
    name_fields = _cymru_fields(dig(f"as{asn}.asn.cymru.com", "TXT"))
    name = name_fields[-1] if name_fields else None
    return {"ip": ip, "asn": asn, "country": country, "name": name}


def tls_issuer(host, runner=subprocess.run):
    """TLS certificate issuer organisation (O=...) via openssl s_client. Best-effort
    evidence — None on any error."""
    try:
        out = runner(["openssl", "s_client", "-connect", f"{host}:443",
                      "-servername", host, "-verify_quiet"],
                     input="", capture_output=True, text=True, timeout=12)
    except Exception:
        return None
    m = re.search(r"^issuer=.*?\bO\s*=\s*([^,/\n]+)", out.stdout, re.M)
    return m.group(1).strip() if m else None


def _hosting(host, dig):
    """Resolve the homepage host's first IPv4 -> hosting jurisdiction evidence."""
    for ip in dig(host, "A"):
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        info = asn_lookup(ip, dig)
        info["jurisdiction"] = country_jurisdiction(info["country"])
        return info
    return {"ip": None, "asn": None, "country": None, "name": None,
            "jurisdiction": "Undetermined"}


def build_record(name, url, headers, html, hosting, issuer, security_txt, date):
    """Assemble one per-kommune web-sovereignty record from already-fetched signals.
    Pure: no network. Derives the third-party jurisdictions, the US-resource
    fraction, the analytics signal and the washing flags."""
    site_host = host_of(url) or ""
    third = []
    for d in third_party_domains(html, site_host):
        cat, jur, flags = classify_resource(d)
        third.append({"domain": d, "category": cat, "jurisdiction": jur, "flags": flags})
    us_n = sum(1 for t in third if t["jurisdiction"] == US_JURISDICTION)
    fraction = round(us_n / len(third), 3) if third else 0.0
    analytics = any("analytics" in t["flags"] for t in third)

    flags = []
    if hosting.get("jurisdiction") == US_JURISDICTION:
        flags.append("us_hosted")
    if analytics:
        flags.append("analytics")
    if any(t["category"] == "cdn" and t["jurisdiction"] == US_JURISDICTION for t in third):
        flags.append("us_cdn")
    if any("tracker" in t["flags"] for t in third):
        flags.append("third_party_trackers")
    return {
        "kommune": name,
        "axis": "web",
        "url": url,
        "host": site_host or None,
        "hosting": hosting,
        "third_parties": third,
        "us_resource_fraction": fraction,
        "analytics": analytics,
        "flags": flags,
        "evidence": {
            "server": headers.get("server"),
            "x_powered_by": headers.get("x-powered-by"),
            "csp": headers.get("content-security-policy") or headers.get("csp"),
            "tls_issuer": issuer,
            "security_txt": security_txt,
        },
        "sourceDate": date,
    }


def scan_one(name, url, date, http_get=http_get, dig=dig, tls=tls_issuer):
    """Fetch the public homepage + public DNS for one kommune and build its record.
    An unreachable homepage yields a no-signal record flagged 'unreachable'."""
    host = host_of(url) or ""
    status, headers, html = http_get(url)
    hosting = _hosting(host, dig) if host else {"jurisdiction": "Undetermined"}
    issuer = tls(host) if host else None
    sec_url = f"https://{host}/.well-known/security.txt"
    security_txt = http_get(sec_url)[0] == 200 if host else False
    rec = build_record(name, url, headers, html, hosting, issuer, security_txt, date)
    if status is None:
        rec["flags"].append("unreachable")
    return rec


def _error_record(name, url, date, err):
    """A no-signal record for a kommune whose scan raised unexpectedly. Keeps the
    batch going at full scale (one bad homepage never aborts the other 357) and
    records WHY as cited evidence — factual, never a silent drop (CLAUDE.md rule 1)."""
    rec = build_record(name, url, {}, "", {"jurisdiction": "Undetermined"},
                       None, False, date)
    rec["flags"] += ["unreachable", "scan_error"]
    rec["error"] = str(err)
    return rec


def scan_all(entries, date, scan=scan_one, max_workers=8):
    """Scan every (name, url) entry concurrently and resiliently. A low worker
    count keeps the rate polite; per-entity exceptions are turned into flagged
    error records so the full 358-entity run completes even if some homepages
    hang, 5xx, or have a malformed DNS reply."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(scan, n, u, date): (n, u) for n, u in entries}
        for fut in as_completed(futs):
            n, u = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                results.append(_error_record(n, u, date, e))
    return results


def aggregate(results):
    total = len(results)
    us_hosted = sum(1 for r in results if "us_hosted" in r["flags"])
    analytics = sum(1 for r in results if r.get("analytics"))
    us_cdn = sum(1 for r in results if "us_cdn" in r["flags"])
    trackers = sum(1 for r in results if "third_party_trackers" in r["flags"])
    unreachable = sum(1 for r in results if "unreachable" in r["flags"])
    avg = (round(sum(r["us_resource_fraction"] for r in results) / total, 3)
           if total else 0.0)
    return {
        "total": total,
        "us_hosted": us_hosted,
        "us_hosted_pct": round(100 * us_hosted / total, 1) if total else 0.0,
        "analytics": analytics,
        "analytics_pct": round(100 * analytics / total, 1) if total else 0.0,
        "us_cdn": us_cdn,
        "third_party_trackers": trackers,
        "unreachable": unreachable,
        "avg_us_resource_fraction": avg,
        "axis_note": (
            "Distinct axis from the email scan: website infrastructure, not mail. "
            "Hosting jurisdiction is the first homepage IPv4's origin ASN (Team "
            "Cymru); third-party jurisdiction is the owner's, not where the asset "
            "is cached. Unknown resources/ASNs are 'Undetermined', never guessed."
        ),
    }


def write_dataset(date, agg, results):
    dataset = {
        "meta": {
            "title": "Norwegian municipality website-infrastructure sovereignty",
            "source": ("public homepage (HTTP headers + embedded third-party "
                       "resources + TLS issuer + security.txt) and public DNS "
                       "(A record -> Team Cymru origin ASN)"),
            "sourceDate": date,
            "license": "CC BY 4.0",
            "attribution": "Skytilsynet / BetterWorld, skytilsynet.no",
            "method": "See ../docs/scorecard-spec.md and scanner/README.md.",
            "axis_note": agg["axis_note"],
        },
        "summary": agg,
        "kommuner": results,
    }
    json.dump(dataset, open(DATASET, "w"), ensure_ascii=False, indent=2)


def domain_of(url):
    if not url:
        return None
    host = urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    return (host[4:] if host.startswith("www.") else host) or None


def main():
    date = os.environ.get("SCAN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = json.load(open(SRC))
    # Key by Wikidata item URI (stable identity); keep only kommuner with a website.
    by_item = {}
    for b in data["results"]["bindings"]:
        item = b["item"]["value"]
        website = b.get("website", {}).get("value")
        if item not in by_item and website:
            by_item[item] = (b.get("itemLabel", {}).get("value", "?"), website)
    print(f"[{date}] scanning {len(by_item)} kommune homepages "
          "(headers + embedded resources + hosting ASN, public only, polite)…",
          file=sys.stderr)

    results = scan_all(by_item.values(), date, max_workers=8)  # low: polite rate
    results.sort(key=lambda r: (-r["us_resource_fraction"], r["kommune"]))

    agg = aggregate(results)
    os.makedirs(SNAP_DIR, exist_ok=True)
    json.dump({"date": date, "summary": agg, "kommuner": results},
              open(os.path.join(SNAP_DIR, f"web-{date}.json"), "w"),
              ensure_ascii=False, indent=2)
    json.dump(results, open(LATEST, "w"), ensure_ascii=False, indent=2)
    write_dataset(date, agg, results)

    history = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    history = [h for h in history if h["date"] != date]      # idempotent re-run
    history.append({"date": date, **{k: v for k, v in agg.items() if k != "axis_note"}})
    history.sort(key=lambda h: h["date"])
    json.dump(history, open(HISTORY, "w"), ensure_ascii=False, indent=2)

    print(f"\n=== Skytilsynet web axis — {agg['total']} kommuner — {date} ===")
    print(f"  US-hosted homepage:        {agg['us_hosted']:4}  {agg['us_hosted_pct']:5.1f}%")
    print(f"  Analytics/tracker present: {agg['analytics']:4}  {agg['analytics_pct']:5.1f}%")
    print(f"  US CDN embedded:           {agg['us_cdn']:4}")
    print(f"  Avg US-resource fraction:  {agg['avg_us_resource_fraction']}")
    print(f"  {agg['unreachable']} homepage(s) unreachable at scan time.")
    print(f"  snapshot → snapshots/web-{date}.json  ·  dataset → data/  ·  history ({len(history)} run(s))")


if __name__ == "__main__":
    main()
