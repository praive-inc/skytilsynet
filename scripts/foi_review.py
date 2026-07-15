#!/usr/bin/env python3
"""Operator review CLI for crowd-submitted FOI answers (issue #54).

Human-in-the-loop is MANDATORY — nothing in this repo auto-promotes a submission
into the published dataset. The intake service (server/foi_intake.py) only stores
untrusted answers; the operator reads them here and, on `accept`, gets a ready
data/saksbehandling.csv row to paste into that human-curated file by hand, then
rebuilds and deploys. Submissions are inert text and MUST NOT be fed to any
agent/LLM.

Usage:
    python3 scripts/foi_review.py list [--all]     # queue (new by default)
    python3 scripts/foi_review.py show <id>
    python3 scripts/foi_review.py accept <id>      # mark accepted + emit CSV row
    python3 scripts/foi_review.py reject <id>
"""

import argparse
import csv
import getpass
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import foi_crypto  # noqa: E402
from server import foi_intake  # noqa: E402

# Mirrors data/saksbehandling.csv exactly so an accepted row pastes straight in.
SAKS_HEADER = ["domain", "category", "vendor", "vendor_method", "vendor_source",
               "vendor_date", "hosting", "hosting_jurisdiction", "hosting_method",
               "hosting_source", "hosting_source_type", "hosting_date", "note"]

# The re-checkable source tiers (issue #55). Highest first; the accept CLI stamps
# the emitted row with one so nothing reaches the published dataset as "bekreftet"
# without a source a skeptic can re-check. innsyn-pa-fil is the conservative
# default (a crowd innsyn answer held on file); the operator passes
# --source-type offentlig-journal when the source is a public postjournal /
# databehandleravtale / Doffin URL.
HOSTING_SOURCE_TYPES = ["offentlig-journal", "innsyn-pa-fil"]
DEFAULT_SOURCE_TYPE = "innsyn-pa-fil"

_COLS = ["id", "created_at", "domain", "entity_name", "vendor", "hosting",
         "jurisdiction", "source", "note", "status"]


def _fetch(conn, sub_id, cipher=None):
    row = conn.execute(
        "SELECT %s FROM submissions WHERE id = ?" % ", ".join(_COLS), (sub_id,)
    ).fetchone()
    if not row:
        return None
    sub = dict(zip(_COLS, row))
    if cipher is not None:                       # decrypt at-rest source/note (#114)
        sub["source"] = cipher.decrypt(sub["source"])
        sub["note"] = cipher.decrypt(sub["note"])
    return sub


def _actor():
    """Who is running the CLI, for the audit log. FOI_OPERATOR overrides the OS
    user (handy when several people share one shell account)."""
    return os.environ.get("FOI_OPERATOR") or getpass.getuser()


def _audit(conn, action, sub_id=None):
    """Append one operator-access entry: who did what, to which row, and when
    (issue #114). Every read (list/show) and decision (accept/reject/purge)
    records here."""
    conn.execute(
        "INSERT INTO operator_access_log (at, actor, action, sub_id) VALUES (?, ?, ?, ?)",
        (foi_intake.now_iso(), _actor(), action, sub_id))
    conn.commit()


def saksbehandling_row(sub, entities, source_type=DEFAULT_SOURCE_TYPE):
    """An accepted submission → a data/saksbehandling.csv row (list). vendor and
    hosting are both cited as innsyn-foi answers (that's what earns 'bekreftet').
    The emitted hosting row carries the re-checkable-source TIER (issue #55):
    hosting_source_type ∈ {offentlig-journal, innsyn-pa-fil}. The date is the
    submission date; the operator can adjust before pasting."""
    date = (sub.get("created_at") or "")[:10]
    category = entities.get(sub["domain"], {}).get("category", "")
    has_vendor = bool(sub.get("vendor"))
    has_hosting = bool(sub.get("hosting") or sub.get("jurisdiction"))
    return [
        sub["domain"],
        category,
        sub.get("vendor") or "",
        "innsyn-foi" if has_vendor else "",
        sub.get("source") or "",
        date if has_vendor else "",
        sub.get("hosting") or "",
        sub.get("jurisdiction") or "",
        "innsyn-foi" if has_hosting else "",
        sub.get("source") or "" if has_hosting else "",
        source_type if has_hosting else "",
        date if has_hosting else "",
        sub.get("note") or "",
    ]


def _row_csv(row):
    # Neutralize spreadsheet formula injection (issue #80): the untrusted answer
    # fields end up in this pasted row and the operator opens the result.
    buf = io.StringIO()
    csv.writer(buf).writerow([foi_intake.csv_safe(c) for c in row])
    return buf.getvalue().rstrip("\r\n")


def cmd_list(conn, args):
    _audit(conn, "list")
    where = "" if args.all else "WHERE status = 'new'"
    rows = conn.execute(
        "SELECT %s FROM submissions %s ORDER BY id" % (", ".join(_COLS), where)
    ).fetchall()
    if not rows:
        print("(ingen innsendingar%s)" % ("" if args.all else " å gå gjennom"))
        return
    for r in rows:
        d = dict(zip(_COLS, r))
        print("#%d  [%s]  %s (%s)  vendor=%s  hosting=%s/%s" % (
            d["id"], d["status"], d["entity_name"], d["domain"],
            d["vendor"] or "-", d["hosting"] or "-", d["jurisdiction"] or "-"))


def cmd_show(conn, args):
    sub = _fetch(conn, args.id, getattr(args, "cipher", None))
    if not sub:
        sys.exit("fann ikkje innsending #%d" % args.id)
    _audit(conn, "show", args.id)
    for k in _COLS:
        print("%-12s %s" % (k, sub[k]))


def _set_status(conn, sub_id, status, cipher=None):
    sub = _fetch(conn, sub_id, cipher)
    if not sub:
        sys.exit("fann ikkje innsending #%d" % sub_id)
    # decided_at anchors the retention purge (#114): the row is deleted a fixed
    # window after the operator decides it.
    conn.execute("UPDATE submissions SET status = ?, decided_at = ? WHERE id = ?",
                 (status, foi_intake.now_iso(), sub_id))
    conn.commit()
    return sub


def cmd_accept(conn, args):
    # Issue #55: no verdict reaches the published dataset without a re-checkable
    # source — the operator SEES the source and must have one before accept.
    sub = _fetch(conn, args.id, getattr(args, "cipher", None))
    if not sub:
        sys.exit("fann ikkje innsending #%d" % args.id)
    source = (sub.get("source") or "").strip()
    source_type = getattr(args, "source_type", None) or DEFAULT_SOURCE_TYPE
    print("# kjelde (må etterprøvast før aksept): %s" % (source or "(inga kjelde!)"),
          file=sys.stderr)
    print("# kildetype (tier): %s" % source_type, file=sys.stderr)
    if not source:
        sys.exit("nekta: innsending #%d har inga kjelde — kan ikkje bli «bekreftet» "
                 "(issue #55). Avvis, eller be om ei etterprøvbar kjelde." % args.id)
    _set_status(conn, args.id, "accepted", getattr(args, "cipher", None))
    _audit(conn, "accept", args.id)
    entities = foi_intake.known_entities()
    row = saksbehandling_row(sub, entities, source_type)
    print("# lim denne raden inn i data/saksbehandling.csv (menneske-kurert):",
          file=sys.stderr)
    print(_row_csv(row))


def cmd_reject(conn, args):
    _set_status(conn, args.id, "rejected", getattr(args, "cipher", None))
    _audit(conn, "reject", args.id)
    print("avvist #%d" % args.id, file=sys.stderr)


def cmd_purge(conn, args):
    n = foi_intake.purge_expired(conn)
    _audit(conn, "purge")
    print("sletta %d utgåtte innsendingar (retensjon: %d/%d dagar)" % (
        n, foi_intake.RETENTION_DECIDED_DAYS, foi_intake.RETENTION_NEW_DAYS),
        file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default=foi_intake.DB_PATH, help="sti til SQLite-basen")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list"); lp.add_argument("--all", action="store_true")
    sub.add_parser("purge", help="delete submissions past their retention window")
    for name in ("show", "accept", "reject"):
        sp = sub.add_parser(name); sp.add_argument("id", type=int)
        if name == "accept":
            sp.add_argument("--source-type", dest="source_type",
                            choices=HOSTING_SOURCE_TYPES, default=DEFAULT_SOURCE_TYPE,
                            help="re-checkable-source tier for the emitted row "
                                 "(default: %s)" % DEFAULT_SOURCE_TYPE)
    args = p.parse_args(argv)
    # Decrypt at-rest source/note when FOI_ENCRYPTION_KEY is configured (#114).
    args.cipher = foi_crypto.cipher_from_env()

    conn = foi_intake.init_db(args.db)
    {"list": cmd_list, "show": cmd_show, "accept": cmd_accept,
     "reject": cmd_reject, "purge": cmd_purge}[args.cmd](conn, args)


if __name__ == "__main__":
    main()
