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
web/       the public site (holding page now → live tracker)
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
