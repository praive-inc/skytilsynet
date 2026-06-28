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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "kommune-email-sovereignty.latest.json")
STAT_DATA = os.path.join(ROOT, "data", "statlige-organ-email-sovereignty.latest.json")
HISTORY = os.path.join(ROOT, "scanner", "history.json")
SNAP_DIR = os.path.join(ROOT, "scanner", "snapshots")
OUT = os.path.join(HERE, "index.html")

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


def compute_trend(old, new):
    """The honest per-kommune movement between two snapshots.

    We count actual platform changes (who left / joined Microsoft), not the
    aggregate count delta — the latter conflates real migrations with DNS
    measurement refinement (a kommune moving OTHER -> US_MICROSOFT because its
    backend got unmasked did not "join" Microsoft, we just saw it clearly).
    Over a short window most movement is the latter; the copy says so.
    """
    if not old or not new:
        return None
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
    return c


def build_html(data, history, trend, stat=None):
    """Render the full single-file site. Pure: same inputs -> same output.

    `data` is the kommune dataset; `stat` (optional) the statlige-organ dataset.
    The two are baked as categories with their own summaries; the headline is the
    COMBINED scanned public sector."""
    categories = [{
        "key": "kommune", "label": "Kommuner",
        "summary": data["summary"], "entities": normalize(data["kommuner"]),
    }]
    if stat:
        categories.append({
            "key": "stat", "label": "Statlige organ",
            "summary": stat["summary"], "entities": normalize(stat["organ"]),
        })
    payload = {
        "meta": data["meta"],
        "combined": combine_summaries([c["summary"] for c in categories]),
        "categories": categories,
        "history": history,
        "trend": trend,
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # </script> can't appear literally inside an inline script.
    blob = blob.replace("</", "<\\/")
    return _TEMPLATE.replace("/*__DATA__*/", blob)


def main():
    data = json.load(open(DATA))
    stat = json.load(open(STAT_DATA)) if os.path.exists(STAT_DATA) else None
    history = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    old, new = load_snapshots()
    trend = compute_trend(old, new)
    html = build_html(data, history, trend, stat)
    with open(OUT, "w") as f:
        f.write(html)
    n_stat = len(stat["organ"]) if stat else 0
    print(f"Wrote {OUT} ({len(html):,} bytes, {len(data['kommuner'])} kommuner "
          f"+ {n_stat} statlige organ)")


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
  :root{
    --bg:#0f1419; --surface:#171e26; --line:#26303b;
    --fg:#eef2f6; --muted:#9bb0c2; --accent:#3ea6ff;
    --red:#ff5d5d; --green:#46d39a; --amber:#f0b46a; --grey:#6b7d8f;
    --maxw:1040px;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{
    background:var(--bg); color:var(--fg);
    font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{max-width:var(--maxw);margin:0 auto;padding:32px 24px 96px}
  .badge{display:inline-block;font-size:13px;letter-spacing:.08em;text-transform:uppercase;
    color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:4px 12px;margin-bottom:18px}
  h1{font-size:clamp(30px,5vw,46px);line-height:1.05;margin:0 0 6px;letter-spacing:-.02em}
  .tagline{font-size:clamp(17px,3vw,21px);color:var(--muted);margin:0 0 28px;font-weight:400}
  h2{font-size:14px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:52px 0 14px;font-weight:600}
  p{margin:0 0 14px}
  /* Disclaimer — load-bearing, always rendered above every view (CLAUDE.md rule 2) */
  .disclaimer{background:#1c1410;border:1px solid #5c3a26;border-radius:14px;padding:18px 22px;margin:0 0 28px}
  .disclaimer strong{color:#ffd9a8}
  .disclaimer p{font-size:14px;color:#f1e3d5;margin:0}
  .hero{display:grid;grid-template-columns:1fr;gap:18px;margin:0 0 8px}
  @media(min-width:720px){.hero{grid-template-columns:1.1fr .9fr}}
  .stat{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:24px}
  .stat .big{font-size:clamp(38px,8vw,60px);font-weight:700;line-height:1;letter-spacing:-.03em;color:var(--red)}
  .stat .cap{color:var(--muted);font-size:15px;margin-top:8px}
  .stat .src{color:var(--muted);font-size:12px;margin-top:12px;opacity:.85}
  .trend .big{color:var(--fg);font-size:clamp(22px,4vw,30px)}
  .trend .row{font-size:14px;margin-top:6px}
  .trend .green{color:var(--green)} .trend .red{color:var(--red)}
  .spark{display:flex;gap:3px;align-items:flex-end;height:42px;margin-top:14px}
  .spark .bar{flex:1;background:var(--red);border-radius:2px 2px 0 0;min-height:3px}
  .spark .lab{font-size:11px;color:var(--muted)}
  /* Category toggle (Kommuner | Statlige organ) */
  .catbar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}
  .cattab{background:var(--surface);border:1px solid var(--line);border-radius:10px;
    color:var(--muted);padding:9px 16px;font-size:14px;font-weight:600;cursor:pointer;user-select:none}
  .cattab.on{color:var(--fg);border-color:var(--accent);background:#10202e}
  .cattab .pct{color:var(--red);font-weight:700}
  /* Controls */
  .controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 14px}
  input[type=search]{background:var(--surface);border:1px solid var(--line);border-radius:10px;
    color:var(--fg);padding:10px 14px;font-size:15px;min-width:220px;flex:1}
  .filters{display:flex;gap:6px;flex-wrap:wrap}
  .chip{background:var(--surface);border:1px solid var(--line);border-radius:999px;color:var(--muted);
    padding:6px 12px;font-size:13px;cursor:pointer;user-select:none}
  .chip.on{color:var(--fg);border-color:var(--accent)}
  .legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin:0 0 14px}
  .legend .sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:middle}
  /* Grid "map": one colored tile per kommune */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
  .cell{background:var(--surface);border:1px solid var(--line);border-left-width:4px;border-radius:10px;
    padding:10px 12px;cursor:pointer;text-align:left;color:var(--fg);font:inherit;overflow:hidden}
  .cell:hover{border-color:var(--accent)}
  .cell .nm{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cell .pl{font-size:12px;color:var(--muted);margin-top:2px}
  .cell .fl{font-size:11px;color:var(--amber);margin-top:3px}
  .c-red{border-left-color:var(--red)} .c-green{border-left-color:var(--green)}
  .c-amber{border-left-color:var(--amber)} .c-grey{border-left-color:var(--grey)}
  .count{color:var(--muted);font-size:13px;margin:0 0 10px}
  /* Detail */
  .back{display:inline-block;margin:0 0 14px;font-size:14px;cursor:pointer;color:var(--accent)}
  .facts{display:grid;grid-template-columns:1fr;gap:10px;margin:0 0 18px}
  @media(min-width:640px){.facts{grid-template-columns:1fr 1fr}}
  .fact{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .fact .k{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
  .fact .v{font-size:18px;font-weight:600;margin-top:4px}
  .fact .v.red{color:var(--red)} .fact .v.green{color:var(--green)}
  .evidence{background:#0b0f13;border:1px solid var(--line);border-radius:12px;padding:6px 16px;
    font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#cdd9e3;
    overflow-x:auto}
  .evidence .lbl{color:var(--muted)}
  /* One evidence row per signal — observation, inference, source + date. */
  .ev{padding:11px 0;border-bottom:1px solid var(--line);white-space:pre-wrap;word-break:break-word}
  .ev:last-child{border-bottom:0}
  .ev.hl{background:#161f17;margin:0 -16px;padding-left:16px;padding-right:16px;border-left:3px solid var(--green)}
  .ev .sig{display:inline-block;min-width:84px;color:var(--accent);font-weight:600;text-transform:uppercase;font-size:11px}
  .ev .obs{color:#eef2f6}
  .ev .inf{display:block;color:var(--muted);margin-top:3px}
  .ev .conf{color:var(--amber)}
  .ev .src{display:block;color:#6b7d8f;margin-top:3px;font-size:11px}
  .ev .note{color:var(--amber);margin-top:3px}
  .verdict .conf{color:var(--amber);font-size:14px;font-weight:400}
  .verdict .note{display:block;font-size:13px;color:var(--muted);font-weight:400;margin-top:5px}
  /* Switch map + benchmark */
  .panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin:0 0 14px}
  .panel h3{margin:0 0 8px;font-size:17px}
  .panel .arrow{color:var(--accent)}
  .flag{border-left:3px solid var(--amber);padding-left:12px;margin:10px 0;font-size:14px;color:#f1e3d5}
  .flag b{color:var(--amber)}
  table.switch{width:100%;border-collapse:collapse;font-size:14px}
  table.switch td,table.switch th{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
  table.switch th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.05em;text-transform:uppercase}
  .en{border-top:1px solid var(--line);margin-top:56px;padding-top:24px;color:var(--muted);font-size:14px}
  .en strong{color:var(--fg)}
  footer{margin-top:40px;color:var(--muted);font-size:14px}
  .dot{color:var(--green)}
  .hidden{display:none}
</style>
</head>
<body>
<div class="wrap">

  <!-- DISCLAIMER: rendered once, outside the routed views, so it is present on
       every "page" (landing and per-kommune detail). Never remove. -->
  <div class="disclaimer">
    <p><strong>⚠️ Skytilsynet er ikke et offentlig organ.</strong>
      Vi er ikke tilknyttet, drevet av eller godkjent av norske myndigheter,
      Datatilsynet, Digitaliseringsdirektoratet eller noen annen statlig eller
      kommunal etat. Navnet beskriver hva vi gjør i overført betydning — vi følger
      med på offentlig sektors avhengighet av skytjenester — og er ikke en offisiell
      rolle. All informasjon er hentet fra åpne kilder og presenteres faktabasert
      og nøytralt. <a href="#kilde">Metode og kilder ↓</a></p>
  </div>

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

    <h2 id="grid-title">Hele offentlig sektor</h2>
    <div class="catbar" id="catbar"></div>
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

    <h2 id="kilde">Metode og forbehold</h2>
    <div class="panel">
      <p id="method-note"></p>
      <p style="font-size:13px;color:var(--muted)"><b>E-post er én akse.</b> Tallet
        er et <b>gulv, ikke et tak</b>: noen kommuner ligger bak en e-postgateway
        der vi ikke har avdekket bakomliggende plattform — den reelle USA-andelen
        er minst så høy som vist. Datasettet er åpent (CC BY 4.0) og hver rad bærer
        sin kilde og dato. <a href="https://github.com/praive-inc/skytilsynet">Kode og metode</a>.</p>
    </div>

    <div class="en">
      <p><strong>About this site (English).</strong> Skybarometeret tracks which
      jurisdiction Norwegian municipalities' email answers to, derived from public
      DNS. <strong>Skytilsynet is an independent project and is
      not a government body, not affiliated with, operated by, or endorsed by any
      Norwegian public authority</strong> (including Datatilsynet). All data is drawn from publicly
      available sources and presented factually. Open data (CC BY 4.0); every row
      carries its source and date.</p>
    </div>

    <footer>
      <span class="dot">●</span> Et prosjekt fra BetterWorld · skytilsynet.no ·
      <a href="https://github.com/praive-inc/skytilsynet">åpen kildekode</a>
    </footer>
  </section>
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
      return '<div class="bar" style="height:'+(v/max*100)+'%" title="'+
        esc(p.date)+': '+v+' %"></div>';
    }).join("");
    return '<div class="spark">'+bars+'</div>'+
      '<div class="lab">Microsoft-andel (kommuner), '+esc(h[0].date)+' → '+esc(h[h.length-1].date)+'</div>';
  }

  // ---- Category toggle + grid --------------------------------------------
  function renderCatbar(){
    var bar = document.getElementById("catbar");
    if(CATS.length < 2){ bar.style.display = "none"; return; }
    bar.innerHTML = CATS.map(function(c){
      return '<span class="cattab'+(state.cat===c.key?" on":"")+'" data-c="'+esc(c.key)+'">'+
        esc(c.label)+' <span class="pct">'+pct(c.summary.microsoft_pct)+' %</span></span>';
    }).join("");
  }
  function renderFilters(){
    document.getElementById("filters").innerHTML = FILTERS.map(function(f){
      return '<span class="chip'+(state.filter===f.key?" on":"")+'" data-f="'+f.key+'">'+
        esc(f.label)+'</span>';
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
    var evLines = [];
    (ev.mx||[]).forEach(function(x){ evLines.push('<span class="lbl">MX  </span>'+esc(x)); });
    if(ev.spf) evLines.push('<span class="lbl">SPF </span>'+esc(ev.spf));
    if(ev.autodiscover) evLines.push('<span class="lbl">AUTO</span>'+esc(ev.autodiscover));
    if(ev.dkim) evLines.push('<span class="lbl">DKIM</span>'+esc(ev.dkim));
    if(ev.realm) evLines.push('<span class="lbl">REALM</span>'+esc(ev.realm));
    if(!evLines.length) evLines.push("(ingen MX/SPF/autodiscover-poster funnet)");
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
        'målt '+esc(noDate(k.sourceDate||DB.meta.sourceDate))+'. Datasett: CC BY 4.0.</p>';
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
  window.addEventListener("hashchange", route);

  renderHero(); renderCatbar(); renderFilters(); renderGrid(); route();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
