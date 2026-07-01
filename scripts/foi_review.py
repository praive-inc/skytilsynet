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
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import foi_intake  # noqa: E402

# Mirrors data/saksbehandling.csv exactly so an accepted row pastes straight in.
SAKS_HEADER = ["domain", "category", "vendor", "vendor_method", "vendor_source",
               "vendor_date", "hosting", "hosting_jurisdiction", "hosting_method",
               "hosting_source", "hosting_date", "note"]

_COLS = ["id", "created_at", "domain", "entity_name", "vendor", "hosting",
         "jurisdiction", "source", "note", "status"]


def _fetch(conn, sub_id):
    row = conn.execute(
        "SELECT %s FROM submissions WHERE id = ?" % ", ".join(_COLS), (sub_id,)
    ).fetchone()
    return dict(zip(_COLS, row)) if row else None


def saksbehandling_row(sub, entities):
    """An accepted submission → a data/saksbehandling.csv row (list). vendor and
    hosting are both cited as innsyn-foi answers (that's what earns 'bekreftet').
    The date is the submission date; the operator can adjust before pasting."""
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
        date if has_hosting else "",
        sub.get("note") or "",
    ]


def _row_csv(row):
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    return buf.getvalue().rstrip("\r\n")


def cmd_list(conn, args):
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
    sub = _fetch(conn, args.id)
    if not sub:
        sys.exit("fann ikkje innsending #%d" % args.id)
    for k in _COLS:
        print("%-12s %s" % (k, sub[k]))


def _set_status(conn, sub_id, status):
    sub = _fetch(conn, sub_id)
    if not sub:
        sys.exit("fann ikkje innsending #%d" % sub_id)
    conn.execute("UPDATE submissions SET status = ? WHERE id = ?", (status, sub_id))
    conn.commit()
    return sub


def cmd_accept(conn, args):
    sub = _set_status(conn, args.id, "accepted")
    entities = foi_intake.known_entities()
    row = saksbehandling_row(sub, entities)
    print("# lim denne raden inn i data/saksbehandling.csv (menneske-kurert):",
          file=sys.stderr)
    print(_row_csv(row))


def cmd_reject(conn, args):
    _set_status(conn, args.id, "rejected")
    print("avvist #%d" % args.id, file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default=foi_intake.DB_PATH, help="sti til SQLite-basen")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list"); lp.add_argument("--all", action="store_true")
    for name in ("show", "accept", "reject"):
        sp = sub.add_parser(name); sp.add_argument("id", type=int)
    args = p.parse_args(argv)

    conn = foi_intake.init_db(args.db)
    {"list": cmd_list, "show": cmd_show,
     "accept": cmd_accept, "reject": cmd_reject}[args.cmd](conn, args)


if __name__ == "__main__":
    main()
