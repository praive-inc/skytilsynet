# Prototype: Norway kommune email-sovereignty scan

**What this is:** a zero-cost, no-auth proof that BetterWorld can score every
Norwegian municipality on digital sovereignty from public DNS alone. It is the
data spike behind the **Public-Sector Sovereignty Scorecard** proposal
([strategy memo](../making-the-world-better-2026-06.md) §5, RFC-017).

**Run:** `python3 scan.py` (needs `dig`; reads `kommuner_wikidata.json`, a
Wikidata SPARQL dump of `wdt:P31 wd:Q755707` municipalities + their `P856`
website). Output: `kommune_sovereignty.json` + a printed summary.

## Method

For each municipality's mail domain we classify the underlying email **platform**
from three public signals, strongest last:

1. **MX records** — `*.mail.protection.outlook.com` = Microsoft 365,
   `aspmx.l.google.com` = Google Workspace, etc.
2. **SPF (TXT)** — unmasks platforms behind a mail-security gateway: a kommune
   can have a vanity MX (Trend Micro, Cisco IronPort, Proofpoint, Comendo) while
   its SPF still declares `include:spf.protection.outlook.com` — i.e. it is
   really on Microsoft.
3. **autodiscover CNAME** — `autodiscover.<domain>` → `autodiscover.outlook.com`
   is the canonical Microsoft 365 tenancy fingerprint, independent of MX/SPF.
   Used to reclassify the gateway-fronted "OTHER" bucket.

## Findings (358 municipalities, scan run 2026-06-27)

| Platform | Count | Share |
|---|---:|---:|
| **Microsoft 365** | **323** | **90.2%** |
| Google Workspace | 4 | 1.1% |
| EU-sovereign (domeneshop.no) | 1 | 0.3% |
| Other (regional NO co-ops / unknown backend) | 29 | 8.1% |
| No MX on website domain | 1 | 0.3% |

- **90.2% on Microsoft 365; 91.3% on a US hyperscaler.** This independently
  corroborates digitaliseringsminister Karianne Tung's "~75% of public-sector
  software is Microsoft" (the email layer is even more concentrated).
- The **90.2% is a floor.** The remaining ~8% "other" are mostly mail-security
  gateways (Trend Micro EU, Cisco IronPort, Proofpoint) fronting a backend we
  did not definitively unmask — some are likely Microsoft too.
- **The only genuinely non-US infrastructure is regional/self-hosted:** the
  **Hedmark IKT** co-op (Hamar, Kongsvinger, Løten, Nord-Odal, Stange, Sør-Odal,
  Grue), a **Fjellregionen** cluster (Alvdal, Folldal, Rendalen, Tolga, Tynset),
  **Sunnmøre IKT** (Hareid, Herøy, Sande, Ulstein, Volda, Ørsta), plus a handful
  of Norwegian hosts (domeneshop, webhuset, bedsys) and a Sámi-language host
  (Kautokeino). ~30 municipalities total — the entire sovereign tail of the
  country fits on one screen.

## Caveats / what a production version adds

- Email is **one axis**. A full scorecard adds web hosting/CDN (Wappalyzer-style
  signatures), procurement contracts (TED API + Doffin CSV), and org resolution
  (Brønnøysund).
- The website domain ≠ mail domain for a few kommuner (the "no MX" cases) — fix
  by also probing the apex `.no` and known mail subdomains.
- Gateway backends should be unmasked further (login.microsoftonline.com realm
  check) to tighten the floor toward the true number.
- Data is a point-in-time DNS snapshot; production re-scans on a schedule and
  versions the result (every score links to evidence — RFC-001 P1).
