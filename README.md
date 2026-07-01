# Skytilsynet

**A live, independent tracker of how dependent Norwegian public bodies are on
foreign cloud technology — and a nudge in the right direction.**

Skytilsynet maps which cloud services Norwegian municipalities (and, later, other
public bodies) rely on — starting with email — derives the jurisdiction that data
answers to, and points to European alternatives. Every figure links to its
evidence. The point is not to shame; it is to make visible something that is
today invisible, so citizens, employees, and elected officials can make informed
choices.

> ⚠️ **Skytilsynet is not a government body.** It is an independent, non-profit
> transparency project by [BetterWorld](https://github.com/praive-inc/betterworld).
> It is **not affiliated with, operated by, or endorsed by** any Norwegian public
> authority (including Datatilsynet). The name describes our watchdog function
> rhetorically; it is not an official title. See [`web/index.html`](web/index.html)
> for the full disclaimer shown to the public.

## The finding (2026-06-27 baseline)

A zero-cost scan of all 358 municipalities by email platform:

| Platform | Share |
|---|---|
| **Microsoft 365** | **90.2%** |
| US hyperscaler (Microsoft + Google) | **91.3%** |
| Genuinely non-US infrastructure | ~8% (30 kommuner) |

This independently corroborates digitaliseringsminister Karianne Tung's "~75% of
public-sector software is Microsoft" — at the email layer the concentration is
even higher.

## Layout

```
scanner/   the DNS pipeline (MX + SPF + autodiscover) + dated snapshots + history
web/       the public site — the live tracker (Skybarometeret), built by build.py
data/      the published open dataset (CC BY 4.0)
docs/      methodology / scope spec (originated as BetterWorld RFC-017)
```

## Run the scanner

```bash
cd scanner
python3 scan.py          # writes snapshots/<date>.json + appends history.json
python3 transition.py    # shows the trend + which kommuner moved since last run
```

Needs `dig`. No auth, no cost — public DNS only.

## Build the public site

```bash
cd web
python3 build.py         # regenerates web/index.html from data/ + scanner/
python3 -m unittest      # tests the build (trend logic + rendered output)
```

`build.py` bakes a **light per-entity summary** + the aggregate history and the
honest trend inline as JSON in `index.html` (small first paint); the **heavy
per-entity evidence loads on demand** from `web/data/detail-<kategori>.json` via a
**same-origin** fetch (our own EU origin). No CDN, web fonts, map tiles or any
external/US-managed serving dependency — RFC-001 P5 forbids *external* deps, not a
fetch from our own origin. `build.py` writes both `index.html` and the
`web/data/detail-*.json` files — commit them together.

Deploy is a plain rsync of `web/` behind Caddy (no build step in prod — see
[`deploy/deploy-local.sh`](deploy/deploy-local.sh)), so the generated
`index.html` is committed. Re-run `build.py` after each scan to refresh the page;
the operator wires that alongside the scheduled `scan.py` (the agent does not add
GitHub Actions — see [`CLAUDE.md`](CLAUDE.md)).

## Relationship to BetterWorld

Skytilsynet is the **public-sector surface** of BetterWorld's "one engine, many
surfaces" architecture (RFC-001). The **scoring engine, ownership graph, and
alternatives data remain BetterWorld's** — this repo owns the scanner and the
presentation. When Skytilsynet grows past the email axis into the full multi-axis
SovereigntyScore, it will *consume* BetterWorld's engine via an API or shared
package, never fork the scoring logic.

## Licensing

- **Code:** MIT (see [`LICENSE`](LICENSE)).
- **Data:** CC BY 4.0 (see [`data/LICENSE`](data/LICENSE)) — open by default,
  per BetterWorld RFC-001 Principle 6.
