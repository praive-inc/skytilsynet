#!/usr/bin/env python3
"""
Norway kommune email-sovereignty scan (BetterWorld / Skytilsynet prototype).

Classifies every Norwegian municipality's email PLATFORM from three public DNS
signals (strongest last): MX records, SPF (TXT, with one level of gateway
include-expansion), and the autodiscover CNAME fingerprint
(autodiscover.<domain> -> autodiscover.outlook.com = Microsoft 365 tenancy).

History: each run writes a dated snapshot under snapshots/ and appends an
aggregate row to history.json, so the transition over time is visible (the whole
point of a live tracker — watch kommuner move off foreign cloud). transition.py
diffs two snapshots to list exactly which municipalities changed.

Run:  python3 scan.py                      # dated today (UTC)
      SCAN_DATE=2026-06-27 python3 scan.py  # pin the snapshot date
Needs: dig. Reads kommuner_wikidata.json (Wikidata SPARQL dump). Zero cost, no auth.
"""
import json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(HERE, "kommuner_wikidata.json")
SNAP_DIR = os.path.join(HERE, "snapshots")
HISTORY  = os.path.join(HERE, "history.json")
LATEST   = os.path.join(HERE, "kommune_sovereignty.json")

MICROSOFT = ("mail.protection.outlook.com", "spf.protection.outlook.com",
             "outlook.com", "microsoft.com", "office365.us")
GOOGLE    = ("aspmx.l.google.com", "google.com", "googlemail.com",
             "_spf.google.com", "googlehosted.com")
EU_SOVEREIGN = ("proton.me", "protonmail.ch", "pm.me", "mailbox.org",
                "ovh.net", "ovh.com", "open-xchange", "ox.io", "ionos",
                "hetzner", "scaleway", "runbox.com", "tutanota", "tuta.com",
                "domeneshop.no")
GATEWAYS = ("mimecast", "proofpoint", "pphosted", "messagelabs",
            "barracudanetworks", "trendmicro", "fireeyecloud", "cisco", "iphmx")

def dig(name, rtype):
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=2", rtype, name],
                             capture_output=True, text=True, timeout=12)
        return [l.strip().lower().rstrip(".") for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []

def spf_text(domain):
    txts = dig(domain, "TXT")
    spf = " ".join(t for t in txts if "v=spf1" in t)
    for inc in re.findall(r"include:([a-z0-9._-]+)", spf)[:4]:
        if any(g in inc for g in GATEWAYS) or "spf" in inc:
            spf += " " + " ".join(dig(inc, "TXT"))
    return spf

def classify(domain):
    mx = dig(domain, "MX")
    mx_hosts = " ".join(re.sub(r"^\d+\s+", "", m) for m in mx)
    spf = spf_text(domain)
    auto = " ".join(dig(f"autodiscover.{domain}", "CNAME"))
    blob = (mx_hosts + " " + spf + " " + auto).lower()
    ms  = any(s in blob for s in MICROSOFT)
    goo = any(s in blob for s in GOOGLE)
    eu  = any(s in blob for s in EU_SOVEREIGN)
    if not mx and not spf and not auto:        platform = "NONE"
    elif ms and not goo:                       platform = "US_MICROSOFT"
    elif goo and not ms:                       platform = "US_GOOGLE"
    elif ms and goo:                           platform = "US_MIXED"
    elif eu:                                   platform = "EU_SOVEREIGN"
    else:                                      platform = "OTHER"
    fp = None
    if platform == "US_MICROSOFT":
        only_auto = ("outlook.com" in auto) and not any(s in (mx_hosts + " " + spf) for s in MICROSOFT)
        fp = "autodiscover" if only_auto else "mx/spf"
    return {
        "domain": domain, "platform": platform,
        "mx": mx_hosts.strip() or None,
        "behind_gateway": any(s in mx_hosts for s in GATEWAYS),
        "fingerprint": fp,
    }

def domain_of(url):
    if not url: return None
    from urllib.parse import urlparse
    host = urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    return (host[4:] if host.startswith("www.") else host) or None

def aggregate(results):
    c = Counter(r["platform"] for r in results); total = len(results)
    us = c["US_MICROSOFT"] + c["US_GOOGLE"] + c["US_MIXED"]
    return {
        "total": total,
        "us_microsoft": c["US_MICROSOFT"], "us_google": c["US_GOOGLE"],
        "us_mixed": c["US_MIXED"], "eu_sovereign": c["EU_SOVEREIGN"],
        "other": c["OTHER"], "none": c["NONE"],
        "us_total": us, "us_pct": round(100 * us / total, 1),
        "microsoft_pct": round(100 * c["US_MICROSOFT"] / total, 1),
    }

def main():
    date = os.environ.get("SCAN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = json.load(open(SRC))
    by_domain = {}
    for b in data["results"]["bindings"]:
        name = b.get("itemLabel", {}).get("value", "?")
        dom = domain_of(b.get("website", {}).get("value"))
        if dom and dom not in by_domain:
            by_domain[dom] = name
    print(f"[{date}] resolving {len(by_domain)} kommune domains (MX+SPF+autodiscover)…",
          file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(classify, d): (d, n) for d, n in by_domain.items()}
        for f, (d, n) in futs.items():
            r = f.result(); r["kommune"] = n; results.append(r)
    results.sort(key=lambda r: (r["platform"], r["kommune"]))

    agg = aggregate(results)
    os.makedirs(SNAP_DIR, exist_ok=True)
    json.dump({"date": date, "summary": agg, "kommuner": results},
              open(os.path.join(SNAP_DIR, f"{date}.json"), "w"), ensure_ascii=False, indent=2)
    json.dump(results, open(LATEST, "w"), ensure_ascii=False, indent=2)

    history = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    history = [h for h in history if h["date"] != date]      # idempotent re-run
    history.append({"date": date, **agg})
    history.sort(key=lambda h: h["date"])
    json.dump(history, open(HISTORY, "w"), ensure_ascii=False, indent=2)

    print(f"\n=== Skytilsynet — {agg['total']} kommuner — {date} ===")
    for k, lab in [("us_microsoft","Microsoft 365"),("us_google","Google Workspace"),
                   ("us_mixed","US mixed"),("eu_sovereign","EU-sovereign"),
                   ("other","Other / regional"),("none","No MX")]:
        n = agg[k]; print(f"  {lab:18}{n:4}  {100*n/agg['total']:5.1f}%  {'█'*round(40*n/agg['total'])}")
    print(f"\n  Microsoft 365: {agg['microsoft_pct']}%  ·  US hyperscaler: {agg['us_pct']}%")
    print(f"  snapshot → snapshots/{date}.json   ·   history → history.json ({len(history)} run(s))")

if __name__ == "__main__":
    main()
