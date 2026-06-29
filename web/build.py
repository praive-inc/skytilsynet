#!/usr/bin/env python3
"""
Build the Skytilsynet public tracker (Skybarometeret) into a single static
``index.html``.

The deploy is a plain rsync of ``web/`` behind Caddy — no build step in prod
(see ``deploy/deploy-local.sh``). So this script runs at dev time and the
generated ``index.html`` is committed. Re-run it after a fresh scan to refresh
the page; scheduling is wired by the operator alongside ``scanner/scan.py``.

  cd web && python3 build.py        # regenerate index.html from current data

The data is baked inline as JSON in a ``<script>`` tag — the page needs no
runtime fetch (works on file://) and has zero US-managed serving dependency
(BetterWorld RFC-001 P5): no CDN, no web fonts, no map tiles.
"""
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "kommune-email-sovereignty.latest.json")
STAT_DATA = os.path.join(ROOT, "data", "statlige-organ-email-sovereignty.latest.json")
# Additional seeded public-sector categories (same {summary, organ} shape as stat).
SEEDED_DATA = [
    (os.path.join(ROOT, "data", "fylkeskommune-email-sovereignty.latest.json"), "fylke", "Fylkeskommuner"),
    (os.path.join(ROOT, "data", "helseforetak-email-sovereignty.latest.json"), "helse", "Helseforetak"),
    (os.path.join(ROOT, "data", "uh-sektor-email-sovereignty.latest.json"), "uni", "Universiteter og høgskoler"),
]
WEB_DATA = os.path.join(ROOT, "data", "kommune-web-sovereignty.latest.json")
HISTORY = os.path.join(ROOT, "scanner", "history.json")
SNAP_DIR = os.path.join(ROOT, "scanner", "snapshots")
OUT = os.path.join(HERE, "index.html")
# The published open datasets (CC BY 4.0) the Om & Metode page links to, per
# category. The deploy rsyncs only web/ to Caddy, so build.py copies these into
# web/data/ (copy_downloads) and the page links to them relatively — self-served,
# no external host, no prod build step.
WEB_DATA_DIR = os.path.join(HERE, "data")
DATA_DOWNLOADS = [
    ("kommune-email-sovereignty.latest.json", "Kommuner — e-post"),
    ("statlige-organ-email-sovereignty.latest.json", "Statlige organ — e-post"),
    ("fylkeskommune-email-sovereignty.latest.json", "Fylkeskommuner — e-post"),
    ("helseforetak-email-sovereignty.latest.json", "Helseforetak — e-post"),
    ("uh-sektor-email-sovereignty.latest.json", "Universiteter og høgskoler — e-post"),
    ("kommune-web-sovereignty.latest.json", "Kommuner — web-akse (infrastruktur)"),
]

MS = "US_MICROSOFT"
LEFT_MS = {MS}

# Norwegian month names for human-readable dates (no locale dependency).
_MONTHS = ["", "januar", "februar", "mars", "april", "mai", "juni", "juli",
           "august", "september", "oktober", "november", "desember"]


def no_date(iso):
    """2026-06-28 -> '28. juni 2026'."""
    y, m, d = iso.split("-")
    return f"{int(d)}. {_MONTHS[int(m)]} {y}"


_DATE_SNAP = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")  # the kommune email series only


def load_snapshots(snap_dir=SNAP_DIR):
    """Return the two most recent kommune email snapshot dicts (old, new), or
    (None, None). Only plain date-named files count — the web (web-*) and statlige
    (statlige-*) series live in the same dir but are a different shape."""
    if not os.path.isdir(snap_dir):
        return None, None
    dates = sorted(f[:-5] for f in os.listdir(snap_dir) if _DATE_SNAP.match(f))
    if len(dates) < 2:
        return None, None
    load = lambda dt: json.load(open(os.path.join(snap_dir, f"{dt}.json")))
    return load(dates[-2]), load(dates[-1])


def _snap_version(snap):
    """The methodology version a snapshot was produced under. Snapshots predating
    the field (issue #24) are treated as version 1, the original MX/SPF/autodiscover
    methodology."""
    return snap.get("methodology_version", 1)


def compute_trend(old, new):
    """The honest per-kommune movement between two snapshots.

    We count actual platform changes (who left / joined Microsoft), not the
    aggregate count delta — the latter conflates real migrations with DNS
    measurement refinement (a kommune moving OTHER -> US_MICROSOFT because its
    backend got unmasked did not "join" Microsoft, we just saw it clearly).
    Over a short window most movement is the latter; the copy says so.

    Movement is only meaningful between snapshots produced by the SAME
    classification methodology. When the methodology version differs (a scanner
    improvement reclassified domains), comparing across it would invent spurious
    "joined Microsoft" jumps (issue #24). In that case we return a `new_baseline`
    marker instead of a count, and the card says "Metodikk forbedret — ny baseline".
    """
    if not old or not new:
        return None
    if _snap_version(old) != _snap_version(new):
        return {
            "new_baseline": True,
            "baseline_date": _snap_date(new),
            "methodology_version": _snap_version(new),
        }
    o = {k["kommune"]: k["platform"] for k in old["kommuner"]}
    n = {k["kommune"]: k["platform"] for k in new["kommuner"]}
    left, joined = [], []
    for name, np in n.items():
        op = o.get(name)
        if op is None or op == np:
            continue
        if op in LEFT_MS and np not in LEFT_MS:
            left.append(name)
        elif op not in LEFT_MS and np in LEFT_MS:
            joined.append(name)
    return {
        "new_baseline": False,
        "methodology_version": _snap_version(new),
        "from_date": _snap_date(old),
        "to_date": _snap_date(new),
        "left_microsoft": sorted(left),
        "joined_microsoft": sorted(joined),
    }


def _snap_date(snap):
    """A snapshot dates itself at the top level (`date`); tests pass meta."""
    return snap.get("date") or snap.get("meta", {}).get("sourceDate")


# Count keys summed when combining per-category summaries into the public-sector
# headline; the percentages are recomputed from the summed counts, never averaged.
_COMBINE_COUNTS = ["total", "us_total", "us_microsoft", "us_google", "us_mixed",
                   "eu_sovereign", "other", "none", "federated", "backend_unmasked"]


def _entity_name(rec):
    """Records carry `name`; kommune records (older datasets) only `kommune`."""
    return rec.get("name") or rec.get("kommune")


def normalize(records):
    """Ensure every record has a `name` (kommune datasets predate the field) so the
    page renders both categories uniformly."""
    out = []
    for r in records:
        if "name" in r:
            out.append(r)
        else:
            out.append({**r, "name": _entity_name(r)})
    return out


def combine_summaries(summaries):
    """Sum the per-category counts and recompute the public-sector headline %.
    A floor stays a floor: backend_unmasked carries over, so the combined
    microsoft_pct/us_pct remain a floor exactly as each category's is."""
    c = {k: sum(s.get(k, 0) for s in summaries) for k in _COMBINE_COUNTS}
    total = c["total"]
    pct = lambda n: round(100 * n / total, 1) if total else 0.0
    c["us_pct"] = pct(c["us_total"])
    c["microsoft_pct"] = pct(c["us_microsoft"])
    c["sovereign_pct"] = pct(c["eu_sovereign"])
    return c


# Suverenitetsmålet (issue #14): the campaign goal as data. The dates and target
# percentage are constants (the goal *definition*); the current share is read from
# the live combined summary, never hardcoded. The countdown to the first milestone
# is computed client-side from `first_target` so the page stays static.
def build_goal(combined):
    return {
        "sovereign_pct": combined["sovereign_pct"],
        "sovereign_count": combined["eu_sovereign"],
        "total": combined["total"],
        "target_pct": 25,
        "target_year": 2030,
        "first_target": "2027-05-17",
        "ladder": [
            {"year": 2026, "name": "Erkjennelsen",
             "desc": "Kartlagt: nesten alt offentlig svarer til amerikansk jurisdiksjon."},
            {"year": 2027, "name": "Den første", "date": "2027-05-17",
             "desc": "Mål: det første organet fullt e-postsuverent — 17. mai 2027."},
            {"year": 2028, "name": "Bevegelsen",
             "desc": "Mål: målbar bevegelse vekk fra USA, organ for organ."},
            {"year": 2030, "name": "Vendepunktet",
             "desc": "Mål: 25 % av skannet offentlig sektor digitalt suveren."},
            {"year": 2035, "name": "Normalen",
             "desc": "Mål: suverenitet er standarden, ikke unntaket."},
        ],
    }


def index_web(web):
    """Index the web-axis dataset by website domain, so each entity can be joined
    to its web record. Empty when no web scan has been published yet."""
    if not web:
        return {}
    return {r["domain"]: r for r in web.get("kommuner", []) if r.get("domain")}


def attach_web(entities, web_index):
    """Join each entity's web-axis record onto it as `web`, keyed by website domain
    (the email record's `website_domain`, or its email `domain` for older data).
    Entities with no web scan carry web=None — the axis stays distinct, never
    conflated with the email verdict."""
    return [{**e, "web": web_index.get(e.get("website_domain") or e.get("domain"))}
            for e in entities]


def build_html(data, history, trend, stat=None, web=None, seeded=None):
    """Render the full single-file site. Pure: same inputs -> same output.

    `data` is the kommune dataset; `stat` (optional) the statlige-organ dataset;
    `seeded` (optional) a list of (dataset, key, label) for the other seeded
    sectors (fylkeskommuner, helseforetak, UH-sektor) — same {summary, organ}
    shape as stat; `web` (optional) the website-infrastructure dataset, joined
    per entity by website domain as a SECOND axis. The categories are baked with
    their own summaries; the headline is the COMBINED scanned public sector."""
    web_index = index_web(web)
    categories = [{
        "key": "kommune", "label": "Kommuner", "summary": data["summary"],
        "entities": attach_web(normalize(data["kommuner"]), web_index),
    }]
    extra = []
    if stat:
        extra.append((stat, "stat", "Statlige organ"))
    extra.extend((ds, key, label) for ds, key, label in (seeded or []) if ds)
    for ds, key, label in extra:
        categories.append({
            "key": key, "label": label, "summary": ds["summary"],
            "entities": attach_web(normalize(ds["organ"]), web_index),
        })
    combined = combine_summaries([c["summary"] for c in categories])
    payload = {
        "meta": data["meta"],
        "combined": combined,
        "goal": build_goal(combined),
        "categories": categories,
        "history": history,
        "trend": trend,
        # The methodology version rides along from the latest snapshot (via the
        # trend) so the Om & Metode section states it without a hardcoded number;
        # null when there are too few snapshots to compute a trend.
        "methodology_version": (trend or {}).get("methodology_version"),
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # </script> can't appear literally inside an inline script.
    blob = blob.replace("</", "<\\/")
    downloads = "".join(
        '<li><a href="data/{fn}" download>{label}</a>'
        '<span class="dl-file">{fn}</span></li>'.format(fn=fn, label=label)
        for fn, label in DATA_DOWNLOADS)
    return (_TEMPLATE.replace("/*__DATA__*/", blob)
            .replace("<!--__DOWNLOADS__-->", downloads))


def copy_downloads(dest_dir=WEB_DATA_DIR):
    """Copy each published per-category dataset into web/data/ so the deployed
    static site (web/ rsynced verbatim to Caddy) serves the CC-BY files the Om &
    Metode page links to — no external host, no prod build step."""
    os.makedirs(dest_dir, exist_ok=True)
    for fn, _ in DATA_DOWNLOADS:
        src = os.path.join(ROOT, "data", fn)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(dest_dir, fn))


def main():
    data = json.load(open(DATA))
    stat = json.load(open(STAT_DATA)) if os.path.exists(STAT_DATA) else None
    seeded = [(json.load(open(p)), key, label) for p, key, label in SEEDED_DATA if os.path.exists(p)]
    web = json.load(open(WEB_DATA)) if os.path.exists(WEB_DATA) else None
    history = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    old, new = load_snapshots()
    trend = compute_trend(old, new)
    html = build_html(data, history, trend, stat, web, seeded)
    with open(OUT, "w") as f:
        f.write(html)
    copy_downloads()
    n_stat = len(stat["organ"]) if stat else 0
    n_web = len(web["kommuner"]) if web else 0
    print(f"Wrote {OUT} ({len(html):,} bytes, {len(data['kommuner'])} kommuner "
          f"+ {n_stat} statlige organ, web axis on {n_web} entities)")


# --------------------------------------------------------------------------
# The page. Static shell (disclaimer always rendered) + JS that renders the
# kommune grid and the per-kommune detail from the baked data via hash routing.
# --------------------------------------------------------------------------
_TEMPLATE = r"""<!doctype html>
<html lang="nb">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Skybarometeret — hvor avhengig er Norge av utenlandsk teknologi?</title>
<meta name="description" content="Skybarometeret: hvilken jurisdiksjon norske kommuners e-post svarer til, kommune for kommune. Faktabasert og kildebelagt. Et uavhengig prosjekt — ikke et offentlig organ." />
<style>
  /* ---------------------------------------------------------------------
     Design tokens. One restrained palette + a modular type and spacing
     scale, so every surface shares the same rhythm (issue #15). System
     fonts only — no web fonts, no CDN (RFC-001 P5).
     --------------------------------------------------------------------- */
  :root{
    /* palette */
    --bg:#0e1217; --bg-2:#0a0d11; --surface:#161d25; --surface-2:#1b232c;
    --line:#2a343f; --line-2:#384654;
    --fg:#eef2f6; --muted:#a3b6c6; --faint:#7d909f; --accent:#5cb3ff;
    --red:#ff6b6b; --green:#4dd6a0; --amber:#f2b56b; --grey:#7d909f;
    --disc-bg:#1c1410; --disc-line:#6a4329; --disc-fg:#f1e3d5; --disc-strong:#ffd9a8;
    /* type scale (1.20 minor third off 16px) */
    --text-xs:12px; --text-sm:13px; --text-base:15px; --text-md:16px;
    --text-lg:18px; --text-xl:21px; --text-2xl:clamp(22px,4vw,30px);
    --text-display:clamp(30px,5vw,46px); --text-stat:clamp(38px,8vw,60px);
    /* spacing scale (4px base) */
    --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px;
    --space-5:20px; --space-6:24px; --space-8:32px; --space-10:40px;
    --space-12:52px; --space-16:72px;
    /* form */
    --radius-sm:10px; --radius:12px; --radius-lg:16px; --radius-pill:999px;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.5);
    --ring:0 0 0 2px var(--bg),0 0 0 4px var(--accent);
    --maxw:1040px;
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  html,body{margin:0;padding:0}
  body{
    background:
      radial-gradient(1200px 600px at 50% -200px,#13202b 0,transparent 70%),
      var(--bg);
    color:var(--fg);
    font:var(--text-md)/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
  }
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  /* Visible keyboard focus everywhere (WCAG 2.4.7) */
  a:focus-visible,button:focus-visible,input:focus-visible,
  .chip:focus-visible,.cattab:focus-visible,.cell:focus-visible,
  .back:focus-visible{outline:none;box-shadow:var(--ring);border-radius:var(--radius-sm)}
  /* Skip link (WCAG 2.4.1) — visible only when focused */
  .skip{position:absolute;left:var(--space-4);top:-48px;z-index:10;
    background:var(--surface);color:var(--fg);border:1px solid var(--line-2);
    border-radius:var(--radius-sm);padding:var(--space-2) var(--space-4);
    transition:top .15s ease}
  .skip:focus{top:var(--space-4);text-decoration:none}
  .wrap{max-width:var(--maxw);margin:0 auto;padding:var(--space-6) var(--space-6) var(--space-16)}
  /* Masthead wordmark — a quiet newsroom byline above the fold */
  .masthead{display:flex;align-items:baseline;gap:var(--space-3);
    padding-bottom:var(--space-5);margin-bottom:var(--space-6);
    border-bottom:1px solid var(--line)}
  .masthead .wordmark{font-weight:700;letter-spacing:-.01em;font-size:var(--text-lg)}
  .masthead .kicker{color:var(--faint);font-size:var(--text-sm);
    letter-spacing:.12em;text-transform:uppercase}
  .badge{display:inline-block;font-size:var(--text-sm);letter-spacing:.08em;text-transform:uppercase;
    color:var(--muted);border:1px solid var(--line);border-radius:var(--radius-pill);
    padding:var(--space-1) var(--space-3);margin-bottom:var(--space-5)}
  h1{font-size:var(--text-display);line-height:1.05;margin:0 0 var(--space-2);letter-spacing:-.02em}
  .tagline{font-size:var(--text-xl);color:var(--muted);margin:0 0 var(--space-8);font-weight:400;max-width:62ch}
  h2{font-size:var(--text-sm);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
    margin:var(--space-12) 0 var(--space-4);font-weight:700;
    display:flex;align-items:center;gap:var(--space-3)}
  h2::after{content:"";flex:1;height:1px;background:var(--line)}
  p{margin:0 0 var(--space-4)}
  /* Disclaimer — load-bearing, always rendered above every view (CLAUDE.md rule 2) */
  .disclaimer{background:var(--disc-bg);border:1px solid var(--disc-line);
    border-radius:var(--radius-lg);padding:var(--space-5) var(--space-6);margin:0 0 var(--space-8)}
  .disclaimer strong{color:var(--disc-strong)}
  .disclaimer p{font-size:var(--text-sm);color:var(--disc-fg);margin:0;max-width:80ch}
  /* Shared card surface */
  .stat,.goal,.fact,.panel{background:linear-gradient(180deg,var(--surface-2),var(--surface));
    border:1px solid var(--line);box-shadow:var(--shadow)}
  .hero{display:grid;grid-template-columns:1fr;gap:var(--space-4);margin:0 0 var(--space-2)}
  @media(min-width:720px){.hero{grid-template-columns:1.1fr .9fr}}
  .stat{border-radius:var(--radius-lg);padding:var(--space-6)}
  .stat .big{font-size:var(--text-stat);font-weight:700;line-height:1;letter-spacing:-.03em;color:var(--red);
    font-variant-numeric:tabular-nums}
  .stat .cap{color:var(--muted);font-size:var(--text-base);margin-top:var(--space-2)}
  .stat .src{color:var(--faint);font-size:var(--text-xs);margin-top:var(--space-3)}
  .trend .big{color:var(--fg);font-size:var(--text-2xl)}
  .trend .row{font-size:var(--text-sm);margin-top:var(--space-1)}
  .trend .green{color:var(--green)} .trend .red{color:var(--red)}
  .spark{display:flex;gap:3px;align-items:flex-end;height:42px;margin-top:var(--space-4)}
  .spark .bar{flex:1;background:linear-gradient(180deg,var(--red),#b8434a);border-radius:2px 2px 0 0;min-height:3px}
  .lab{font-size:var(--text-xs);color:var(--faint);margin-top:var(--space-2)}
  /* Målet — the campaign centerpiece (issue #14) */
  .goal{border-radius:var(--radius-lg);padding:var(--space-6);margin:0 0 var(--space-4)}
  .goal .lead{font-size:var(--text-base);color:var(--muted);margin:0 0 var(--space-5);max-width:72ch}
  .goal-head{display:grid;grid-template-columns:1fr;gap:var(--space-5);margin:0 0 var(--space-2)}
  @media(min-width:720px){.goal-head{grid-template-columns:1fr 1fr}}
  .goal .now{font-size:var(--text-stat);font-weight:700;line-height:1;letter-spacing:-.03em;color:var(--green);
    font-variant-numeric:tabular-nums}
  .goal .now .of{color:var(--muted);font-size:var(--text-md);font-weight:400;letter-spacing:0}
  .goal .sub{color:var(--muted);font-size:var(--text-sm);margin-top:var(--space-2)}
  .goal-track{height:14px;background:var(--bg-2);border:1px solid var(--line);
    border-radius:var(--radius-pill);overflow:hidden;margin:var(--space-4) 0 var(--space-1)}
  .goal-bar{height:100%;background:linear-gradient(90deg,#2f9e74,var(--green));
    border-radius:var(--radius-pill);min-width:2px;transition:width .6s ease}
  .goal .scale{display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--faint)}
  .count-num{font-size:clamp(30px,6vw,46px);font-weight:700;line-height:1;letter-spacing:-.02em;color:var(--fg);font-variant-numeric:tabular-nums}
  .count-units{font-size:var(--text-sm);color:var(--muted);margin-top:var(--space-1)}
  .count-cap{color:var(--muted);font-size:var(--text-sm);margin-top:var(--space-3)}
  .ladder{list-style:none;margin:var(--space-5) 0 0;padding:0;display:grid;gap:var(--space-2)}
  .rung{display:grid;grid-template-columns:auto 1fr;gap:var(--space-4);align-items:start;
    background:var(--bg-2);border:1px solid var(--line);border-left-width:4px;
    border-radius:var(--radius-sm);padding:var(--space-3) var(--space-4)}
  .rung .yr{font-weight:700;font-size:var(--text-sm);color:var(--faint);min-width:54px;font-variant-numeric:tabular-nums}
  .rung .nm{font-weight:600}
  .rung .ds{font-size:var(--text-sm);color:var(--muted);margin-top:2px}
  .rung.on{border-left-color:var(--green);background:#10201a}
  .rung.on .yr,.rung.on .nm{color:var(--green)}
  /* Category toggle (Kommuner | Statlige organ) */
  .catbar{display:flex;gap:var(--space-2);flex-wrap:wrap;margin:0 0 var(--space-4)}
  .cattab{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-sm);
    color:var(--muted);padding:var(--space-3) var(--space-4);font:inherit;font-size:var(--text-sm);
    font-weight:600;cursor:pointer;user-select:none;min-height:44px}
  .cattab:hover{border-color:var(--line-2)}
  .cattab.on{color:var(--fg);border-color:var(--accent);background:#10202e}
  .cattab .pct{color:var(--red);font-weight:700}
  /* Controls */
  .controls{display:flex;flex-wrap:wrap;gap:var(--space-3);align-items:center;margin:var(--space-2) 0 var(--space-4)}
  input[type=search]{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-sm);
    color:var(--fg);padding:var(--space-3) var(--space-4);font:inherit;font-size:var(--text-base);
    min-width:220px;flex:1;min-height:44px}
  input[type=search]::placeholder{color:var(--faint)}
  .filters{display:flex;gap:var(--space-2);flex-wrap:wrap}
  .chip{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-pill);color:var(--muted);
    padding:var(--space-2) var(--space-3);font:inherit;font-size:var(--text-sm);cursor:pointer;user-select:none}
  .chip:hover{border-color:var(--line-2)}
  .chip.on{color:var(--fg);border-color:var(--accent);background:#10202e}
  .legend{display:flex;gap:var(--space-4);flex-wrap:wrap;font-size:var(--text-sm);color:var(--muted);margin:0 0 var(--space-4)}
  .legend .sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:var(--space-2);vertical-align:middle}
  /* Grid "map": one colored tile per kommune */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:var(--space-2)}
  .cell{background:var(--surface);border:1px solid var(--line);border-left-width:4px;border-radius:var(--radius-sm);
    padding:var(--space-3);cursor:pointer;text-align:left;color:var(--fg);font:inherit;overflow:hidden;
    transition:border-color .12s ease,transform .12s ease,background .12s ease}
  .cell:hover{border-color:var(--line-2);background:var(--surface-2);transform:translateY(-1px)}
  .cell .nm{font-size:var(--text-sm);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cell .pl{font-size:var(--text-xs);color:var(--muted);margin-top:2px}
  .cell .fl{font-size:var(--text-xs);color:var(--amber);margin-top:3px}
  .c-red{border-left-color:var(--red)} .c-green{border-left-color:var(--green)}
  .c-amber{border-left-color:var(--amber)} .c-grey{border-left-color:var(--grey)}
  .count{color:var(--muted);font-size:var(--text-sm);margin:0 0 var(--space-3)}
  /* Detail */
  .back{display:inline-block;margin:0 0 var(--space-4);font-size:var(--text-sm);cursor:pointer;color:var(--accent);
    background:none;border:0;padding:var(--space-1) 0;font:inherit}
  .back:hover{text-decoration:underline}
  .facts{display:grid;grid-template-columns:1fr;gap:var(--space-3);margin:0 0 var(--space-5)}
  @media(min-width:640px){.facts{grid-template-columns:1fr 1fr}}
  .fact{border-radius:var(--radius);padding:var(--space-4) var(--space-5)}
  .fact .k{font-size:var(--text-xs);letter-spacing:.05em;text-transform:uppercase;color:var(--faint)}
  .fact .v{font-size:var(--text-lg);font-weight:600;margin-top:var(--space-1)}
  .fact .v.red{color:var(--red)} .fact .v.green{color:var(--green)}
  .evidence{background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius);padding:0 var(--space-4);
    font:var(--text-xs)/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#cdd9e3;
    overflow-x:auto}
  .evidence .lbl{color:var(--muted)}
  /* One evidence row per signal — observation, inference, source + date. */
  .ev{padding:var(--space-3) 0;border-bottom:1px solid var(--line);white-space:pre-wrap;word-break:break-word}
  .ev:last-child{border-bottom:0}
  .ev.hl{background:#161f17;margin:0 calc(-1*var(--space-4));padding-left:var(--space-4);padding-right:var(--space-4);border-left:3px solid var(--green)}
  .ev .sig{display:inline-block;min-width:84px;color:var(--accent);font-weight:600;text-transform:uppercase;font-size:11px}
  .ev .obs{color:var(--fg)}
  .ev .inf{display:block;color:var(--muted);margin-top:3px}
  .ev .conf{color:var(--amber)}
  .ev .src{display:block;color:var(--faint);margin-top:3px;font-size:11px}
  .ev .note{color:var(--amber);margin-top:3px}
  .verdict .conf{color:var(--amber);font-size:var(--text-sm);font-weight:400}
  .verdict .note{display:block;font-size:var(--text-sm);color:var(--muted);font-weight:400;margin-top:5px}
  /* Switch map + benchmark */
  .panel{border-radius:var(--radius);padding:var(--space-5) var(--space-6);margin:0 0 var(--space-4)}
  .panel h3{margin:0 0 var(--space-2);font-size:var(--text-lg)}
  .panel .arrow{color:var(--accent)}
  .flag{border-left:3px solid var(--amber);padding-left:var(--space-3);margin:var(--space-3) 0;font-size:var(--text-sm);color:var(--disc-fg)}
  .flag b{color:var(--amber)}
  table.switch{width:100%;border-collapse:collapse;font-size:var(--text-sm)}
  table.switch td,table.switch th{text-align:left;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--line);vertical-align:top}
  table.switch th{color:var(--faint);font-weight:600;font-size:var(--text-xs);letter-spacing:.05em;text-transform:uppercase}
  /* Activism funnel (issue #3): copy-ready citizen tooling per entity */
  .funnel{padding:var(--space-5) var(--space-6);margin:0 0 var(--space-4)}
  .funnel h3{margin:0 0 var(--space-2);font-size:var(--text-lg)}
  .funnel .lead{font-size:var(--text-sm);color:var(--muted);margin:0 0 var(--space-3)}
  .funnel textarea{width:100%;min-height:200px;resize:vertical;background:var(--bg-2);
    border:1px solid var(--line);border-radius:var(--radius-sm);color:#cdd9e3;
    padding:var(--space-3) var(--space-4);
    font:var(--text-xs)/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .funnel .acts{display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:center;margin-top:var(--space-3)}
  .copybtn{background:#10202e;border:1px solid var(--accent);border-radius:var(--radius-sm);
    color:var(--fg);padding:var(--space-2) var(--space-4);font:inherit;font-size:var(--text-sm);
    font-weight:600;cursor:pointer;min-height:44px}
  .copybtn:hover{background:#15293a}
  .copybtn.ok{border-color:var(--green);color:var(--green)}
  .funnel .ext{font-size:var(--text-sm)}
  .funnel .stat-note{font-size:var(--text-xs);color:var(--faint);margin-top:var(--space-3)}
  /* Share card (issue #25): a screenshot-friendly per-entity 'del'-card.
     The fact is the provocation — name + email verdict + jurisdiction +
     governance + the headline floor + skytilsynet.no. */
  .sharewrap{margin:0 0 var(--space-4)}
  .sharecard{background:linear-gradient(160deg,#13202b,#0a0d11);border:1px solid var(--line-2);
    border-radius:var(--radius-lg);padding:var(--space-8);margin:0 0 var(--space-4);
    box-shadow:var(--shadow);max-width:560px}
  .sharecard .sc-brand{color:var(--faint);font-size:var(--text-sm);letter-spacing:.08em}
  .sharecard .sc-brand .dot{color:var(--green)}
  .sharecard .sc-name{font-size:var(--text-2xl);font-weight:700;margin-top:var(--space-3);
    letter-spacing:-.02em;line-height:1.1}
  .sharecard .sc-label{font-size:var(--text-xs);letter-spacing:.1em;text-transform:uppercase;
    color:var(--faint);margin-top:var(--space-5)}
  .sharecard .sc-verdict{font-size:var(--text-xl);font-weight:700;margin-top:var(--space-1)}
  .sharecard .sc-verdict.red{color:var(--red)} .sharecard .sc-verdict.green{color:var(--green)}
  .sharecard .sc-juris{color:var(--muted);font-size:var(--text-base);margin-top:var(--space-1)}
  .sharecard .sc-gov{color:var(--muted);font-size:var(--text-sm);margin-top:var(--space-2)}
  .sharecard .sc-stat{color:var(--fg);font-size:var(--text-sm);margin-top:var(--space-5);
    border-top:1px solid var(--line);padding-top:var(--space-3)}
  .sharecard .sc-url{color:var(--accent);font-weight:700;margin-top:var(--space-3);font-size:var(--text-md)}
  /* Om & Metode (issue #30): the credibility section. Reuses .panel; a few
     list/link affordances for the method signals + data downloads. */
  .panel h3{margin-top:0}
  .method-list{margin:0 0 var(--space-4);padding-left:var(--space-5);color:var(--muted);font-size:var(--text-sm)}
  .method-list li{margin:0 0 var(--space-2)}
  .method-list code,.panel p code{background:var(--bg-2);border:1px solid var(--line);
    border-radius:6px;padding:1px 5px;font:var(--text-xs)/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#cdd9e3}
  .downloads{list-style:none;margin:0 0 var(--space-4);padding:0;display:grid;gap:var(--space-2)}
  .downloads li{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--space-2);
    background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius-sm);padding:var(--space-2) var(--space-3)}
  .downloads a{font-weight:600}
  .downloads .dl-file{color:var(--faint);font:var(--text-xs)/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .mast-nav{margin-left:auto;font-size:var(--text-sm)}
  .en{border-top:1px solid var(--line);margin-top:var(--space-16);padding-top:var(--space-6);color:var(--muted);font-size:var(--text-sm)}
  .en strong{color:var(--fg)}
  footer{margin-top:var(--space-10);color:var(--faint);font-size:var(--text-sm)}
  .dot{color:var(--green)}
  .hidden{display:none}
  /* Honour a reduced-motion preference (WCAG 2.3.3): no fills, no smooth scroll */
  @media(prefers-reduced-motion:reduce){
    html{scroll-behavior:auto}
    *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
      transition-duration:.001ms!important}
  }
</style>
</head>
<body>
<a class="skip" href="#main">Hopp til innholdet</a>
<div class="wrap">

  <header class="masthead">
    <span class="wordmark"><span class="dot" aria-hidden="true">●</span> Skytilsynet</span>
    <span class="kicker">Skybarometeret</span>
    <nav class="mast-nav" aria-label="Sidenavigasjon"><a href="#om">Om &amp; metode</a></nav>
  </header>

  <!-- DISCLAIMER: rendered once, outside the routed views, so it is present on
       every "page" (landing and per-kommune detail). Never remove. -->
  <div class="disclaimer" role="note">
    <p><strong>⚠️ Skytilsynet er ikke et offentlig organ.</strong>
      Vi er ikke tilknyttet, drevet av eller godkjent av norske myndigheter,
      Datatilsynet, Digitaliseringsdirektoratet eller noen annen statlig eller
      kommunal etat. Navnet beskriver hva vi gjør i overført betydning — vi følger
      med på offentlig sektors avhengighet av skytjenester — og er ikke en offisiell
      rolle. All informasjon er hentet fra åpne kilder og presenteres faktabasert
      og nøytralt. <a href="#kilde">Metode og kilder ↓</a></p>
  </div>

  <main id="main">

  <!-- LANDING VIEW -->
  <section id="view-home">
    <span class="badge">Skybarometeret</span>
    <h1>Hvor avhengig er Norge av utenlandsk teknologi?</h1>
    <p class="tagline">Hvilken jurisdiksjon norsk offentlig sektors e-post svarer til —
      kommune for kommune og statlig organ for statlig organ.</p>

    <div class="hero">
      <div class="stat" id="stat-hero"></div>
      <div class="stat trend" id="stat-trend"></div>
    </div>

    <h2 id="maalet">Målet</h2>
    <div class="goal" id="goal"></div>

    <h2 id="grid-title">Hele offentlig sektor</h2>
    <nav class="catbar" id="catbar" aria-label="Velg kategori"></nav>
    <p class="tagline" style="font-size:15px;margin-bottom:14px">
      Hver rute er ett organ, fargelagt etter hvilken jurisdiksjon e-posten svarer
      til. Klikk for plattform, jurisdiksjon, evidens og anbefalt europeisk alternativ.</p>

    <div class="legend">
      <span><span class="sw" style="background:var(--red)"></span>USA (CLOUD Act)</span>
      <span><span class="sw" style="background:var(--green)"></span>Norge / EØS</span>
      <span><span class="sw" style="background:var(--amber)"></span>USA — bak e-postgateway (gulv, ikke tak)</span>
      <span><span class="sw" style="background:var(--grey)"></span>Uavklart</span>
    </div>
    <div class="controls">
      <input type="search" id="q" placeholder="Søk …" autocomplete="off" />
      <div class="filters" id="filters"></div>
    </div>
    <p class="count" id="count"></p>
    <div class="grid" id="grid"></div>
  </section>

  <!-- DETAIL VIEW -->
  <section id="view-detail" class="hidden"></section>

  <!-- SWITCH MAP + BENCHMARK + METHOD: always below the fold -->
  <section id="static-rest">
    <h2 id="bytte">Fra USA til Europa — byttekartet</h2>
    <div class="panel">
      <p>Hvert funn har et konkret, adopterbart europeisk alternativ. Men
        <b>EU-lokalisert er ikke det samme som EU-eid</b> — det er fallgruven
        («suverenitetsvasking»). Byttekartet under koder forskjellen.</p>
      <table class="switch">
        <thead><tr><th>I dag</th><th>Europeisk alternativ</th></tr></thead>
        <tbody>
          <tr><td>Microsoft 365 (e-post + dokumenter)</td>
              <td><span class="arrow">→</span> openDesk (Open-Xchange + Nextcloud) / LibreOffice</td></tr>
          <tr><td>Azure / AWS (drift)</td>
              <td><span class="arrow">→</span> OVHcloud / Hetzner / IONOS / STACKIT</td></tr>
        </tbody>
      </table>
      <div class="flag"><b>Suverenitetsvasking 1 — «EU-region» ≠ EU-jurisdiksjon.</b>
        AWS og Azures «suverene sky» ligger fysisk i EU, men leverandøren er
        amerikansk og dermed underlagt CLOUD Act uansett hvor dataene lagres.</div>
      <div class="flag"><b>Suverenitetsvasking 2 — utenfor EU-retten.</b>
        Leverandører med jurisdiksjon i Storbritannia eller Sveits er utenfor
        EUs felles rettsvern, selv om de er «europeiske».</div>
      <div class="flag"><b>Suverenitetsvasking 3 — opphav.</b>
        OnlyOffice har russisk opphav — «åpen kildekode» og «europeisk vert»
        skjuler ikke leverandørkjeden.</div>
    </div>

    <h2>Hvorfor det går an — to målestokker</h2>
    <div class="panel">
      <h3>Schleswig-Holstein</h3>
      <p>Den tyske delstaten flytter ~30 000 arbeidsstasjoner av Windows og
        Microsoft 365 over på Linux og åpen kildekode — anslått <b>≈ 15 mill. euro
        spart per år</b> og full kontroll over egen infrastruktur.</p>
      <h3 style="margin-top:14px">Larvik kommune</h3>
      <p>Larvik friga saksbehandlingen sin fra både Microsoft og Google — anslått
        <b>≈ 10 mill. kroner spart per år</b>. Det er gjort, i Norge, av en kommune.</p>
      <p style="font-size:13px;color:var(--muted);margin-top:10px">Endring som varer
        ligger i innkjøpsreglene, ikke i en enkelt IT-beslutning som kan reverseres
        ved neste kommunestyrevedtak (lærdommen fra Münchens LiMux).</p>
    </div>

    <h2 id="om">Om &amp; Metode</h2>

    <div class="panel">
      <h3>Hva dette er</h3>
      <p>Skybarometeret kartlegger hvilken jurisdiksjon norsk offentlig sektors
        e-post svarer til, utledet av åpne DNS-data, og peker på europeiske
        alternativer. Hver påstand er kildebelagt og presentert faktabasert —
        vi oppgir faktumet, aldri en moralsk dom.</p>
      <p><strong>Skytilsynet er ikke et offentlig organ.</strong> Vi er
        uavhengige, og ikke tilknyttet, drevet av eller godkjent av Datatilsynet,
        Digitaliseringsdirektoratet eller noen annen norsk statlig eller kommunal
        myndighet. Navnet beskriver i overført betydning hva vi gjør — vi følger
        med på offentlig sektors avhengighet av skytjenester — og er ikke en
        offisiell rolle.</p>
    </div>

    <div class="panel">
      <h3 id="kilde">Slik måler vi (metode)</h3>
      <p id="method-note"></p>
      <p>Hvert organ klassifiseres ut fra det offentlig DNS — og ett uautentisert
        HTTPS-oppslag — uansett røper. Ingen innlogging, ingen kostnad. Signalene,
        i økende bevisstyrke:</p>
      <ul class="method-list">
        <li><b>MX</b> — hvor e-posten leveres (f.eks.
          <code>*.mail.protection.outlook.com</code> → Microsoft 365).</li>
        <li><b>SPF</b> — hvilke servere som får sende for domenet
          (<code>include:spf.protection.outlook.com</code> → Microsoft).</li>
        <li><b>Autodiscover</b> — klientoppsettets CNAME
          (<code>autodiscover.outlook.com</code> → Microsoft 365-leietaker).</li>
        <li><b>getuserrealm</b> — Microsofts egen
          <code>login.microsoftonline.com/getuserrealm.srf</code> svarer
          <code>Managed</code> (aktiv M365-leietaker) eller <code>Federated</code>
          (Azure AD-føderert).</li>
        <li><b>DKIM-selektor</b> — <code>selector1/2._domainkey</code> peker inn i
          <code>*.onmicrosoft.com</code> (Microsoft), eller
          <code>google._domainkey</code> finnes (Google).</li>
        <li><b>SPF-IP-avdekking</b> — når SPF bare lister IP-er, slår vi opp om de
          ligger i Microsofts EOP-områder; flatet SPF røper plattformen likevel.</li>
      </ul>
      <p><b>Avdekking av gateway og samdrift.</b> Mange organ ligger bak en
        e-postsikkerhets-gateway (Proofpoint, Mimecast) eller en interkommunal
        IKT-samdrift som skjuler bakomliggende plattform i MX-en. Vi forsøker å
        avdekke den reelle plattformen via SPF, DKIM og getuserrealm — der det
        ikke lykkes, merkes organet «bak gateway».</p>
      <div class="flag"><b>Gulv, ikke tak.</b> Microsoft- og USA-andelen er et
        <b>gulv</b>: noen gateway-bakender forblir uavdekket, og flere av dem er
        etter alt å dømme også Microsoft. Den reelle USA-andelen er minst så høy
        som vist — aldri lavere.</div>
      <p><b>Web-akse (andre akse).</b> E-post er bare én akse. En egen, atskilt
        akse ser på <b>nettstedets infrastruktur</b>: vert-IP-ens opphavs-ASN,
        HTTP-headere, innebygde tredjeparts-ressurser og TLS-utsteder — utledet av
        det en nettleser uansett henter. Den påvirker aldri e-postverdiktet; de to
        aksene holdes adskilt.</p>
    </div>

    <div class="panel">
      <h3>Styresett-vurdering</h3>
      <p>For hvert funn utleder vi operatørens jurisdiksjon, og for jurisdiksjonens
        land et faktabasert styresett-nivå — <b>Demokrati</b>, <b>Delvis fritt</b>
        eller <b>Autoritært styre</b> — fra <b>Freedom House (Freedom in the
        World)</b>. Vi viser den siterte statusen og poengsummen (0–100) med år og
        en lenke til kilden, f.eks.
        <a href="https://freedomhouse.org/country/united-states/freedom-world/2026"
          target="_blank" rel="noopener">USA: Free 81/100 (2026)</a>. Nivået er en
        nøytral merkelapp på landet dataene svarer til — ikke en dom over organet.</p>
    </div>

    <div class="panel">
      <h3>Metodikk-versjon</h3>
      <p>Gjeldende metodikk-versjon: <b id="methodology-version">—</b>.
        Trendtall sammenlignes kun mellom målinger gjort med <b>samme</b>
        metodikk-versjon. En forbedring i skanneren som omklassifiserer domener gir
        en ny baseline — ikke et falskt «byttet plattform»-tall (det ville vist
        forbedret kartlegging som faktiske bytter).</p>
    </div>

    <div class="panel">
      <h3 id="apne-data">Åpne data</h3>
      <p>Datasettene er åpne under <b>CC BY 4.0</b>. Hver rad bærer sin egen kilde
        og dato. Last ned per kategori — for journalister og forskere:</p>
      <ul class="downloads"><!--__DOWNLOADS__--></ul>
      <p style="font-size:13px;color:var(--muted)">Attribusjon ved gjenbruk:
        «Skytilsynet / BetterWorld, skytilsynet.no (CC BY 4.0)».
        <a href="https://github.com/praive-inc/skytilsynet">Kode og full metode på GitHub →</a></p>
    </div>

    <div class="panel">
      <h3>Retting</h3>
      <p>Ser du en feil? Vi retter gjerne — og oppgir alltid kilde, så påstandene
        er etterprøvbare. En egen rettekanal <b>kommer ved lansering</b>. Vi
        beholder ingenting personlig: aktivisme-verktøyene samler ikke inn noe om
        deg, kun aggregerte tall.</p>
    </div>

    <div class="en" lang="en">
      <p><strong>About this site (English).</strong> Skybarometeret tracks which
      jurisdiction Norwegian municipalities' email answers to, derived from public
      DNS. <strong>Skytilsynet is an independent project and is
      not a government body, not affiliated with, operated by, or endorsed by any
      Norwegian public authority</strong> (including Datatilsynet). All data is drawn from publicly
      available sources and presented factually. Open data (CC BY 4.0); every row
      carries its source and date.</p>
    </div>

  </section>

  </main>

  <footer>
    <span class="dot" aria-hidden="true">●</span> Et prosjekt fra BetterWorld · skytilsynet.no ·
    <a href="#om">Om &amp; metode</a> ·
    <a href="https://github.com/praive-inc/skytilsynet">åpen kildekode</a>
  </footer>
</div>

<script id="data" type="application/json">/*__DATA__*/</script>
<script>
(function(){
  "use strict";
  var DB = JSON.parse(document.getElementById("data").textContent);
  var CATS = DB.categories;                 // [{key,label,summary,entities}]
  var COMBINED = DB.combined;               // headline over the whole public sector
  function nameOf(k){ return k.name || k.kommune; }

  // platform -> {label, juris, css color class}
  function platMeta(k){
    switch(k.platform){
      case "US_MICROSOFT": return {label:"Microsoft 365", juris:"USA (CLOUD Act)",
        css: k.behind_gateway ? "c-amber" : "c-red", vcls:"red"};
      case "US_GOOGLE": return {label:"Google Workspace", juris:"USA (CLOUD Act)", css:"c-red", vcls:"red"};
      case "US_MIXED": return {label:"Microsoft + Google", juris:"USA (CLOUD Act)", css:"c-red", vcls:"red"};
      case "EU_SOVEREIGN": return {label:"Europeisk / norsk drift", juris:"Norge (EØS)", css:"c-green", vcls:"green"};
      default: return {label:"Uavklart", juris:"Uavklart", css:"c-grey", vcls:""};
    }
  }
  var FILTERS = [
    {key:"ALL", label:"Alle"},
    {key:"US_MICROSOFT", label:"Microsoft"},
    {key:"US_GOOGLE", label:"Google"},
    {key:"EU_SOVEREIGN", label:"Norge / EØS"},
    {key:"OTHER", label:"Uavklart"}
  ];
  var FLAG_NO = {
    backend_unmasked: "Bak e-postgateway — bakomliggende plattform ikke avdekket (tallet er et gulv)",
    mail_domain_differs_from_website: "E-postdomenet er et annet enn nettstedet",
    federated: "Azure AD-føderert tenant påvist — e-post utledet med høy sikkerhet"
  };
  // Governance frame (issue #9): the regime tier of the jurisdiction's country,
  // derived from the cited Freedom House status. Factual label, not editorial.
  var TIER_NO = {democracy:"Demokrati", "partly free":"Delvis fritt",
    authoritarian:"Autoritært styre"};
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];});}
  function slug(name){return name.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");}
  function pct(n){return Number(n).toFixed(1).replace(".",",");}
  function catBy(key){ for(var i=0;i<CATS.length;i++){ if(CATS[i].key===key) return CATS[i]; } return CATS[0]; }

  var state = {q:"", filter:"ALL", cat: CATS[0].key};
  function curCat(){ return catBy(state.cat); }

  // ---- Hero (combined) + trend -------------------------------------------
  function renderHero(){
    var s = COMBINED;
    var breakdown = CATS.map(function(c){
      return esc(c.label)+' '+pct(c.summary.microsoft_pct)+' %'; }).join(' · ');
    var sectorLabel = CATS.length>1 ? "norsk offentlig sektor (kommuner + statlige organ)"
                                    : "norske kommuner";
    document.getElementById("stat-hero").innerHTML =
      '<div class="big">'+pct(s.microsoft_pct)+' %</div>'+
      '<div class="cap">av '+sectorLabel+' kjører e-posten på Microsoft 365 '+
      '(USA; CLOUD Act-jurisdiksjon). '+pct(s.us_pct)+
      ' % på en amerikansk skyleverandør.</div>'+
      '<div class="src">'+s.us_total+' av '+s.total+' organ på USA · '+breakdown+
      ' · målt '+esc(noDate(DB.meta.sourceDate))+
      ' fra åpne DNS-data (MX, SPF, autodiscover).</div>';

    var t = DB.trend, el = document.getElementById("stat-trend");
    var spark = renderSpark();
    if(!t){ el.innerHTML = '<div class="big">Trend</div>'+
      '<div class="cap">For få målinger til å vise bevegelse ennå.</div>'+spark; return; }
    if(t.new_baseline){ el.innerHTML =
      '<div class="big">Bevegelse (kommuner)</div>'+
      '<div class="cap">Metodikk forbedret — ny baseline fra '+
        esc(noDate(t.baseline_date))+'.</div>'+
      '<div class="src">Forbedret kartlegging endret klassifiseringen, så vi '+
        'sammenligner ikke på tvers av rekalibreringen — det ville vist forbedret '+
        'kartlegging som faktiske bytter. Bevegelsestall kommer ved neste måling '+
        'med samme metodikk.</div>'+spark; return; }
    var left = t.left_microsoft.length, joined = t.joined_microsoft.length;
    el.innerHTML =
      '<div class="big">Bevegelse (kommuner)</div>'+
      '<div class="cap">Siden forrige måling ('+esc(noDate(t.from_date))+' → '+
        esc(noDate(t.to_date))+'):</div>'+
      '<div class="row '+(left?"green":"")+'">'+
        (left? '🟢 '+left+' kommune'+(left===1?"":"r")+' forlot Microsoft'
             : '— ingen kommuner forlot Microsoft')+'</div>'+
      '<div class="row '+(joined?"red":"")+'">'+
        (joined? '🔴 '+joined+' kommune'+(joined===1?"":"r")+' ble kartlagt på Microsoft'
               : '— ingen nye på Microsoft')+'</div>'+
      '<div class="src">Over så korte vindu gjenspeiler bevegelse mest forbedret '+
        'kartlegging, ikke faktiske bytter. Vi sier det rett ut.</div>'+spark;
  }
  function renderSpark(){
    var h = DB.history || [];
    if(h.length < 2) return "";
    var max = 100, bars = h.map(function(p){
      var v = p.microsoft_pct;
      return '<div class="bar" aria-hidden="true" style="height:'+(v/max*100)+'%" title="'+
        esc(p.date)+': '+v+' %"></div>';
    }).join("");
    var lab = 'Microsoft-andel blant kommuner, fra '+h[0].date+' ('+h[0].microsoft_pct+
      ' %) til '+h[h.length-1].date+' ('+h[h.length-1].microsoft_pct+' %)';
    return '<div class="spark" role="img" aria-label="'+esc(lab)+'">'+bars+'</div>'+
      '<div class="lab">Microsoft-andel (kommuner), '+esc(h[0].date)+' → '+esc(h[h.length-1].date)+'</div>';
  }

  // ---- Målet: progress + live countdown + ladder -------------------------
  function renderGoal(){
    var g = DB.goal, el = document.getElementById("goal");
    if(!g){ el.style.display = "none"; return; }
    var fill = Math.min(100, g.target_pct ? g.sovereign_pct / g.target_pct * 100 : 0);
    var moved = (DB.trend && DB.trend.left_microsoft) ? DB.trend.left_microsoft.length : 0;
    var rungs = g.ladder.map(function(r){
      return '<li class="rung" data-yr="'+r.year+'">'+
        '<span class="yr">'+esc(r.year)+'</span>'+
        '<span><span class="nm">'+esc(r.name)+'</span>'+
        '<span class="ds">'+esc(r.desc)+'</span></span></li>';
    }).join("");
    el.innerHTML =
      '<p class="lead">Suverenitetsmålet: <b>'+g.target_pct+' %</b> av skannet '+
        'offentlig sektor digitalt suveren innen <b>'+g.target_year+'</b> — og det '+
        '<b>første</b> fullt e-postsuverene organet innen <b>17. mai 2027</b>. '+
        'E-post er én akse; tallet under er e-postsuverenitet, et gulv mot det fulle målet.</p>'+
      '<div class="goal-head">'+
        '<div>'+
          '<div class="now">'+pct(g.sovereign_pct)+' %<span class="of"> / mål '+g.target_pct+' %</span></div>'+
          '<div class="sub">'+esc(g.sovereign_count)+' av '+esc(g.total)+' skannede organ '+
            'har e-post under norsk/europeisk jurisdiksjon i dag.</div>'+
          '<div class="goal-track" role="img" aria-label="Fremdrift mot målet: '+
            pct(g.sovereign_pct)+' % av '+g.target_pct+' %">'+
            '<div class="goal-bar" style="width:'+fill.toFixed(1)+'%"></div></div>'+
          '<div class="scale"><span>0 %</span><span>mål '+g.target_pct+' % ('+g.target_year+')</span></div>'+
        '</div>'+
        '<div>'+
          '<div class="count-num" id="countdown" aria-hidden="true">…</div>'+
          '<div class="count-units" id="countdown-units" aria-hidden="true"></div>'+
          '<div class="count-cap">til <b>17. mai 2027</b> — '+
            'fortsatt '+esc(g.sovereign_count)+' e-postsuverene. '+
            'Denne uken: '+moved+' flyttet.</div>'+
        '</div>'+
      '</div>'+
      '<ul class="ladder">'+rungs+'</ul>';
    highlightRung();
    tickCountdown(g.first_target);
    if(window.__goalTimer) clearInterval(window.__goalTimer);
    window.__goalTimer = setInterval(function(){ tickCountdown(g.first_target); }, 1000);
  }
  function highlightRung(){
    // The current rung = the latest one whose year has arrived (client-side, so it
    // advances on its own as the calendar does — the page itself stays static).
    var now = new Date(), yr = now.getFullYear(), rungs = document.querySelectorAll(".rung");
    var on = null;
    for(var i=0;i<rungs.length;i++){
      if(parseInt(rungs[i].getAttribute("data-yr"),10) <= yr) on = rungs[i];
    }
    if(on) on.classList.add("on");
  }
  function tickCountdown(target){
    var el = document.getElementById("countdown");
    if(!el) return;
    var ms = new Date(target+"T00:00:00") - new Date();
    var units = document.getElementById("countdown-units");
    if(ms <= 0){ el.textContent = "Nådd"; if(units) units.textContent = ""; return; }
    var s = Math.floor(ms/1000);
    var d = Math.floor(s/86400), h = Math.floor(s%86400/3600),
        m = Math.floor(s%3600/60), sec = s%60;
    el.textContent = d + " dager";
    if(units) units.textContent = h+" t "+m+" min "+sec+" s";
  }

  // ---- Category toggle + grid --------------------------------------------
  function renderCatbar(){
    var bar = document.getElementById("catbar");
    if(CATS.length < 2){ bar.style.display = "none"; return; }
    bar.innerHTML = CATS.map(function(c){
      var on = state.cat===c.key;
      return '<button type="button" class="cattab'+(on?" on":"")+'" data-c="'+esc(c.key)+
        '" aria-pressed="'+on+'">'+
        esc(c.label)+' <span class="pct">'+pct(c.summary.microsoft_pct)+' %</span></button>';
    }).join("");
  }
  function renderFilters(){
    document.getElementById("filters").innerHTML = FILTERS.map(function(f){
      var on = state.filter===f.key;
      return '<button type="button" class="chip'+(on?" on":"")+'" data-f="'+f.key+
        '" aria-pressed="'+on+'">'+esc(f.label)+'</button>';
    }).join("");
  }
  function matches(k){
    if(state.filter==="OTHER"){ if(k.platform!=="OTHER" && k.platform!=="NONE") return false; }
    else if(state.filter!=="ALL" && k.platform!==state.filter) return false;
    if(state.q && nameOf(k).toLowerCase().indexOf(state.q)<0 &&
       (k.domain||"").toLowerCase().indexOf(state.q)<0) return false;
    return true;
  }
  function renderGrid(){
    var cat = curCat(), all = cat.entities;
    document.getElementById("grid-title").textContent = cat.label;
    var rows = all.filter(matches).sort(function(a,b){
      return nameOf(a).localeCompare(nameOf(b),"nb"); });
    document.getElementById("count").textContent =
      rows.length+" av "+all.length+" "+cat.label.toLowerCase();
    document.getElementById("grid").innerHTML = rows.map(function(k){
      var m = platMeta(k);
      var fl = (k.flags||[]).indexOf("backend_unmasked")>=0
        ? '<div class="fl">⚑ bak gateway — gulv</div>' : "";
      return '<button class="cell '+m.css+'" data-c="'+esc(cat.key)+'" data-k="'+esc(slug(nameOf(k)))+'">'+
        '<div class="nm">'+esc(nameOf(k))+'</div>'+
        '<div class="pl">'+esc(m.label)+'</div>'+fl+'</button>';
    }).join("");
  }

  // ---- Detail -------------------------------------------------------------
  function bySlug(catKey, s){
    var arr = catBy(catKey).entities;
    for(var i=0;i<arr.length;i++){ if(slug(nameOf(arr[i]))===s) return arr[i]; }
    return null;
  }
  function fact(k,v,cls){ return '<div class="fact"><div class="k">'+esc(k)+
    '</div><div class="v '+(cls||"")+'">'+v+'</div></div>'; }
  // platform key (incl. UAVKLART verdict) -> {label, value-color class}
  function verdictCls(p){
    return p==="EU_SOVEREIGN" ? "green"
         : (p==="US_MICROSOFT"||p==="US_GOOGLE"||p==="US_MIXED") ? "red" : "";
  }
  function renderVerdict(k){
    var vd = k.verdict || {platform:k.platform, label:platMeta(k).label,
                           confidence:null, uavklart:false, note:null};
    var pct = vd.confidence!=null
      ? '<span class="conf">'+Math.round(vd.confidence*100)+' % konfidens</span>' : "";
    var note = vd.note ? '<span class="note">'+esc(vd.note)+'</span>' : "";
    return '<div class="fact verdict"><div class="k">Plattform (e-post) — konfidensvektet verdikt</div>'+
      '<div class="v '+verdictCls(vd.platform)+'">'+esc(vd.label)+' '+pct+note+'</div></div>';
  }
  function renderTrail(trail){
    if(!trail || !trail.length)
      return '<div class="evidence">(ingen e-postsignaler funnet i DNS)</div>';
    return '<div class="evidence">'+trail.map(function(s){
      var hl = s.signal_type==="spf_ip" ? " hl" : "";
      var conf = '<span class="conf">'+s.confidence.toFixed(2)+' konfidens</span>';
      return '<div class="ev'+hl+'">'+
        '<span class="sig">'+esc(s.signal_type)+'</span>'+
        '<span class="obs">'+esc(s.observation)+'</span>'+
        '<span class="inf">→ '+esc(s.inference)+' · '+conf+'</span>'+
        '<span class="src">kilde: '+esc(s.source)+' · '+esc(noDate(s.observed_at))+'</span>'+
        '</div>';
    }).join("")+'</div>';
  }
  // The governance verdict, cited: tier + the Freedom House status/score it is
  // derived from + a source link. Empty when the jurisdiction is undetermined.
  function govFact(k){
    var g = k.governance;
    if(!g) return "";
    var tier = TIER_NO[g.tier] || g.tier;
    var src = esc(g.index.split(" (")[0])+": "+esc(g.status)+" "+esc(g.score)+
      "/100 ("+esc(g.year)+")";
    var v = esc(tier)+
      '<div style="font-size:12px;font-weight:400;color:var(--muted);margin-top:4px">'+
        esc(g.country)+' · <a href="'+esc(g.sourceUrl)+'" target="_blank" '+
        'rel="noopener">'+src+'</a></div>';
    return fact("Styresett i jurisdiksjonen", v);
  }
  // Jurisdiction string -> the same red/green coding the email axis uses.
  function jurCls(j){
    j = j || "";
    return /CLOUD Act/.test(j) ? "red" : /\(EEA\)|\(EU\)/.test(j) ? "green" : "";
  }
  // ---- Web axis (issue #13): the SECOND, distinct axis. Where does the website
  // infrastructure answer to? Joined per entity by website domain, never merged
  // into the email verdict. Rendered as its own cited section; absent when no scan.
  function renderWebAxis(k){
    var w = k.web;
    if(!w) return "";
    var host = w.hosting || {};
    var usPct = Math.round((w.us_resource_fraction || 0) * 100);
    var e = w.evidence || {};
    var tp = (w.third_parties || []).map(function(t){
      return '<div class="ev">'+
        '<span class="sig">'+esc(t.category)+'</span>'+
        '<span class="obs">'+esc(t.domain)+'</span>'+
        '<span class="inf">→ jurisdiksjon: '+esc(t.jurisdiction)+
          (t.flags && t.flags.length ? ' · '+esc(t.flags.join(", ")) : "")+'</span>'+
        '</div>';
    }).join("");
    if(!tp) tp = '<div class="ev"><span class="obs">Ingen eksterne tredjeparts-'+
      'ressurser lastet fra forsiden.</span></div>';
    return '<h2>Web-akse — nettstedets infrastruktur</h2>'+
      '<p style="font-size:13px;color:var(--muted);margin:-6px 0 12px">En '+
        '<b>egen akse, skilt fra e-post</b>: hvor svarer selve nettstedet til? '+
        'Utledet av det en nettleser uansett henter — HTTP-headere, innebygde '+
        'tredjeparts-ressurser og verts-IP-ens opphavs-ASN. Påvirker ikke '+
        'e-postverdiktet over.</p>'+
      '<div class="facts">'+
        fact("Vert (hosting) jurisdiksjon", esc(host.jurisdiction || "Uavklart"),
             jurCls(host.jurisdiction))+
        fact("Tredjeparts-ressurser fra USA", usPct+' %', usPct>0?"red":"green")+
        fact("Analyse / sporing", w.analytics ? "Påvist" : "Ikke påvist",
             w.analytics ? "red" : "green")+
        fact("TLS-utsteder", esc(e.tls_issuer || "—"))+
      '</div>'+
      '<div class="evidence">'+tp+'</div>'+
      '<p style="font-size:13px;color:var(--muted);margin-top:10px">Kilde: '+
        esc(w.url || ("https://"+(w.host||"")))+' (HTTP-headere + innebygde '+
        'ressurser + TLS-utsteder) og offentlig DNS (A → Team Cymru origin-ASN'+
        (host.asn? ": AS"+esc(host.asn)+(host.name?" "+esc(host.name):""):"")+
        '), målt '+esc(noDate(w.sourceDate || DB.meta.sourceDate))+'.</p>';
  }
  // ---- Activism funnel (issue #3): turn each detail page into ACTION ------
  // Templates are baked here, client-side, from the entity name — no runtime
  // fetch, no per-citizen data (rule 5). The ask is always the DURABLE one:
  // a procurement-rule / strategy change, never a personal attack (rule 6).
  function buildInnsyn(k){
    var n = nameOf(k);
    return ""+
      "Til "+n+"\n\n"+
      "Innsynskrav etter offentleglova\n\n"+
      "Med hjemmel i offentleglova § 3 ber jeg om innsyn i følgende dokumenter:\n\n"+
      "1. Gjeldende avtale(r) med Microsoft eller annen skyleverandør om e-post, "+
        "Microsoft 365 / Office 365 og tilknyttede skytjenester, inkludert "+
        "databehandleravtale.\n"+
      "2. Personvernkonsekvensvurdering (DPIA) og eventuell risikovurdering for "+
        "bruken av disse tjenestene.\n"+
      "3. Vurdering av overføring av personopplysninger til tredjeland (USA) og "+
        "det rettslige grunnlaget for slik overføring etter personvernforordningen.\n\n"+
      "Jeg ber om innsyn i elektronisk form. Etter offentleglova § 29 skal kravet "+
        "avgjøres uten ugrunnet opphold. Krav som ikke er besvart innen fem "+
        "arbeidsdager regnes etter fast forvaltningspraksis som et avslag som kan "+
        "påklages. Ved helt eller delvis avslag ber jeg om en skriftlig begrunnelse "+
        "med henvisning til den bestemmelsen som er brukt, jf. offentleglova § 31, "+
        "og opplysning om klageadgang.\n\n"+
      "Med vennlig hilsen\n[Ditt navn]";
  }
  function buildKommuneForslag(k){
    var n = nameOf(k);
    return ""+
      "Forslag: Vedta en strategi for digital suverenitet\n\n"+
      "Vi ber kommunestyret i "+n+" om å vedta at digital suverenitet — at "+
        "innbyggernes data svarer til norsk og europeisk jurisdiksjon — skal vektes "+
        "som kriterium i kommunens IKT-anskaffelser, og at det utarbeides en plan for "+
        "å redusere avhengigheten av leverandører underlagt amerikansk jurisdiksjon "+
        "(CLOUD Act) der det finnes egnede europeiske alternativer.\n\n"+
      "Dette er et forslag om varig endring i kommunens innkjøps- og IKT-strategi, "+
        "ikke en kritikk av enkeltpersoner eller en enkeltstående IT-beslutning som "+
        "kan reverseres ved neste vedtak.";
  }
  function buildStatSporsmal(k){
    var n = nameOf(k);
    return ""+
      "Forslag til skriftlig spørsmål til ansvarlig statsråd:\n\n"+
      "«Hvilke planer har statsråden for å redusere "+n+" sin avhengighet av "+
        "programvare- og skytjenester underlagt amerikansk jurisdiksjon (CLOUD Act), "+
        "og vil statsråden sørge for at hensynet til digital suverenitet vektes som "+
        "kriterium i statlige IKT-anskaffelser, slik anskaffelsesregelverket allerede "+
        "åpner for?»\n\n"+
      "Dette gjelder en varig endring i anskaffelses- og IKT-strategi, ikke en "+
        "kritikk av enkeltpersoner.";
  }
  function mailto(subject, body){
    return "mailto:?subject="+encodeURIComponent(subject)+
      "&body="+encodeURIComponent(body);
  }
  function tool(title, lead, text, acts){
    return '<div class="panel funnel">'+
      '<h3>'+esc(title)+'</h3>'+
      '<p class="lead">'+lead+'</p>'+
      '<textarea class="tmpl" readonly aria-label="'+esc(title)+'">'+esc(text)+'</textarea>'+
      '<div class="acts">'+
        '<button type="button" class="copybtn">Kopier teksten</button>'+acts+
      '</div></div>';
  }
  function renderFunnel(k, catKey){
    var n = nameOf(k);
    var innsyn = buildInnsyn(k);
    var html = '<h2>Krev svar — verktøy for innbyggere</h2>'+
      '<p style="font-size:13px;color:var(--muted);margin:-6px 0 12px">Endringen som '+
        'varer ligger i <b>innkjøpsreglene og IKT-strategien</b>, ikke i et enkelt '+
        'vedtak som kan reverseres (lærdommen fra Münchens LiMux). Malene under er '+
        'ferdig utfylt med organets navn — kopiér, fyll inn ditt eget navn og send. '+
        'Vi lagrer ingenting om deg.</p>'+
      tool("1. Innsynskrav (offentleglova)",
        'Krev innsyn i organets Microsoft 365-/sky-avtale og personvern'+
          'konsekvensvurdering (DPIA). Etter <b>offentleglova § 29</b> skal kravet '+
          'avgjøres uten ugrunnet opphold — normalt innen <b>fem arbeidsdager</b>.',
        innsyn,
        '<a class="ext" href="'+mailto("Innsynskrav etter offentleglova", innsyn)+
          '">Åpne i e-post →</a>');
    if(catKey==="kommune"){
      var forslag = buildKommuneForslag(k);
      html += tool("2. Innbyggerforslag (minsak.no)",
        'Et innbyggerforslag med <b>300 underskrifter</b> (eller 2 % av innbyggerne) '+
          'forplikter kommunestyret til å ta stilling til saken, jf. <b>kommuneloven '+
          '§ 12-1</b>. Opprett forslaget på minsak.no.',
        forslag,
        '<a class="ext" href="https://www.minsak.no" target="_blank" rel="noopener">'+
          'Opprett på minsak.no →</a>');
    } else {
      var sporsmal = buildStatSporsmal(k);
      html += tool("2. Skriftlig spørsmål via Stortinget",
        'Innbyggerforslag gjelder bare kommuner. For et statlig organ er den '+
          'realistiske kanalen et <b>skriftlig spørsmål</b> til ansvarlig statsråd, '+
          'stilt av en stortingsrepresentant. Be en representant fra din valgkrets '+
          'om å fremme spørsmålet — finn representanten på stortinget.no — eller send '+
          'det direkte til '+esc(n)+' som en alminnelig henvendelse.',
        sporsmal,
        '<a class="ext" href="https://www.stortinget.no" target="_blank" '+
          'rel="noopener">Finn representant på stortinget.no →</a>');
    }
    return html;
  }

  // ---- Share card (issue #25): turn each visitor into a distributor -------
  // A screenshot-friendly card (entity name + email verdict + jurisdiction +
  // governance + the headline floor + skytilsynet.no). One-tap Web Share where
  // available, else copy the deep link; plus a fully client-side SVG -> canvas
  // -> PNG download (no external image service). The fact is the provocation —
  // no editorializing, no per-citizen data (rules 1, 5).
  var SC_COLOR = {red:"#ff6b6b", green:"#4dd6a0", "":"#eef2f6"};
  function shareGovLine(k){
    var g = k.governance; if(!g) return "";
    return "Styresett: "+(TIER_NO[g.tier]||g.tier)+" ("+g.country+")";
  }
  function shareStat(){
    return pct(COMBINED.us_pct)+" % av skannet offentlig sektor svarer til amerikansk jurisdiksjon";
  }
  function shareText(k){
    var m = platMeta(k);
    return nameOf(k)+" — e-post: "+m.label+" ("+m.juris+"). "+shareStat()+
      ". Kilde: Skybarometeret, skytilsynet.no";
  }
  function renderShareCard(k){
    var m = platMeta(k), gov = shareGovLine(k);
    return '<h2>Del faktumet</h2>'+
      '<p style="font-size:13px;color:var(--muted);margin:-6px 0 12px">Faktumet er '+
        'provokasjonen nok i seg selv. Del kortet — så fôrer det samfunnsdebatten. '+
        'Ingenting lagres om deg.</p>'+
      '<div class="sharewrap">'+
        '<figure class="sharecard" id="sharecard"'+
          ' data-name="'+esc(nameOf(k))+'" data-verdict="'+esc(m.label)+'"'+
          ' data-juris="'+esc(m.juris)+'" data-gov="'+esc(gov)+'"'+
          ' data-stat="'+esc(shareStat())+'" data-color="'+esc(SC_COLOR[m.vcls]||SC_COLOR[""])+'"'+
          ' data-text="'+esc(shareText(k))+'">'+
          '<figcaption class="sc-brand"><span class="dot" aria-hidden="true">●</span> '+
            'Skytilsynet · Skybarometeret</figcaption>'+
          '<div class="sc-name">'+esc(nameOf(k))+'</div>'+
          '<div class="sc-label">E-POST SVARER TIL</div>'+
          '<div class="sc-verdict '+m.vcls+'">'+esc(m.label)+'</div>'+
          '<div class="sc-juris">'+esc(m.juris)+'</div>'+
          (gov? '<div class="sc-gov">'+esc(gov)+'</div>' : "")+
          '<div class="sc-stat">'+esc(shareStat())+'</div>'+
          '<div class="sc-url">skytilsynet.no</div>'+
        '</figure>'+
        '<div class="acts">'+
          '<button type="button" class="copybtn sharebtn" data-act="share">Del ↗</button>'+
          '<button type="button" class="copybtn sharebtn" data-act="copy">Kopier lenke</button>'+
          '<button type="button" class="copybtn sharebtn" data-act="img">Last ned bilde</button>'+
        '</div>'+
      '</div>';
  }
  // Render the card as a self-contained SVG (system fonts only, no external
  // load) for the PNG download. Long names step down a size so they fit 1200px.
  function cardSvg(c){
    var W = 1200, H = 630, x = 72, nameSize = c.name.length > 24 ? 56 : 84;
    function txt(y, size, fill, weight, s, extra){
      return '<text x="'+x+'" y="'+y+'" fill="'+fill+'" font-family="-apple-system,'+
        'BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif" font-size="'+size+
        '" font-weight="'+weight+'"'+(extra||"")+'>'+esc(s)+'</text>';
    }
    var y = c.gov ? 470 : 480;
    return '<svg xmlns="http://www.w3.org/2000/svg" width="'+W+'" height="'+H+'" '+
      'viewBox="0 0 '+W+' '+H+'">'+
      '<rect width="'+W+'" height="'+H+'" fill="#0a0d11"/>'+
      '<rect x="20" y="20" width="'+(W-40)+'" height="'+(H-40)+'" rx="24" fill="#13202b" '+
        'stroke="#384654"/>'+
      txt(110, 28, "#7d909f", 400, "●  Skytilsynet · Skybarometeret")+
      txt(110+nameSize+24, nameSize, "#eef2f6", 700, c.name)+
      txt(c.gov?330:340, 26, "#7d909f", 400, "E-POST SVARER TIL", ' letter-spacing="3"')+
      txt(c.gov?400:415, 60, c.color, 700, c.verdict)+
      txt(c.gov?445:465, 32, "#a3b6c6", 400, c.juris)+
      (c.gov? txt(495, 28, "#a3b6c6", 400, c.gov) : "")+
      '<line x1="'+x+'" y1="'+y+'" x2="'+(W-72)+'" y2="'+y+'" stroke="#2a343f"/>'+
      txt(y+44, 26, "#eef2f6", 400, c.stat)+
      txt(H-56, 34, "#5cb3ff", 700, "skytilsynet.no")+
      '</svg>';
  }
  function downloadCard(card){
    var c = {name:card.getAttribute("data-name"), verdict:card.getAttribute("data-verdict"),
      juris:card.getAttribute("data-juris"), gov:card.getAttribute("data-gov"),
      stat:card.getAttribute("data-stat"), color:card.getAttribute("data-color")};
    var blob = new Blob([cardSvg(c)], {type:"image/svg+xml;charset=utf-8"});
    var url = URL.createObjectURL(blob), img = new Image();
    img.onload = function(){
      var cv = document.createElement("canvas");
      cv.width = 1200; cv.height = 630;
      cv.getContext("2d").drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      cv.toBlob(function(b){
        var a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = "skybarometeret-"+slug(c.name)+".png";
        a.click();
        setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1000);
      });
    };
    img.src = url;
  }

  function renderDetail(k, catKey){
    var m = platMeta(k);
    var resid = (k.platform==="EU_SOVEREIGN")
      ? "Norge / EØS — under europeisk rettsvern"
      : (k.platform==="OTHER" || k.platform==="NONE")
        ? "Ikke avgjort fra DNS alene"
        : "Avhenger av oppsett, men operatøren er underlagt CLOUD Act uansett lagringssted "+
          "(EU-region opphever ikke jurisdiksjonen)";
    var alt = k.alternative
      ? esc(k.alternative)
      : (k.platform==="EU_SOVEREIGN" ? "Allerede på europeisk/norsk drift" : "—");
    var flagsHtml = (k.flags||[]).map(function(f){
      return '<div class="flag">'+esc(FLAG_NO[f]||f)+'</div>'; }).join("");
    var kind = catKey==="stat" ? "Statlig organ" : "Kommune";
    var backLabel = catKey==="stat" ? "← Alle statlige organ" : "← Alle kommuner";

    var v = document.getElementById("view-detail");
    v.innerHTML =
      '<span class="back" id="back">'+esc(backLabel)+'</span>'+
      '<span class="badge">'+esc(kind)+'</span>'+
      '<h1>'+esc(nameOf(k))+'</h1>'+
      '<p class="tagline" style="font-size:16px">E-postdomene: <code>'+esc(k.domain||"—")+'</code></p>'+
      '<div class="facts">'+
        renderVerdict(k)+
        fact("Operatørens jurisdiksjon", esc(m.juris), m.vcls)+
        govFact(k)+
        fact("Datas oppholdssted", resid)+
        fact("Kontraktsverdi", "Ikke kartlagt (denne aksen dekker kun e-post via DNS)")+
      '</div>'+
      renderShareCard(k)+
      (flagsHtml? '<h2 style="margin-top:0">Forbehold</h2>'+flagsHtml : "")+
      '<h2>Anbefalt europeisk alternativ</h2>'+
      '<div class="panel"><p style="margin:0">'+alt+'. Se byttekartet og '+
        'fallgruvene for suverenitetsvasking på forsiden.</p></div>'+
      '<h2>Evidens — Vis hvordan vi vet det</h2>'+
      '<p style="font-size:13px;color:var(--muted);margin:-6px 0 12px">Hvert signal under '+
        'bærer sin egen kilde (den eksakte spørringen) og dato. Det er hele '+
        'troverdighetsgrunnlaget: ingen påstand uten kilde.</p>'+
      renderTrail(k.evidence)+
      '<p style="font-size:13px;color:var(--muted);margin-top:10px">Kilde: offentlig DNS, '+
        'målt '+esc(noDate(k.sourceDate||DB.meta.sourceDate))+'. Datasett: CC BY 4.0.</p>'+
      renderWebAxis(k)+
      renderFunnel(k, catKey);
  }

  // ---- Routing ------------------------------------------------------------
  function route(){
    var hash = location.hash.replace(/^#/,"");
    var m = hash.match(/^org\/([^/]+)\/(.+)$/);
    var home = document.getElementById("view-home");
    var detail = document.getElementById("view-detail");
    var rest = document.getElementById("static-rest");
    if(m){
      var k = bySlug(m[1], m[2]);
      if(k){ renderDetail(k, m[1]); home.classList.add("hidden"); rest.classList.add("hidden");
        detail.classList.remove("hidden"); window.scrollTo(0,0); return; }
    }
    detail.classList.add("hidden"); home.classList.remove("hidden"); rest.classList.remove("hidden");
  }

  // ---- Wiring -------------------------------------------------------------
  function noDate(iso){
    if(!iso) return "";
    var mo=["","januar","februar","mars","april","mai","juni","juli","august",
      "september","oktober","november","desember"];
    var p=iso.split("-"); return parseInt(p[2],10)+". "+mo[parseInt(p[1],10)]+" "+p[0];
  }
  var totalAll = CATS.reduce(function(n,c){ return n + c.summary.total; }, 0);
  document.getElementById("method-note").innerHTML =
    "Hvert organ er klassifisert ut fra offentlig DNS (MX + SPF + autodiscover-fingeravtrykk, "+
    "med DKIM/SPF-IP/getuserrealm-avdekking for maskerte bakender), målt "+
    esc(noDate(DB.meta.sourceDate))+". "+esc(totalAll)+" organ totalt — "+
    CATS.map(function(c){ return esc(c.summary.total)+" "+esc(c.label.toLowerCase()); }).join(" + ")+
    ". Statlige organ kommer fra Brønnøysund Enhetsregisteret.";
  // Om & Metode: state the current methodology version (baked from the latest
  // snapshot, never hardcoded). Em-dash stays if there are too few snapshots.
  var mvEl = document.getElementById("methodology-version");
  if(mvEl && DB.methodology_version) mvEl.textContent = "v"+DB.methodology_version;

  document.getElementById("q").addEventListener("input", function(e){
    state.q = e.target.value.trim().toLowerCase(); renderGrid(); });
  document.getElementById("filters").addEventListener("click", function(e){
    var f = e.target.getAttribute("data-f"); if(!f) return;
    state.filter = f; renderFilters(); renderGrid(); });
  document.getElementById("catbar").addEventListener("click", function(e){
    var t = e.target.closest(".cattab"); if(!t) return;
    state.cat = t.getAttribute("data-c"); renderCatbar(); renderGrid(); });
  document.getElementById("grid").addEventListener("click", function(e){
    var btn = e.target.closest(".cell"); if(!btn) return;
    location.hash = "org/"+btn.getAttribute("data-c")+"/"+btn.getAttribute("data-k"); });
  document.addEventListener("click", function(e){
    if(e.target && e.target.id==="back") history.length>1 ? history.back() : (location.hash=""); });
  // Copy a funnel template to the clipboard (no data leaves the browser).
  document.addEventListener("click", function(e){
    var btn = e.target.closest && e.target.closest(".copybtn"); if(!btn) return;
    var ta = btn.closest(".funnel").querySelector("textarea");
    var done = function(){ btn.classList.add("ok"); btn.textContent = "Kopiert ✓";
      setTimeout(function(){ btn.classList.remove("ok"); btn.textContent = "Kopier teksten"; }, 2000); };
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(ta.value).then(done, function(){ ta.select(); });
    } else { ta.select(); document.execCommand("copy"); done(); }
  });
  // Share card (issue #25): Web Share API where available, else copy the deep
  // link; "last ned bilde" rasterizes the card client-side. No data leaves the
  // browser beyond what the user explicitly shares.
  document.addEventListener("click", function(e){
    var btn = e.target.closest && e.target.closest(".sharebtn"); if(!btn) return;
    var card = document.getElementById("sharecard"); if(!card) return;
    var act = btn.getAttribute("data-act");
    var flash = function(msg){ var o = btn.textContent; btn.classList.add("ok");
      btn.textContent = msg;
      setTimeout(function(){ btn.classList.remove("ok"); btn.textContent = o; }, 2000); };
    var copyLink = function(){
      var url = location.href;
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(url).then(function(){ flash("Lenke kopiert ✓"); },
          function(){ window.prompt("Kopier lenken:", url); });
      } else { window.prompt("Kopier lenken:", url); }
    };
    if(act==="share"){
      if(navigator.share){
        navigator.share({title:"Skybarometeret — "+card.getAttribute("data-name"),
          text:card.getAttribute("data-text"), url:location.href}).catch(function(){});
      } else { copyLink(); }
    } else if(act==="copy"){ copyLink(); }
    else if(act==="img"){ downloadCard(card); }
  });
  window.addEventListener("hashchange", route);

  renderHero(); renderGoal(); renderCatbar(); renderFilters(); renderGrid(); route();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
