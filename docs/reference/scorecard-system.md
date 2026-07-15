# Skytilsynet — system reference

> **What this is.** The operator-facing living overview of Skytilsynet as it
> exists today: what each service and feature does, and how it is run. It is the
> reference layer (RFC-005) — present-tense fact, kept in sync with the code.
>
> For *why* the system is shaped this way — the strategic bet, the trust posture,
> the guardrails — see the RFC it grew out of:
> [`../scorecard-spec.md`](../scorecard-spec.md). When the two disagree, this doc
> is authoritative on *what is*; the RFC on *why*.

Skytilsynet is a static public tracker plus a small intake backend. Everything
citizen-facing is a committed static site behind Caddy on an EU origin; the only
moving server part is the FOI intake service. There is no client-side business
logic and no external/US serving dependency (RFC-001 P5).

---

## The pipeline at a glance

```
scanner/  →  data/  →  web/build.py  →  web/index.html + web/data/*.json  →  Caddy
                ↑                                    ↑
      data/*.csv (curated)              server/foi_intake.py (operator-reviewed
      data/*-auto.json (probe)           FOI answers feed data/saksbehandling.csv)
```

1. **Scanners** read public DNS / web signals and write dated JSON snapshots to
   `data/`.
2. **`web/build.py`** joins those snapshots with the curated CC-BY data files and
   bakes the whole site (`web/index.html`, `web/en/index.html`, and the on-demand
   `web/data/detail-*.json`).
3. **Deploy** is an rsync of `web/` behind Caddy — no build step in prod, so the
   generated files are committed.
4. The **FOI intake backend** collects innsyn answers from the public; the
   operator reviews each by hand and promotes accepted ones into the curated data,
   which the next build renders.

---

## Bodies covered

Five public-body categories are scanned and scored, each with its own dated
snapshot under `data/`:

| Category | Snapshot |
|---|---|
| Kommuner (municipalities) | `data/kommune-email-sovereignty.latest.json` |
| Fylkeskommuner (counties) | `data/fylkeskommune-email-sovereignty.latest.json` |
| Helseforetak (health trusts) | `data/helseforetak-email-sovereignty.latest.json` |
| Statlige organ (state agencies) | `data/statlige-organ-email-sovereignty.latest.json` |
| UH-sektor (higher education) | `data/uh-sektor-email-sovereignty.latest.json` |

The web-infrastructure axis is scanned for kommuner
(`data/kommune-web-sovereignty.latest.json`); the saksbehandling/arkiv probe spans
all five categories.

---

## The axes

Three axes are measured. The suverenitetsscore is built from the first two plus a
governance term; the saksbehandling/arkiv axis is rendered separately and never
enters the score.

### Email-platform jurisdiction (`scanner/scan.py`)
The MVP axis: MX + SPF + autodiscover fingerprint → email platform → the
jurisdiction that platform answers to. The classifier is one readable file
(`scanner/scan.py`) and encodes the sovereignty-washing traps (EU-located ≠
EU-owned; UK/CH non-EU jurisdiction; OnlyOffice Russian heritage). Every verdict
carries its evidence records and an `observed_at`.

### Web infrastructure (`scanner/web_scan.py`)
Host jurisdiction plus the fraction of page resources served from US-managed
infrastructure. Feeds the web sub-score.

### Saksbehandling / arkiv (`scanner/saksarkiv_probe.py`)
Which NOARK-5 sakarkiv vendor a body runs and the jurisdiction its hosting answers
to. The vendor comes from two merged sources — the curated
`data/saksbehandling.csv` (manual/FOI rows, which always win) and the
machine-generated `data/saksbehandling-auto.json` from the innsyn-portal
fingerprint probe. Hosting is either *utledet* (inferred from `VENDOR_HOSTING` in
`build.py`) or *bekreftet* (confirmed against a re-checkable source the operator
recorded). A fingerprint identifies the *vendor* only, never hosting.

---

## The suverenitetsscore (`web/build.py`)

`build.py` derives a 0–100 score (higher = more sovereign) as Skytilsynet's
*presentation* of the axes it already cites — **not** a fork of BetterWorld's
SovereigntyScore engine. The weights are `SCORE_WEIGHTS` in `web/build.py`:

| Axis | Weight |
|---|---|
| Email-platform jurisdiction | **60 %** |
| Web infrastructure | **25 %** |
| Governance of the operator's jurisdiction | **15 %** |

Each axis is a 0.0–1.0 sub-score; the entity card shows every term as
*weight × delscore → poeng* behind "Vis formelen". An axis with no measurement is
dropped and the remaining weights renormalise — never counted as a silent zero.
Every entity also carries `nationalRank` / `nationalTotal` (standard competition
ranking) and a per-entity email-platform trend across snapshots.

---

## The public site (`web/`)

`web/build.py` regenerates `web/index.html` from `data/` + the scanners. It bakes
a light per-entity summary and the aggregate history inline (small first paint)
and loads the heavy per-entity evidence on demand from
`web/data/detail-<kategori>.json` via a **same-origin** fetch. It also writes an
English press page at `web/en/index.html` (served at `/en/`), `hreflang`-linked
both ways.

The site includes the Norway maps (a real geographic choropleth from committed
Kartverket-derived geometry, plus an equal-area hex cartogram — toggle degrades to
the geographic map with no JS), a league table with hall-of-fame / most-dependent,
and the trust-armor material (corrections log `data/corrections.json`,
saksbehandling change log `data/saksbehandling-endringslogg.json`, named
methodology author, "hva dette IKKE beviser" box).

**Build + verify locally:**

```bash
cd web
python3 build.py         # regenerates index.html + web/data/detail-*.json + en/
python3 -m unittest      # trend logic + rendered output
```

`build.py` writes `index.html`, the `web/data/detail-*.json` files and
`en/index.html` — commit them together.

---

## The FOI intake backend (`server/foi_intake.py`)

The only live server component. It collects innsyn answers so the saksbehandling
axis can move a body's hosting to *bekreftet*.

- **`POST /api/foi`** accepts a submission (JSON or form-encoded, no auth). The
  per-entity "Send oss svaret" form and the standalone `/bidra` page (works
  without JS, 303-redirects) post here.
- **`GET /api/foi/pending`** returns the operator review queue (JSON or
  `?format=csv`), guarded by `FOI_OPERATOR_TOKEN`.

Submissions are stored **inert** in SQLite — domain-whitelisted, capped, honeypot-
and throttle-guarded. See [`../../server/README.md`](../../server/README.md) for
config and how it runs — the `skytilsynet-foi` compose service (with a host
systemd unit as the alternative).

**Privacy controls (issue #114).** In addition to storing no requester identity
and hashing ip+ua for the throttle:

- **Retention:** rows are purged 30 days after an accept/reject decision, with a
  180-day backstop for undecided rows. `purge_expired()` runs on server start and
  via `foi_review.py purge` — nothing persists indefinitely.
- **Minimization:** e-mail addresses are stripped from the free-text `source`/`note`
  at intake, and the form warns against pasting personal data.
- **Encryption at rest:** set `FOI_ENCRYPTION_KEY` to field-encrypt `source`/`note`
  with Fernet (opt-in; plaintext default keeps the stdlib-only deploy dependency-free).
- **Operator-access audit:** every review read/decision is logged to an
  `operator_access_log` table (who/when/what).

> **Binding security rule.** FOI submissions are untrusted public input, stored
> for human review only, and **MUST NEVER enter any agent/LLM workflow**
> (prompt-injection safety). No per-citizen data is retained (RFC-007 aggregate
> discipline).

### Operator review (`scripts/foi_review.py`)

The operator reviews every row by hand — nothing auto-promotes:

```bash
python3 scripts/foi_review.py list          # the queue (--all to include handled)
python3 scripts/foi_review.py show <id>     # one submission
python3 scripts/foi_review.py accept <id>   # emit a saksbehandling.csv row → bekreftet
python3 scripts/foi_review.py reject <id>
python3 scripts/foi_review.py purge         # delete rows past their retention window
```

`accept` writes a `data/saksbehandling.csv` row that flips the body's hosting to
*bekreftet*; the next `build.py` renders it.

---

## Deploy

Deploy is a plain rsync of `web/` behind Caddy — no build step in prod, so the
generated `index.html` and `web/data/*.json` are committed. The FOI backend runs
as the `skytilsynet-foi` compose container behind Caddy;
[`deploy/deploy-local.sh`](../../deploy/deploy-local.sh) swaps the static files,
syncs the backend (`server/` **plus the top-level `shared/` package** it imports —
`server/foi_intake.py` does `from shared.csv_safe import csv_safe`, so `shared/`
must ship alongside `server/` or the container crashes at import on restart, #110),
and restarts the service each deploy. Re-run `build.py` after
each scan to refresh the page; the operator wires that alongside the scheduled
`scan.py` (the agent does not add GitHub Actions).

---

## Data & licensing

Public-entity scoring is **public reference data**, not user data (CC-BY core;
open by default per RFC-001 P6). Scores are versioned point-in-time snapshots;
every score row carries its `sourceUrl` / `sourceDate`. Code is MIT, data is
CC BY 4.0 (`data/LICENSE`).
