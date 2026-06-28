# Norway kommune email-sovereignty scan

A zero-cost, no-auth pipeline that scores every Norwegian municipality on email
**platform sovereignty** from public DNS alone — which jurisdiction its mail
answers to. It is the scanner half of the **Public-Sector Sovereignty Scorecard**
([scorecard-spec](../docs/scorecard-spec.md) §2/§5); the scoring engine stays in
BetterWorld (CLAUDE.md rule 3).

**Run:**

```bash
python3 scan.py                       # snapshot dated today (UTC)
SCAN_DATE=2026-06-27 python3 scan.py  # pin the snapshot date
python3 transition.py                 # which kommuner moved between the last two runs
python3 -m unittest                   # offline test suite (no network)
```

Needs `dig`. Reads `kommuner_wikidata.json` (Wikidata SPARQL dump of
`wdt:P31 wd:Q755707` municipalities + their `P856` website).

## Method

For each municipality we classify the email **platform** from three public
signals, strongest last:

1. **MX records** — `*.mail.protection.outlook.com` / `*.mx.microsoft` = Microsoft
   365, `aspmx.l.google.com` = Google Workspace, etc.
2. **SPF (TXT)** — unmasks platforms behind a mail-security gateway: a kommune can
   have a vanity MX (Trend Micro, Cisco IronPort, Proofpoint) while its SPF still
   declares `include:spf.protection.outlook.com` — i.e. it is really on Microsoft.
3. **autodiscover CNAME** — `autodiscover.<domain>` → `autodiscover.outlook.com`
   is the canonical Microsoft 365 tenancy fingerprint, independent of MX/SPF.

**Website ≠ mail domain.** A municipality's website domain sometimes carries only
a null-sending `v=spf1 -all` record (or nothing) because its mail lives elsewhere.
We resolve the real mail domain by probing candidate domains in order — the
website, its parent domains (never the shared `kommune.no` apex), then
`<slug>.kommune.no` — and keep the first that yields a real signal. The handful of
unguessable vanity domains (e.g. Aurskog-Høland → `ahk.no`) live in the curated
[`mail_domain_overrides.json`](mail_domain_overrides.json).

## Evidence & the washing flags (factual-over-moralizing)

Every record carries its **evidence** — the actual MX / SPF / autodiscover records
— plus `sourceDate`, the `jurisdiction` the platform answers to, and the
recommended European `alternative`. Never a classification without its source
(CLAUDE.md rule 1).

The data model encodes the **sovereignty-washing traps** (scorecard-spec §3) as
per-record `flags`:

- `backend_unmasked` — a mail-security gateway hides the real backend and SPF did
  not reveal it. **These mean the Microsoft/US share is a floor, not a ceiling**
  (`summary.floor_note` + `summary.backend_unmasked` state this explicitly).
- `non_eu_jurisdiction` — EU-*located* ≠ EU-*owned*: a provider whose owner sits
  outside EU law (e.g. Proton, Switzerland).
- `russian_origin` — Russian-heritage suite (OnlyOffice / MyOffice).
- `mail_domain_differs_from_website` — the resolved mail domain is not the website
  domain (audit trail for the fallback above).

## Findings (358 municipalities, scan run 2026-06-28)

| Platform | Count | Share |
|---|---:|---:|
| **Microsoft 365** | **328** | **91.6%** |
| Google Workspace | 4 | 1.1% |
| EU-sovereign (domeneshop.no) | 1 | 0.3% |
| Other (regional NO co-ops / unmasked gateway backend) | 25 | 7.0% |
| Unresolved | 0 | 0.0% |

- **91.6% on Microsoft 365; 92.7% on a US hyperscaler — and that is a floor.**
  10 of the 25 "other" rows are mail-security gateways (Trend Micro EU, Cisco
  IronPort, Proofpoint) fronting a backend we could not definitively unmask; some
  are likely Microsoft too. This independently corroborates digitaliseringsminister
  Karianne Tung's "~75% of public-sector software is Microsoft" — the email layer
  is even more concentrated.
- **The genuinely non-US tail is regional/self-hosted:** the **Hedmark IKT** co-op
  (Hamar, Kongsvinger, Løten, Nord-Odal, Stange, Sør-Odal, Grue), a
  **Fjellregionen** cluster (Alvdal, Folldal, Rendalen, Tolga, Tynset),
  **Sunnmøre IKT** (Hareid, Herøy, Sande, Ulstein, Volda, Ørsta), plus a handful
  of Norwegian hosts (domeneshop, webhuset, bedsys). ~25 municipalities — the
  entire sovereign tail fits on one screen.

## Output

| File | Contents |
|---|---|
| `snapshots/<date>.json` | versioned point-in-time snapshot (`date`, `summary`, `kommuner`) |
| `history.json` | one aggregate row per run — the trend `transition.py` reads |
| `kommune_sovereignty.json` | latest records (scanner-local convenience copy) |
| `../data/kommune-email-sovereignty.latest.json` | the **published CC-BY dataset** (meta + summary + kommuner) |

## Scheduling the re-scan

`scan.py` is cron-friendly: no args, no auth, idempotent (a same-date re-run
replaces that day's snapshot/history row). Scheduling is **wired by the operator**,
not via GitHub Actions (CLAUDE.md — the push token has no `workflow` scope). The
operator runs it off the devbox, e.g. a weekly crontab line:

```cron
0 6 * * 1  cd /path/to/skytilsynet/scanner && /usr/bin/python3 scan.py >> scan.log 2>&1
```

Then `python3 transition.py` surfaces which municipalities moved since the prior run.

## Caveats / roadmap

- Email is **one axis**. A full scorecard adds web hosting/CDN signatures,
  procurement contracts (TED API + Doffin CSV), and org resolution (Brønnøysund).
- Gateway backends could be unmasked further (login.microsoftonline.com realm
  check) to tighten the floor toward the true number.
- `transition.py` keys the per-kommune diff by name; the three duplicate-name
  municipalities (two Vålers, two Herøys) are kept distinct in the dataset by
  their separate domains but collide in that one diff view — a known, minor limit.
