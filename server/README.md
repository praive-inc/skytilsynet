# FOI intake service (`server/`)

A small EU-hosted (Helsinki devbox, RFC-001 P5) HTTP service that gives the
offentleglova FOI loop a real intake. It **replaces the dead `presse@skytilsynet.no`
mailto**: a citizen who got an innsyn answer submits it through a form → this
service → a SQLite database the **operator reviews BY HAND**.

Stdlib only (`http.server` + `sqlite3`) — the site gains a *small backend*, not a
framework (CLAUDE.md "simplest thing that solves it").

## The hard rule — read this first

**FOI submissions are UNTRUSTED public input. They are stored for human review
only and MUST NEVER enter an agent/LLM workflow.**

This is the project's standing no-untrusted-input / prompt-injection rule. The
service stores every submitted value as **inert, length-capped text via
parameterized queries**. It never renders it as HTML server-side, never executes
it, and nothing auto-promotes it into the published dataset. A human runs
`scripts/foi_review.py`, reads the answer, and pastes an accepted row into the
**human-curated** `data/saksbehandling.csv`. Do not wire submissions into any
summarizer, classifier, agent, or other LLM step — not for triage, not for
"just categorizing". If you ever add automation here, it processes the answers
as opaque data, never as instructions.

## Endpoints

- **`POST /api/foi`** — accept a submission (JSON *or* form-encoded). No auth.
  - Whitelisted, length-capped fields: `domain`, `vendor`, `hosting`,
    `jurisdiction`, `source`, `note`, plus a hidden **honeypot** (`company`).
  - `domain` **must match a known entity** in the dataset (loaded from
    `data/*email-sovereignty.latest.json`); otherwise the submission is rejected.
  - Abuse controls: `MAX_BODY` size cap, honeypot silently drops bots, and a
    per-identity throttle (a **salted hash** of ip+ua — never the raw address,
    rule 5). Caddy rate-limits in front too.
  - JSON request → `{"ok":true}`; form request → `303` redirect to `/bidra?sendt=1`
    (the no-JS success path).
- **`GET /api/foi/pending`** — the operator review queue as JSON (or `?format=csv`).
  Behind the operator secret: HTTP Basic (any user, password = the token) **or**
  `Authorization: Bearer <token>`.

## Environment

| Var | Purpose |
| --- | --- |
| `PORT` | Listen port (default `8781`). |
| `HOST` | Bind address (default `127.0.0.1`). Set `0.0.0.0` in a container on an internal-only docker network so the sibling Caddy container can reach it by name — see Deploy. |
| `FOI_OPERATOR_TOKEN` | **Required.** Guards `GET /api/foi/pending`. |
| `FOI_HASH_SALT` | Salt for the abuse-only ip/ua hash (defaults to the token). |

The DB lives at `server/data/foi_submissions.db` (git-ignored) when run standalone.
In the compose deploy that path is a **docker volume**, so the database survives
code syncs and restarts — the deploy script deletes any DB left in the synced app
dir precisely so the volume copy is the only one (see Deploy).

## Run

```bash
FOI_OPERATOR_TOKEN=changeme python3 server/foi_intake.py
```

## Operator review (human-in-the-loop is mandatory)

```bash
python3 scripts/foi_review.py list            # the 'new' queue
python3 scripts/foi_review.py show 3
python3 scripts/foi_review.py accept 3         # marks accepted + prints a CSV row
python3 scripts/foi_review.py accept 3 --source-type offentlig-journal
python3 scripts/foi_review.py reject 4
```

`accept` prints a ready `data/saksbehandling.csv` row (`vendor` + `hosting` +
`source` + date, `*_method=innsyn-foi`) to **stdout**. The operator pastes it into
`data/saksbehandling.csv` by hand, then `cd web && python3 build.py` and deploys.
`saksbehandling.csv` stays human-curated — nothing here writes to it.

**Trust & verification (issue #55).** `accept` **shows the submission's `source`
and refuses to accept without one** — no verdict reaches the dataset as
*bekreftet* without a source a skeptic can re-check. It stamps the emitted row's
`hosting_source_type` with the re-checkable-source **tier**:

- `--source-type offentlig-journal` — the source is a public postjournal /
  journalpost (einnsyn.no / norske-postlister.no), a databehandleravtale, or a
  Doffin award URL. **Highest**; rendered as a clickable evidence link.
- `--source-type innsyn-pa-fil` *(default)* — a real innsyn answer held on file,
  offered on request (not a public URL). **Medium**.

No `hosting_source_type` (or an `offentlig-journal` tier whose source is not a
resolvable URL) → the row stays *utledet* (vendor inference), never *bekreftet*.
At build time the axis also auto-cross-checks each vendor claim against the
body's innsyn-portal fingerprint (`*.onacos.no`→Acos, `*.elementscloud.no`→Sikri,
`*.360online.com`→Tietoevry, `ephinnsyn.*`→ePhorte): agreement earns a "bekreftet
av to uavhengige kilder" marker, a conflict is flagged and **not published**.
Every add/change is recorded in the public per-axis change log
(`data/saksbehandling-endringslogg.json`).

## Deploy — compose service behind Caddy (how it actually runs)

In prod the service runs as the **compose service `skytilsynet-foi`** in the
colocated `/opt/praive` runtime, alongside the sibling Caddy container. It is
**not** exposed on the host — it binds `HOST=0.0.0.0` on an **internal-only docker
network** and Caddy reaches it by service name (`skytilsynet-foi:8781`). The app
code and the domain whitelist are rsync'd into a mounted app dir; the SQLite DB
lives in a **docker volume** so it survives every code sync.

[`deploy/deploy-local.sh`](../../deploy/deploy-local.sh) is the deploy: it swaps
the static `web/` into `/opt/praive/skytilsynet-dist`, syncs `server/`, `shared/`,
`data/`, and `scripts/` into `/opt/praive/skytilsynet-app`, deletes any stale DB
from the synced app dir (the volume copy is authoritative), then
`docker compose restart skytilsynet-foi` and reloads Caddy.

`shared/` ships **alongside** `server/`: the backend adds the mounted app root
(`/app`) to `sys.path` and imports the top-level `shared` package
(`from shared.csv_safe import csv_safe`), so it must be present in the app dir or
the container fails at import time on restart (#110). An image built from this
repo must `COPY` `shared/` too, for the same reason.

The compose file and the Caddyfile live **on the prod box** (`/opt/praive`), not
in this repo (per CLAUDE.md this repo adds no `.github/workflows`). The service
block reflects the reality above — an internal-network container, no host port,
the DB on a named volume:

```yaml
# /opt/praive/docker-compose.yml (excerpt)
services:
  skytilsynet-foi:
    build: ./skytilsynet-app          # or an image built from server/
    command: python3 server/foi_intake.py
    environment:
      HOST: "0.0.0.0"                  # reachable by the sibling Caddy container
      PORT: "8781"
      FOI_OPERATOR_TOKEN: "REPLACE_WITH_A_LONG_RANDOM_SECRET"
      FOI_HASH_SALT: "REPLACE_WITH_ANOTHER_RANDOM_SALT"
    volumes:
      - ./skytilsynet-app:/app         # rsync'd app code + whitelist
      - foi-db:/app/server/data        # SQLite DB survives code syncs
    networks: [internal]
    restart: unless-stopped
    # No `ports:` — never exposed to the host; only Caddy on the internal net.
volumes:
  foi-db:
```

Caddy — reverse-proxy the API to the service by name, with a rate limit
(the static `web/` continues to be served as today):

```caddy
skytilsynet.no {
    # ... existing static file_server for web/ ...

    @foi path /api/foi*
    handle @foi {
        # Requires the caddy-ratelimit plugin; tune to taste.
        rate_limit {
            zone foi {
                key    {remote_host}
                events 20
                window 1m
            }
        }
        reverse_proxy skytilsynet-foi:8781
    }
}
```

The service reads `X-Forwarded-For` for the abuse hash, so the throttle sees the
real client, not the proxy. Because a client can prepend its own forged entries,
the service trusts only the **last** hop — the address Caddy appends (`TRUSTED_PROXY_HOPS
= 1`), never the client-supplied first entry (issue #84). If you ever chain more
than one trusted proxy, bump `TRUSTED_PROXY_HOPS` to match, or have Caddy strip
inbound `X-Forwarded-For` before proxying.

### Alternative — a host systemd unit

If you run the service directly on a host instead of in compose (no docker), a
systemd unit works too. Leave `HOST` at its `127.0.0.1` default and point Caddy's
`reverse_proxy` at `127.0.0.1:8781`.

`/etc/systemd/system/skytilsynet-foi.service`:

```ini
[Unit]
Description=Skytilsynet FOI intake service
After=network.target

[Service]
Type=simple
User=skytilsynet
WorkingDirectory=/opt/skytilsynet
Environment=PORT=8781
Environment=FOI_OPERATOR_TOKEN=REPLACE_WITH_A_LONG_RANDOM_SECRET
Environment=FOI_HASH_SALT=REPLACE_WITH_ANOTHER_RANDOM_SALT
ExecStart=/usr/bin/python3 server/foi_intake.py
Restart=on-failure
# Hardening: the service only needs to write its SQLite DB.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/skytilsynet/server/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now skytilsynet-foi.service
```

Here the DB lives at `/opt/skytilsynet/server/data/foi_submissions.db` (no docker
volume), and `deploy/deploy-local.sh` does not apply — it targets the `/opt/praive`
compose box.
