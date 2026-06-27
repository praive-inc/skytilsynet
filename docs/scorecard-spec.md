# Skytilsynet — scorecard scope & methodology

> **Lineage:** this began as **BetterWorld RFC-017**. It moved here when the
> public-sector scorecard was split into its own repo + surface. The scoring
> engine it depends on (sovereignty/democracy axes, ownership graph) stays in
> [BetterWorld](https://github.com/praive-inc/betterworld); this repo owns the
> scanner ([`../scanner/`](../scanner/)) and the presentation.

**Status:** Draft
**Author:** BetterWorld / Skytilsynet
**Created:** 2026-06-27
**Depends on (BetterWorld RFCs):** RFC-001 (vision, seven principles), RFC-006 (sovereignty scoring), RFC-011 (entity SovereigntyScore), RFC-016 (Norway-first market config)
**Strategy:** BetterWorld `docs/strategy/making-the-world-better-2026-06.md` §5

---

## Why this RFC

BetterWorld's leverage ladder is *me → my workplace → my institutions*. The top
rung is the highest-leverage: a citizen who moves one municipality off Microsoft
365 redirects more spending and sets more precedent than thousands of individual
consumer swaps. This RFC specifies a **distinct public-facing surface** — a
Norway-first scorecard ranking public bodies (municipalities first) on digital
sovereignty — and the activism funnel that turns a passive ranking into pressure.

It is a **separate surface from the consumer app** (RFC-001's three-surfaces
model). The audience is citizens pressuring institutions + procurement officers,
and the levers are FOI, council votes, and tenders — fundamentally different from
an individual's grocery swap. It runs on the **shared scoring engine** (the
entity SovereigntyScore, RFC-011), not a new one.

## The opening (why now)

Validated by research 2026-06-27 and a working data spike:

- A **live regulator + news cadence**: Datatilsynet is actively inspecting
  municipalities on school-cloud privacy (50-kommune letter-control, findings
  2025-05-15; 2026 on-site inspections). Digitaliseringsminister Karianne Tung
  publicly challenged Microsoft's ~75% public-sector share (Apr 2026).
- A **vivid benchmark**: Schleswig-Holstein moved ~30,000 workstations off
  Windows/M365 (≈€15M/yr saved); Norway's own Larvik kommune freed its
  case-management from both Microsoft and Google (≈NOK 10M/yr saved).
- **The data spike already proves feasibility.** A zero-cost DNS scan
  ([kommune-sovereignty-scan](../scanner/)) classified
  all 358 municipalities by email platform: **90.2% on Microsoft 365, 91.3% on a
  US hyperscaler** — independently corroborating the minister's number, at the
  email layer, for free.

## Decision

### 1. A separate web surface, on the shared engine

A public web surface (`/sovereignty` or a distinct subdomain — naming is an open
fork in the strategy memo) presenting a **per-public-body sovereignty scorecard**.
It consumes the entity SovereigntyScore (RFC-011) applied to public entities. The
consumer mobile app does **not** absorb this; the only crossing between surfaces
is the employer-nudge bridge (separate issue), which *links out* here.

### 2. MVP axis: email-platform sovereignty, from public DNS

V1 scores each municipality on a single, defensible, fully-automatable axis:
which jurisdiction its **email platform** answers to, derived from public DNS
(MX + SPF + autodiscover fingerprint — see the prototype's method). This is the
cheapest credible axis and already discriminates 90/10. Output per body:
platform, jurisdiction, evidence (the actual records), and the
recommended European alternative.

> **Principle 1 (factual over moralizing) is binding here.** Every score links to
> its evidence (the DNS records, the contract). Copy states the fact — *"Email
> runs on Microsoft 365 (United States; CLOUD Act jurisdiction)"* — never
> *"bad"*. The four-facts discipline from the consumer side applies: platform /
> operator jurisdiction / data residency / contract value, each cited.

### 3. The US→EU switch map, with sovereignty-washing flags

Each finding pairs with a concrete, adoptable alternative (M365 → openDesk /
LibreOffice + Nextcloud + Open-Xchange; Azure/AWS → OVHcloud / Hetzner / IONOS /
STACKIT). The data model **must encode the washing traps**: EU-located ≠
EU-owned (AWS/Azure "sovereign cloud" remains CLOUD-Act-exposed); flag
non-EU-jurisdiction (UK/CH) and Russian-origin heritage (OnlyOffice). Encoding
this expert distinction is the credibility moat — a naive "is it European?" check
would miss it.

### 4. The activism funnel (the highest-leverage feature)

Each scorecard entry emits concrete citizen tooling, Norway-specific at MVP:

- A **pre-filled offentleglova (innsyn/FOI) request** for the body's M365
  contract + DPIA (statutory response in 5 working days, else appealable).
- A **minsak.no innbyggerforslag template** (300 signatures *forces* the
  kommunestyre to take a position, normally within 6 months).
- The procurement ask, grounded: anskaffelsesloven §7-9 already mandates ≥30%
  climate weighting — the precedent for a "weight sovereignty" criterion; the EU
  SEAL framework is the scored-sovereignty template.

**Messaging guardrail (the Munich LiMux lesson):** push for change locked into
**procurement rules / strategy**, not a flippable one-off IT decision — Munich
reverted LiMux by a council vote. The CTA copy frames the durable ask.

### 5. Data architecture

- Public-entity scoring is **public reference data**, not user data — it sits in
  the open entity graph (CC-BY core; RFC-005 / RFC-001 P6), not the gated
  natural-person layer.
- Scores are **versioned point-in-time snapshots**; every score row carries
  `sourceUrl`/`sourceDate` (the DNS snapshot, the contract). Re-scan on a
  schedule; never show a number without its evidence link.
- No per-citizen records from the activism funnel beyond what the digital
  pillar's aggregate-only architecture (RFC-007) already permits.

## MVP scope (what ships)

1. DNS email-sovereignty pipeline (productionise the prototype: scheduled,
   versioned, evidence-linked; widen domain resolution to fix the few
   website≠mail cases).
2. A Norway-first landing page + per-municipality scorecard (email axis), the
   benchmark narrative, and the US→EU switch map with washing flags.
3. The two CTA templates (FOI + innbyggerforslag).

## Out of scope (roadmap)

- Additional axes (web hosting/CDN signatures, procurement contracts via
  TED/Doffin, productivity-suite detection) — additive once the surface exists.
- Other public-body types (counties, state agencies, hospitals) and other EEA
  markets — the model is designed to generalize (RFC-016), built Norway-first.
- The business/employer (private-company) scorecard — same engine, its own RFC.

## Consequences

- BetterWorld gains a high-leverage, news-rideable surface at near-zero data
  cost, distinct from the consumer app and reusing the entity engine.
- The factual-over-moralizing + evidence-linked discipline ports directly; the
  washing-flags requirement raises the data model's bar (a feature, not a cost).
- A new public surface widens the legal/anti-SLAPP surface area — naming public
  bodies in their official capacity on cited public facts is the safest version
  of that exposure, but it ties to RFC-013's posture and should be reviewed
  alongside it before public launch.
