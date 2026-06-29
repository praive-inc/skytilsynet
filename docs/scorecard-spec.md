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

### 6. Trust armor: residency ≠ jurisdiction + open method (issue #35)

The finding will face well-funded counter-messaging — Microsoft actively markets
"Microsoft 365 data residency in Norway" and an "EU sovereign cloud". If the
method doesn't pre-empt that, the tracker gets waved away. The credibility
posture is therefore part of the spec, not cosmetics:

- **Datalagring ≠ jurisdiksjon, stated precisely.** EU/Norway-resident data does
  **not** remove US jurisdiction: the **US CLOUD Act** reaches a US-headquartered
  provider regardless of where the bytes physically sit. The site states this on
  both the methodology view and the per-entity view (the latter gated to US
  verdicts, where the distinction bites). Keep the three signals **distinct, never
  conflated**: *bruker Microsoft* (runs M365) ≠ *ingen EU-datagrense* (no agreed
  EU storage boundary) ≠ *US-jurisdiksjon* (provider bound by US law — the
  heaviest, true even *with* an EU data boundary).
- **Per-record "Kontrollert den \<dato\>".** Every verdict is one click from its
  per-signal evidence, and each evidence record surfaces the `observed_at` it was
  checked on. Trust floor matching OWID / Bellingcat / Faktisk.no: no claim
  without a dated, citable observation.
- **"Hva dette IKKE beviser."** A plain-language box bounding the email axis: a
  mail gateway ≠ all workloads in the US; email is one axis; a DNS verdict proves
  the platform, not that data was exfiltrated; the US share is a **floor**.
- **Open classifier, loudly.** The classification rules are one readable file
  (`scanner/scan.py`), linked prominently — not a black box.
- **Named methodology author + independence line.** The method is owned by a
  named person (currently **Jøran Bjerksetmyr**), not an anonymous desk, and the
  "uavhengig prosjekt fra BetterWorld — ikke et offentlig organ" line sits beside
  it (reinforcing the load-bearing disclaimer, rule 2).
- **Public corrections log (endringslogg).** A CC-BY data file
  (`data/corrections.json`) the build bakes inline; each entry carries a date so
  the history is open and verifiable. A corrections log is a trust *builder*, not
  an admission — the honest empty state ("Ingen rettelser ennå") never fabricates
  one. Norway's faktasjekk culture rewards radical transparency and punishes
  hidden-number advocacy.

### 7. The news-driving layer: Norway cartogram + league table (issue #36)

The grid of 486 isn't the story — the **ranking** is. Two artifacts make the
finding shareable, baked static into `web/index.html` by `web/build.py`:

- **An equal-area hex cartogram of Norway's 15 fylker.** Identical hexes (a
  cartogram, not an area choropleth) so sparse-but-huge Finnmark cannot visually
  erase Oslo. The geometry is committed in `build.py` (`_FYLKE_HEXES`) and
  rendered to inline SVG — **no external map tiles** (RFC-001 P5). Each hex is
  coloured by that county government's own email platform and is a hash permalink
  to its entity card (the fylkeskommune; Oslo → the kommune). It shows the
  fylkeskommune's *own* email, not an aggregate of the county's kommuner — stated
  in the caption (honesty about what the axis covers).
- **A league table** with a pinned **hall of fame** (most sovereign) and the
  **most dependent**, plus a client-side **sortable** full table over every
  scanned body. Each row is a permalink to its evidence card and is date-stamped.
  Two honesty rules bind the ranking: (a) it is **per organ on the same
  measurement** — never on size, so a 2 000-person kommune is not ranked against
  Oslo on absolutes; (b) the hall of fame holds **only genuinely non-US bodies**,
  never padded to ten with US ones — when only a handful qualify, that *is* the
  finding (the caption states the brutal `Bare X av Y`). The order is a
  transparent `dep_score` from the cited platform class plus two honest
  tiebreakers (confirmed Azure federation, an unmasked backend) — **not** a
  reimplemented SovereigntyScore (rule 3).

### 8. Per-entity suverenitetsscore + national ranking (issue #38)

The LCV-scorecard lever — *"Køyrer DIN kommune innbyggjarane sine data på
US-jurisdiksjon sky?"* — needs a single number per body, not just a platform
tile. `web/build.py` derives a **suverenitetsscore (0–100, higher = more
sovereign)** as Skytilsynet's **presentation** of the axes it already cites —
**not** a fork of BetterWorld's multi-axis SovereigntyScore engine (rule 3, the
RFC-011 seam). When that engine matures, the score becomes a thin renderer of
its output.

- **The formula is on the page (open method).** A fixed weighting of the three
  cited axes: **email-platform jurisdiction 60 %**, **web-axis (infrastructure)
  25 %**, **governance of the operator's jurisdiction 15 %**. Each axis is a
  0.0–1.0 sub-score; the entity card shows every term as *weight × delscore →
  poeng* behind a "Vis formelen" disclosure, and the points sum to the score.
  - *Email sub:* EU/Norwegian drift `1.0`; undetermined `0.5` (we have not proven
    US — the honest middle); a US operator `0.1`, dropping the last notch to `0.0`
    for an **Azure-federated** tenant (deeper lock-in — the `+federated` nuance).
  - *Web sub:* `0.6 ×` host-jurisdiction (EEA/EU `1.0`, CLOUD Act `0.0`, else
    `0.5`) `+ 0.4 ×` (1 − US-resource-fraction). From the joined web-axis record.
  - *Governance sub:* the cited Freedom House 0–100 score of the jurisdiction,
    normalised. So a US body in a free democracy still scores **low** — the 60 %
    email term is near zero and dominates.
  - **An axis with no measurement is dropped and the weights renormalise**, never
    silently counted as zero (which would invent a finding we did not measure).
- **A national ranking.** Every entity carries `nationalRank` / `nationalTotal`
  across *all* categories by score, descending (rank 1 = most sovereign), using
  standard competition ranking (equal scores share a rank). The score also rides
  on each §7 league row. This ranks on the same cited measurement, never on size.
- **A per-entity trend over time.** Each card shows the body's email platform at
  every snapshot in its category series (`entity_trend`), so one good scan can't
  bury a record. It is **methodology-version aware** (issue #24): a platform
  change across a version bump is marked a *new baseline*, not a real migration.
  Web/governance are measured today only, so the line tracks the email axis (the
  heaviest term) — stated in the caption.
- **A concrete "kva kan endrast" ask.** For a US/undetermined body the card names
  the durable lever — *move email to an EU-jurisdiction provider (openDesk /
  Nextcloud)* — and **quantifies it**: the score it would unlock with the email
  axis lifted to full sovereignty. Framed as a procurement/strategy change, never
  a personal attack (rule 6). An already-sovereign body is told to hold the line.

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
