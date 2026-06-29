#!/usr/bin/env python3
"""
Show the transition over time — the point of a live tracker.

  python3 transition.py            # diff the two most recent snapshots
  python3 transition.py A B        # diff snapshots/A.json vs snapshots/B.json

Prints the aggregate trend (from history.json) and the per-kommune platform
changes between two snapshots — i.e. exactly which municipalities moved (and in
which direction) since last time.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "snapshots")
HISTORY  = os.path.join(HERE, "history.json")
DATE_SNAP = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")  # kommune email series only

def load_snap(date):
    return json.load(open(os.path.join(SNAP_DIR, f"{date}.json")))

def main():
    hist = json.load(open(HISTORY)) if os.path.exists(HISTORY) else []
    if hist:
        print("=== Aggregate trend (history.json) ===")
        print(f"  {'date':12} {'MS%':>6} {'US%':>6} {'EU+other':>9}")
        for h in hist:
            print(f"  {h['date']:12} {h['microsoft_pct']:>6} {h['us_pct']:>6} "
                  f"{h['eu_sovereign']+h['other']:>9}")
        print()

    snaps = sorted(f[:-5] for f in os.listdir(SNAP_DIR) if DATE_SNAP.match(f)) \
            if os.path.isdir(SNAP_DIR) else []
    if len(sys.argv) == 3:
        a, b = sys.argv[1], sys.argv[2]
    elif len(snaps) >= 2:
        a, b = snaps[-2], snaps[-1]
    else:
        print("Only one snapshot so far — re-run scan.py later to see the transition.")
        return

    snap_a, snap_b = load_snap(a), load_snap(b)
    va, vb = snap_a.get("methodology_version", 1), snap_b.get("methodology_version", 1)
    if va != vb:
        # Cross-methodology diff: classification logic changed between these runs,
        # so per-kommune "moves" would conflate improved mapping with real switches
        # (issue #24). The web trend shows "ny baseline" here; we say it plainly.
        print(f"=== {a} (metodikk v{va}) → {b} (metodikk v{vb}) ===")
        print("  Metodikk endret mellom målingene — ny baseline, ingen bevegelsestall.")
        print("  (Forbedret kartlegging, ikke faktiske bytter. Sammenlign kun samme versjon.)")
        return
    old = {k["kommune"]: k["platform"] for k in snap_a["kommuner"]}
    new = {k["kommune"]: k["platform"] for k in snap_b["kommuner"]}
    changes = [(name, old[name], new[name]) for name in new
               if name in old and old[name] != new[name]]

    print(f"=== Changes {a} → {b} ===")
    if not changes:
        print("  No platform changes.")
        return
    LEFT_US = {"US_MICROSOFT", "US_GOOGLE", "US_MIXED"}
    for name, o, n in sorted(changes):
        arrow = "🟢 left US cloud" if (o in LEFT_US and n not in LEFT_US) \
                else "🔴 moved to US cloud" if (o not in LEFT_US and n in LEFT_US) \
                else "↔︎ changed"
        print(f"  {name:24} {o:14} → {n:14} {arrow}")

if __name__ == "__main__":
    main()
