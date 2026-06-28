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

When those three leave a domain **masked** (a mail-security gateway — Cisco
IronPort `*.iphmx.com`, Trend Micro, Comendo — or a regional IKT co-op fronting an
unknown backend) or **unresolved**, three further no-auth signals unmask it. They
run *only* for those few domains, never all 358:

4. **DKIM selectors (DNS)** — `selector1/2._domainkey.<domain>` CNAME into
   `*.onmicrosoft.com` is airtight Microsoft 365; a `google._domainkey.<domain>`
   TXT is Google Workspace.
5. **SPF IP-range match** — an `ip4:` in the SPF that falls inside a Microsoft EOP
   range (`40.92.0.0/15`, `40.107.0.0/16`, `52.100–103.x`, `104.47.0.0/17`, …).
   Catches **flattened SPF** that inlines raw MS IPs instead of the
   `spf.protection.outlook.com` include (the reason Alvdal was missed).
6. **Azure AD realm** — one HTTPS GET to `login.microsoftonline.com/getuserrealm.srf`.
   `<NameSpaceType>` `Managed` = a cloud M365 tenant (airtight); `Federated` =
   the domain is federated into Azure AD, so an M365 tenant exists — labelled
   Microsoft with a **`federated`** flag (tenant proven, email inferred
   high-confidence; we don't overclaim it as hard M365); `Unknown` = no tenant.

Precedence: airtight Microsoft (DKIM→onmicrosoft **or** SPF-MS-IP **or**
realm=Managed) → `US_MICROSOFT`; realm=Federated only → `US_MICROSOFT` +
`federated`; `google._domainkey` → `US_GOOGLE`; a domain stays `OTHER` only when
**no** signal fires. Each resolved record carries the signal that resolved it
(`fingerprint` + the value under `evidence`: `dkim` / `spf_ms_ip` / `realm`).

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

- `federated` — the domain is federated into Azure AD (getuserrealm=Federated): an
  M365 tenant is proven, but email is inferred high-confidence rather than directly
  observed, so it is a **visible qualifier**, not silently merged into hard M365
  (`summary.federated` counts them).
- `backend_unmasked` — a mail-security gateway hides the real backend and neither
  SPF nor the deep probe (DKIM / realm) revealed it. **These mean the Microsoft/US
  share is a floor, not a ceiling** (`summary.floor_note` +
  `summary.backend_unmasked` state this explicitly).
- `non_eu_jurisdiction` — EU-*located* ≠ EU-*owned*: a provider whose owner sits
  outside EU law (e.g. Proton, Switzerland).
- `russian_origin` — Russian-heritage suite (OnlyOffice / MyOffice).
- `mail_domain_differs_from_website` — the resolved mail domain is not the website
  domain (audit trail for the fallback above).

## Findings (358 municipalities, scan run 2026-06-28)

| Platform | Count | Share |
|---|---:|---:|
| **Microsoft 365** | **353** | **98.6%** |
| Google Workspace | 4 | 1.1% |
| EU-sovereign (domeneshop.no) | 1 | 0.3% |
| Other (regional NO co-ops / unmasked gateway backend) | 0 | 0.0% |
| Unresolved | 0 | 0.0% |

- **98.6% on Microsoft 365; 99.7% on a US hyperscaler.** The deep-unmask probe
  resolved all 25 previously-`OTHER` rows — gateway-fronted (Cisco IronPort, Trend
  Micro) and regional IKT co-ops (Hedmark IKT, Sunnmøre IKT, Fjellregionen) — to
  Microsoft 365: 8 via airtight signals (DKIM→onmicrosoft / SPF-MS-IP), 17 via an
  Azure AD federation (`federated`-flagged: tenant proven, email inferred). This
  sharpens the corroboration of digitaliseringsminister Karianne Tung's "~75% of
  public-sector software is Microsoft" — the email layer is far more concentrated.
- **The non-US tail is now a single municipality** on `domeneshop.no` (Norway) plus
  four on Google Workspace. The regional co-ops that *looked* sovereign were
  Microsoft tenants behind a co-op gateway all along.

## Output

| File | Contents |
|---|---|
| `snapshots/<date>.json` | versioned point-in-time snapshot (`date`, `summary`, `kommuner`) |
| `history.json` | one aggregate row per run — the trend `transition.py` reads |
| `kommune_sovereignty.json` | latest records (scanner-local convenience copy) |
| `../data/kommune-email-sovereignty.latest.json` | the **published CC-BY dataset** (meta + summary + kommuner) |

## Second axis: website-infrastructure sovereignty (`web_scan.py`)

Email is one axis. `web_scan.py` adds a **distinct** one — where the kommune's
public **website** infrastructure answers to — kept separate from the email score,
never conflated. It derives jurisdiction from public, no-auth signals a browser
already fetches (no intrusion, no crawl: one homepage GET + one `security.txt` GET
per kommune, low concurrency, identifying User-Agent):

1. **HTTP headers** — `Server`, `X-Powered-By`, `Content-Security-Policy`.
2. **Embedded third-party resources** — every external host in the homepage's
   `<script>/<link>/<img>/<iframe>` (Google Analytics/Tag Manager/Fonts, US CDNs,
   map tiles, social trackers) classified to the jurisdiction its **owner**
   answers to. Same-site subdomains are excluded; unknown hosts stay
   `Undetermined` (we never guess a jurisdiction we can't cite).
3. **Hosting jurisdiction** — the homepage's first IPv4 → origin ASN + country via
   **Team Cymru DNS** (`<rev-ip>.origin.asn.cymru.com TXT`, no key).
4. **TLS certificate issuer** (`openssl s_client`) and `/.well-known/security.txt`.

Per kommune it records the hosting jurisdiction, the per-resource jurisdiction
list, the **fraction of embedded resources that are US-hosted**, an analytics y/n,
and washing flags (`us_hosted`, `analytics`, `us_cdn`, `third_party_trackers`,
`unreachable`) — each with its evidence + `sourceDate`. The **EU-located ≠
EU-owned** moat applies here too: an asset cached in AWS `eu-central-1` (Frankfurt)
is still US-jurisdiction because Amazon owns it.

```bash
python3 web_scan.py                       # snapshot dated today (UTC)
SCAN_DATE=2026-06-27 python3 web_scan.py  # pin the snapshot date
```

Needs `dig` and `openssl`. Writes `snapshots/web-<date>.json`, `web_history.json`,
`kommune_web_sovereignty.json`, and the published
`../data/kommune-web-sovereignty.latest.json`.

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

- Two axes so far: email (`scan.py`) and website infrastructure (`web_scan.py`,
  above). A full scorecard still adds procurement contracts (TED API + Doffin CSV)
  and org resolution (Brønnøysund).
- The web axis reads only the homepage `<head>`/markup, not JS-injected resources;
  a tracker added by client-side script after load is not seen. The US-resource
  fraction is therefore a floor, like the email Microsoft share.
- The `MS_EOP_RANGES` prefix list in `scan.py` is static; refresh it periodically
  from `https://endpoints.office.com` (service id `Exchange`) so flattened-SPF
  matching keeps up with Microsoft's IP allocations.
- `transition.py` keys the per-kommune diff by name; the three duplicate-name
  municipalities (two Vålers, two Herøys) are kept distinct in the dataset by
  their separate domains but collide in that one diff view — a known, minor limit.
