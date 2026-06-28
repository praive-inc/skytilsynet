#!/usr/bin/env python3
"""
Norway kommune email-sovereignty scan (BetterWorld / Skytilsynet).

Classifies every Norwegian municipality's email PLATFORM from three public DNS
signals (strongest last): MX records, SPF (TXT, with one level of gateway
include-expansion), and the autodiscover CNAME fingerprint
(autodiscover.<domain> -> autodiscover.outlook.com = Microsoft 365 tenancy).

Each record carries its EVIDENCE (the actual MX/SPF/autodiscover records), the
jurisdiction the platform answers to, the recommended European alternative, and
sovereignty-washing flags (EU-located != EU-owned; gateway-fronted backends we
could not unmask -> the Microsoft share is a FLOOR, not a ceiling). This is the
factual-over-moralizing discipline (CLAUDE.md rule 1, scorecard-spec §2): never a
number without its source.

Website != mail domain for a handful of kommuner. We resolve the real mail domain
by probing candidate domains (the website, its parents, and <slug>.kommune.no)
plus a small curated override map for the unguessable vanity domains.

History: each run writes a dated snapshot under snapshots/, appends an aggregate
row to history.json, and refreshes the published CC-BY dataset in ../data/.
transition.py diffs two snapshots to list exactly which municipalities moved.

Run:  python3 scan.py                       # dated today (UTC)
      SCAN_DATE=2026-06-27 python3 scan.py   # pin the snapshot date
Needs: dig. Reads kommuner_wikidata.json (Wikidata SPARQL dump). Zero cost, no auth.
"""
import ipaddress, json, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(HERE, "kommuner_wikidata.json")
OVERRIDES_FILE = os.path.join(HERE, "mail_domain_overrides.json")
SNAP_DIR = os.path.join(HERE, "snapshots")
HISTORY  = os.path.join(HERE, "history.json")
LATEST   = os.path.join(HERE, "kommune_sovereignty.json")
DATASET  = os.path.join(HERE, os.pardir, "data", "kommune-email-sovereignty.latest.json")

MICROSOFT = ("mail.protection.outlook.com", "spf.protection.outlook.com",
             "outlook.com", "microsoft.com", "office365.us", "mx.microsoft")
GOOGLE    = ("aspmx.l.google.com", "google.com", "googlemail.com",
             "_spf.google.com", "googlehosted.com")
EU_SOVEREIGN = ("proton.me", "protonmail.ch", "pm.me", "mailbox.org",
                "ovh.net", "ovh.com", "open-xchange", "ox.io", "ionos",
                "hetzner", "scaleway", "runbox.com", "tutanota", "tuta.com",
                "domeneshop.no")
GATEWAYS = ("mimecast", "proofpoint", "pphosted", "messagelabs",
            "barracudanetworks", "trendmicro", "fireeyecloud", "cisco", "iphmx")

# Microsoft Exchange Online Protection (EOP) outbound IPv4 ranges. A flattened SPF
# (e.g. powerspf.com) drops the spf.protection.outlook.com include and inlines
# these raw IPs — the exact reason Alvdal was missed by the hostname check. Static
# prefix list; refresh periodically from https://endpoints.office.com (id 'Exchange').
MS_EOP_RANGES = [ipaddress.ip_network(c) for c in (
    "40.92.0.0/15", "40.107.0.0/16", "52.100.0.0/15",
    "52.101.0.0/16", "52.102.0.0/16", "52.103.0.0/16", "104.47.0.0/17")]

# The jurisdiction each US platform answers to, plus the recommended European
# switch target (scorecard-spec §3). Same engine ownership graph BetterWorld owns;
# here we only label what the DNS proves.
US_JURISDICTION = "United States (CLOUD Act)"
OPENDESK = "openDesk (Open-Xchange + Nextcloud) / LibreOffice"
PLATFORM_META = {
    "US_MICROSOFT": (US_JURISDICTION, OPENDESK),
    "US_GOOGLE":    (US_JURISDICTION, OPENDESK),
    "US_MIXED":     (US_JURISDICTION, OPENDESK),
    "EU_SOVEREIGN": (None, None),   # jurisdiction comes from the provider below
    "OTHER":        ("Undetermined", None),
    "NONE":         ("Undetermined", None),
}

# Non-US mail providers we recognise, with the jurisdiction their OWNER answers
# to — NOT merely where the servers sit. EU-located != EU-owned is the credibility
# moat (scorecard-spec §3): a naive "is it European?" check would miss that Proton
# is Swiss (outside EU law) and OnlyOffice is Russian-origin. Ordered; first
# substring hit in the DNS evidence wins. Each entry: (substr, jurisdiction, flags).
PROVIDERS = [
    ("onlyoffice",   "Russia (origin)",       ["russian_origin"]),
    ("myoffice",     "Russia (origin)",       ["russian_origin"]),
    ("proton.me",    "Switzerland (non-EU)",  ["non_eu_jurisdiction"]),
    ("protonmail.ch","Switzerland (non-EU)",  ["non_eu_jurisdiction"]),
    ("pm.me",        "Switzerland (non-EU)",  ["non_eu_jurisdiction"]),
    ("mailbox.org",  "Germany (EU)",          []),
    ("open-xchange", "Germany (EU)",          []),
    ("ox.io",        "Germany (EU)",          []),
    ("ionos",        "Germany (EU)",          []),
    ("hetzner",      "Germany (EU)",          []),
    ("tutanota",     "Germany (EU)",          []),
    ("tuta.com",     "Germany (EU)",          []),
    ("ovh",          "France (EU)",           []),
    ("scaleway",     "France (EU)",           []),
    ("runbox.com",   "Norway (EEA)",          []),
    ("domeneshop",   "Norway (EEA)",          []),
    ("webhuset",     "Norway (EEA)",          []),
    ("bedsys",       "Norway (EEA)",          []),
    ("hedmark-ikt",  "Norway (EEA)",          []),
]

# Parent domains we must never probe when walking up a host: shared category
# apexes would return a foreign kommune's (or no) mail records.
CATEGORY_DOMAINS = {"kommune.no", "fylke.no", "herad.no", "suohkan.no",
                    "gov.no", "no"}


def dig(name, rtype):
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=2", rtype, name],
                             capture_output=True, text=True, timeout=12)
        return [l.strip().lower().rstrip(".") for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def spf_text(domain, dig=dig):
    txts = dig(domain, "TXT")
    spf = " ".join(t for t in txts if "v=spf1" in t)
    for inc in re.findall(r"include:([a-z0-9._-]+)", spf)[:4]:
        if any(g in inc for g in GATEWAYS) or "spf" in inc:
            spf += " " + " ".join(dig(inc, "TXT"))
    return spf


def fetch(domain, dig=dig):
    """Resolve the three DNS signals for a domain into one evidence dict."""
    mx = dig(domain, "MX")
    mx_hosts = " ".join(re.sub(r"^\d+\s+", "", m) for m in mx)
    spf = spf_text(domain, dig)
    auto = " ".join(dig(f"autodiscover.{domain}", "CNAME"))
    return {"mx": mx, "mx_hosts": mx_hosts.strip(), "spf": spf, "autodiscover": auto}


def spf_ms_ip_match(spf):
    """Return the first ip4: token in the SPF that falls inside an MS EOP range,
    else None. Catches flattened SPF that inlines MS IPs instead of the include."""
    for tok in re.findall(r"ip4:([0-9.]+(?:/\d+)?)", spf):
        try:
            net = ipaddress.ip_network(tok, strict=False)
        except ValueError:
            continue
        if any(net.overlaps(r) for r in MS_EOP_RANGES):
            return tok
    return None


def dkim_probe(domain, dig=dig):
    """DKIM selectors as a backend fingerprint (DNS, no auth). M365 publishes
    selector1/2._domainkey CNAMEs into *.onmicrosoft.com; Google Workspace
    publishes a google._domainkey TXT."""
    cnames = []
    for sel in ("selector1", "selector2"):
        cnames += dig(f"{sel}._domainkey.{domain}", "CNAME")
    google = bool(dig(f"google._domainkey.{domain}", "TXT"))
    return {"dkim": " ".join(cnames), "dkim_google": google}


def getuserrealm(domain, opener=urllib.request.urlopen):
    """Azure AD realm via one no-auth HTTPS GET. <NameSpaceType> is Managed (cloud
    M365 tenant), Federated (domain federated into Azure AD — tenant exists), or
    Unknown (no tenant). Returns the type string, or None on any network error."""
    url = ("https://login.microsoftonline.com/getuserrealm.srf"
           f"?login=test@{domain}&xml=1")
    try:
        with opener(url, timeout=8) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"<NameSpaceType>(\w+)</NameSpaceType>", body)
    return m.group(1) if m else None


def deep_probe(domain, dig=dig, opener=urllib.request.urlopen):
    """The no-auth unmasking signals run ONLY for gateway/co-op/None domains: the
    DKIM selectors plus the Azure AD realm. One DNS triple + one HTTPS GET."""
    out = dkim_probe(domain, dig)
    out["realm"] = getuserrealm(domain, opener)
    return out


def classify_evidence(ev):
    """Pure: evidence dict -> (platform, fingerprint). No network.

    A bare "v=spf1 -all" with no MX and no autodiscover is a null-sending record
    (the domain explicitly sends no mail), so it counts as no signal -> NONE.

    Beyond the MX/SPF-hostname/autodiscover signals, three deep signals (present
    only after deep_probe runs on a masked domain) unmask gateway/co-op backends:
    a DKIM CNAME into *.onmicrosoft.com, an SPF ip4 inside an MS EOP range, or
    getuserrealm=Managed are each AIRTIGHT Microsoft; getuserrealm=Federated proves
    an M365 tenant exists (email inferred high-confidence -> flagged 'federated',
    not merged into hard M365); a google._domainkey TXT is Google Workspace.
    """
    mx_hosts, spf, auto = ev["mx_hosts"], ev["spf"], ev["autodiscover"]
    dkim, realm = ev.get("dkim", ""), ev.get("realm")
    blob = (mx_hosts + " " + spf + " " + auto).lower()
    ms  = any(s in blob for s in MICROSOFT)
    goo = any(s in blob for s in GOOGLE)
    eup = any(s in blob for s in EU_SOVEREIGN)
    dkim_ms   = "onmicrosoft.com" in dkim.lower()
    dkim_goo  = bool(ev.get("dkim_google"))
    spf_ip    = spf_ms_ip_match(spf)
    ms_hard   = ms or dkim_ms or bool(spf_ip) or realm == "Managed"
    goo_hard  = goo or dkim_goo
    federated = realm == "Federated"
    has_signal = bool(ev["mx"] or auto or ms_hard or goo_hard or eup or federated)
    if not has_signal:             platform = "NONE"
    elif ms_hard and goo_hard:     platform = "US_MIXED"
    elif ms_hard:                  platform = "US_MICROSOFT"
    elif goo_hard:                 platform = "US_GOOGLE"
    elif federated:                platform = "US_MICROSOFT"
    elif eup:                      platform = "EU_SOVEREIGN"
    else:                          platform = "OTHER"
    fp = None
    if platform in ("US_MICROSOFT", "US_MIXED"):
        if ms:
            only_auto = ("outlook.com" in auto) and not any(
                s in (mx_hosts + " " + spf) for s in MICROSOFT)
            fp = "autodiscover" if only_auto else "mx/spf"
        elif dkim_ms:            fp = "dkim"
        elif spf_ip:             fp = "spf-ms-ip"
        elif realm == "Managed": fp = "realm-managed"
        elif federated:          fp = "realm-federated"
    elif platform == "US_GOOGLE":
        fp = "mx/spf" if goo else "dkim-google"
    return platform, fp


def provider_jurisdiction(ev):
    """First recognised non-US provider in the evidence -> (jurisdiction, flags)."""
    blob = (ev["mx_hosts"] + " " + ev["spf"] + " " + ev["autodiscover"]).lower()
    for substr, jurisdiction, flags in PROVIDERS:
        if substr in blob:
            return jurisdiction, list(flags)
    return None, []


def slugify(name):
    """Kommune label -> bare .no mail-domain slug (drop ' kommune', fold æøå)."""
    s = name.lower().replace(" kommune", "").strip().replace(" ", "-")
    return s.replace("æ", "ae").replace("ø", "o").replace("å", "a")


def candidates(name, website_domain, overrides):
    """Ordered mail-domain candidates. A curated override is authoritative and
    used alone; otherwise probe the website, then its parents, then slug.kommune.no."""
    if name in overrides:
        return [overrides[name]]
    out = []
    if website_domain:
        out.append(website_domain)
        parts = website_domain.split(".")
        while len(parts) > 2:
            parts = parts[1:]
            d = ".".join(parts)
            if d not in CATEGORY_DOMAINS and d not in out:
                out.append(d)
    slug = slugify(name) + ".kommune.no"
    if slug not in out and slug not in CATEGORY_DOMAINS:
        out.append(slug)
    return out


def make_record(name, website_domain, domain, ev, date):
    """Assemble the published per-kommune record: platform + jurisdiction +
    evidence + recommended alternative + sovereignty-washing flags + sourceDate."""
    platform, fp = classify_evidence(ev)
    behind_gateway = any(g in ev["mx_hosts"] for g in GATEWAYS)
    jurisdiction, alternative = PLATFORM_META[platform]
    prov_jur, flags = provider_jurisdiction(ev)
    if jurisdiction is None:                 # EU_SOVEREIGN / not-yet-known
        jurisdiction = prov_jur or "Undetermined"
    # Sovereignty-washing flags (scorecard-spec §3):
    if fp == "realm-federated":
        # Tenant proven by federation, not a managed cloud tenant: email is
        # inferred high-confidence, so qualify it rather than overclaim.
        flags.append("federated")
    if platform == "OTHER" and behind_gateway:
        # A mail-security gateway masks the real backend; the deep probe did not
        # unmask it either -> the MS share is a floor, not a ceiling.
        flags.append("backend_unmasked")
    if website_domain and domain != website_domain:
        flags.append("mail_domain_differs_from_website")
    return {
        "kommune": name,
        "domain": domain,
        "website_domain": website_domain,
        "platform": platform,
        "jurisdiction": jurisdiction,
        "alternative": alternative,
        "behind_gateway": behind_gateway,
        "fingerprint": fp,
        "flags": flags,
        "evidence": {
            "mx": ev["mx"],
            "spf": ev["spf"] or None,
            "autodiscover": ev["autodiscover"] or None,
            "dkim": ev.get("dkim") or None,
            "spf_ms_ip": spf_ms_ip_match(ev["spf"]),
            "realm": ev.get("realm"),
        },
        "sourceDate": date,
    }


def resolve(name, website_domain, overrides, date, fetch=fetch, deep=deep_probe):
    """Walk candidate domains; keep the first that yields a real mail signal.
    Falls back to the website domain's (NONE) evidence if nothing resolves.

    When the base DNS signals leave a domain masked (OTHER, e.g. a mail-security
    gateway or a regional IKT co-op) or unresolved (NONE), run the no-auth deep
    probe (DKIM + getuserrealm) on the chosen domain and reclassify. The HTTPS
    realm GET therefore fires only for the handful of masked domains, not all 358."""
    chosen = None
    for d in candidates(name, website_domain, overrides):
        ev = fetch(d)
        platform, _ = classify_evidence(ev)
        if platform != "NONE":
            chosen = (d, ev); break
        if chosen is None:
            chosen = (d, ev)                 # remember the first probe as fallback
    d, ev = chosen
    if classify_evidence(ev)[0] in ("OTHER", "NONE"):
        ev = {**ev, **deep(d)}               # unmask gateway/co-op/None backend
    return make_record(name, website_domain, d, ev, date)


def domain_of(url):
    if not url: return None
    from urllib.parse import urlparse
    host = urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    return (host[4:] if host.startswith("www.") else host) or None


def aggregate(results):
    c = Counter(r["platform"] for r in results); total = len(results)
    us = c["US_MICROSOFT"] + c["US_GOOGLE"] + c["US_MIXED"]
    unmasked = sum(1 for r in results if "backend_unmasked" in r.get("flags", []))
    federated = sum(1 for r in results if "federated" in r.get("flags", []))
    return {
        "total": total,
        "us_microsoft": c["US_MICROSOFT"], "us_google": c["US_GOOGLE"],
        "us_mixed": c["US_MIXED"], "eu_sovereign": c["EU_SOVEREIGN"],
        "other": c["OTHER"], "none": c["NONE"],
        "us_total": us, "us_pct": round(100 * us / total, 1) if total else 0.0,
        "microsoft_pct": round(100 * c["US_MICROSOFT"] / total, 1) if total else 0.0,
        "federated": federated,
        "backend_unmasked": unmasked,
        "floor_note": (
            f"microsoft_pct and us_pct are a FLOOR, not a ceiling: {unmasked} "
            "domain(s) sit behind a mail-security gateway whose backend SPF did "
            "not unmask — some are likely Microsoft too. The true US share is >= "
            "the figure shown."
        ),
    }


def write_dataset(date, agg, results):
    """Refresh the published CC-BY dataset consumed by the public site."""
    dataset = {
        "meta": {
            "title": "Norwegian municipality email-platform sovereignty",
            "source": "public DNS (MX + SPF + autodiscover CNAME)",
            "sourceDate": date,
            "license": "CC BY 4.0",
            "attribution": "Skytilsynet / BetterWorld, skytilsynet.no",
            "method": "See ../docs/scorecard-spec.md §2 and scanner/README.md.",
            "floor_note": agg["floor_note"],
        },
        "summary": agg,
        "kommuner": results,
    }
    json.dump(dataset, open(DATASET, "w"), ensure_ascii=False, indent=2)


def main():
    date = os.environ.get("SCAN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = json.load(open(SRC))
    overrides = json.load(open(OVERRIDES_FILE)) if os.path.exists(OVERRIDES_FILE) else {}
    # Key by the Wikidata item URI: it is the stable, unique municipality identity.
    # Keying by name would wrongly collapse the two distinct Vålers and two Herøys
    # (same name, different kommuner); keying by domain would merge any that share
    # one. Bergen appears twice in the dump — first binding wins.
    by_item = {}
    for b in data["results"]["bindings"]:
        item = b["item"]["value"]
        if item not in by_item:
            by_item[item] = (b.get("itemLabel", {}).get("value", "?"),
                             domain_of(b.get("website", {}).get("value")))
    print(f"[{date}] resolving {len(by_item)} kommune mail domains "
          "(MX+SPF+autodiscover, with mail-domain fallback)…", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = [ex.submit(resolve, n, d, overrides, date)
                for n, d in by_item.values()]
        for f in futs:
            results.append(f.result())
    results.sort(key=lambda r: (r["platform"], r["kommune"]))

    agg = aggregate(results)
    os.makedirs(SNAP_DIR, exist_ok=True)
    json.dump({"date": date, "summary": agg, "kommuner": results},
              open(os.path.join(SNAP_DIR, f"{date}.json"), "w"), ensure_ascii=False, indent=2)
    json.dump(results, open(LATEST, "w"), ensure_ascii=False, indent=2)
    write_dataset(date, agg, results)

    history = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    history = [h for h in history if h["date"] != date]      # idempotent re-run
    history.append({"date": date, **{k: v for k, v in agg.items() if k != "floor_note"}})
    history.sort(key=lambda h: h["date"])
    json.dump(history, open(HISTORY, "w"), ensure_ascii=False, indent=2)

    print(f"\n=== Skytilsynet — {agg['total']} kommuner — {date} ===")
    for k, lab in [("us_microsoft","Microsoft 365"),("us_google","Google Workspace"),
                   ("us_mixed","US mixed"),("eu_sovereign","EU-sovereign"),
                   ("other","Other / regional"),("none","Unresolved")]:
        n = agg[k]; print(f"  {lab:18}{n:4}  {100*n/agg['total']:5.1f}%  {'█'*round(40*n/agg['total'])}")
    print(f"\n  Microsoft 365: {agg['microsoft_pct']}% (floor)  ·  US hyperscaler: {agg['us_pct']}% (floor)")
    print(f"  {agg['federated']} of the Microsoft rows are federated (tenant proven, email inferred).")
    print(f"  {agg['backend_unmasked']} gateway-fronted backend(s) still unmasked → MS share is a floor.")
    print(f"  snapshot → snapshots/{date}.json  ·  dataset → data/  ·  history ({len(history)} run(s))")


if __name__ == "__main__":
    main()
