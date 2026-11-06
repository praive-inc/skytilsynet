# Vision

> **Starter draft — this is the operator's document.** It is the human-owned
> *why* layer: the agent never rewrites it. Edit freely. It was seeded from the
> README, [`../scorecard-spec.md`](../scorecard-spec.md), the reference doc and the
> closed issues, so the console's "About & vision" panel has something real to
> read. The `## Outcomes` list below is what the progress panel tracks.

## North-star

**Public bodies off US hyperscalers.** Skytilsynet exists to bring down the share
of Norwegian municipal e-mail that runs on a US hyperscaler. The baseline is the
first zero-cost DNS scan of all 358 kommuner (2026-06-27): **91.3%** on Microsoft
or Google, 90.2% on Microsoft 365 alone.

Since the deep-unmask scanner (#6), which resolves gateway- and co-op-masked tenants,
the measured share is higher, not lower: **99.7%** US hyperscaler and 98.6% Microsoft
365 in the 2026-11-02 weekly scan. Because masked domains may still hide more
Microsoft tenants, that figure is a floor. Success means this number moves down.

The way there: **make the invisible visible, never shame.** Every figure links to
its evidence. Citizens, employees and elected officials get a fact they can act on,
plus a European alternative and an offentleglova (FOI) request they can send.

## What makes it different / what we are not

- **Evidence, not opinion.** Every claim about a body carries its source and date
  (DNS records, portal fingerprints, FOI answers). We state facts like "e-mail runs on
  Microsoft 365 (United States; CLOUD Act jurisdiction)", never "bad".
- **Residency is not jurisdiction.** "Data stored in Norway" does not remove US legal
  reach, and the site says so.
- **Not a government body.** It is an independent, non-profit project. The `-tilsynet`
  name makes the disclaimer load-bearing; it stays prominent.
- **Not a scoring engine.** This repo owns the scanner and the presentation. The
  multi-axis sovereignty score stays in BetterWorld.
- **Open by default.** Code is MIT and data is CC BY 4.0. FOI intake minimises
  personal data and purges it on a fixed retention schedule (#114).

## Outcomes

> Tracked by the progress panel. `[shipped]` = delivered, `[in progress]` =
> building now, `[planned]` = next. A trailing `(#N)` credits the work.

- **[shipped]** Every kommune's e-mail platform is scanned weekly from public DNS, with dated snapshots and history. (#1)
- **[shipped]** Masked tenants behind gateways and municipal co-ops are unmasked, so few kommuner stay unresolved. (#6)
- **[shipped]** A public tracker at skytilsynet.no: per-kommune scorecard, "Finn din kommune" search, map and league table. (#2, #34, #36, #46)
- **[shipped]** Every record shows its work: an evidence page per body, checked-on dates and an open methodology page. (#8, #30, #35)
- **[shipped]** Coverage beyond kommuner: fylkeskommuner, helseforetak, statlige organ and UH-sektor. (#12, #26)
- **[shipped]** Further axes beyond e-mail: web infrastructure and case handling/archive (sakarkiv). (#7, #13, #61, #65)
- **[shipped]** Each body gets a sovereignty score and a national ranking; the trend ignores methodology changes. (#24, #38)
- **[shipped]** Citizens can act: a per-body offentleglova FOI kit, plus a reviewed and GDPR-hardened FOI answer intake. (#3, #51, #54, #55, #114)
- **[shipped]** Press can reuse the data: /for-presse with CSV and embeds, an English page, and share cards. (#25, #32, #37, #44)
- **[shipped]** The Suverenitetsmålet goal section: progress toward 25% sovereign by 2030 and the first fully sovereign body by 17 May 2027. (#14)
- **[in progress]** The weekly scan refreshes every generated file, and how it runs (Actions or devbox) is settled. (#127, #129)
- **[in progress]** Decide whether the pre-launch legal review in the spec is satisfied now that the site is live. (#71)
- **[planned]** A procurement axis (contracts via TED/Doffin) and productivity-suite detection.
- **[planned]** Other EEA countries, using the Norway-first model.
