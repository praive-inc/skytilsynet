#!/usr/bin/env python3
"""
Governance rating per jurisdiction (Skytilsynet / BetterWorld) — issue #9.

Skytilsynet tracks not just *foreign* dependence but how the governing country is
run: who governs the provider's operating jurisdiction. This module maps a
jurisdiction string (as produced by scan.py / web_scan.py, e.g.
``"United States (CLOUD Act)"``) to the governance rating of that country, drawn
from an established, citable democracy index — Freedom House's *Freedom in the
World*. Each rating carries its index, 0-100 score, status, the regime tier
derived from that status, source URL and edition year: factual-over-moralizing,
never a label without its source (CLAUDE.md rule 1).

WHY a static table HERE and not the scoring brain: the democracy/governance axis
is BetterWorld's engine territory — the V-Dem-led DemocracyScore (BetterWorld
RFC-002). Per CLAUDE.md rule 3 ("the scoring brain stays BetterWorld's") this repo
does NOT fork that logic; it starts with this lightweight ``country -> rating``
lookup and is the FIRST concrete consumer of that seam. ``governance_for`` is the
seam: when this grows past a flat table, swap its body for a call into
BetterWorld's DemocracyScore (API / shared package) — callers stay unchanged.

Source: Freedom House, *Freedom in the World 2026* — https://freedomhouse.org/country/scores
Score is the 0-100 Global Freedom score; status is Free / Partly Free / Not Free.
"""

FH_INDEX = "Freedom House (Freedom in the World)"
FH_YEAR = 2026

# Freedom House status -> regime tier. The factual frame issue #9 asks for: Free
# democracies, Not-Free authoritarian, Partly Free kept as the index's own label.
# Tier is derived ONLY from the cited status — no separate editorial judgement.
TIER = {"Free": "democracy", "Partly Free": "partly free", "Not Free": "authoritarian"}

# country -> (Global Freedom score 0-100, status, Freedom House country-page slug).
# Freedom in the World 2026 edition (covers 2025). Covers every jurisdiction the
# scanners currently emit, plus the wider "who governs it" frame (China, Gulf-type
# autocracies are added as they appear). Refresh annually from the URL above.
_SCORES = {
    "United States":  (81,  "Free",     "united-states"),
    "Norway":         (99,  "Free",     "norway"),
    "Sweden":         (99,  "Free",     "sweden"),
    "Finland":        (100, "Free",     "finland"),
    "Germany":        (95,  "Free",     "germany"),
    "France":         (89,  "Free",     "france"),
    "Netherlands":    (97,  "Free",     "netherlands"),
    "Switzerland":    (96,  "Free",     "switzerland"),
    "United Kingdom": (92,  "Free",     "united-kingdom"),
    "Russia":         (12,  "Not Free", "russia"),
    "China":          (9,   "Not Free", "china"),
}


def _country_of(jurisdiction):
    """'United States (CLOUD Act)' / 'Russia (origin)' / 'China' -> the bare
    country name. The scanners prefix the country, then a parenthetical axis
    qualifier (CLOUD Act / EU / EEA / non-EU / origin)."""
    return jurisdiction.split(" (")[0].strip()


def governance_for(jurisdiction):
    """Jurisdiction string -> cited governance-rating dict, or None when the
    country is not rated (e.g. 'Undetermined') or no jurisdiction is given.

    THE SEAM (issue #9): replace this body with a BetterWorld DemocracyScore
    lookup when the axis grows past a flat table; the returned shape is the
    contract callers depend on.
    """
    if not jurisdiction:
        return None
    entry = _SCORES.get(_country_of(jurisdiction))
    if not entry:
        return None
    score, status, slug = entry
    return {
        "country": _country_of(jurisdiction),
        "index": FH_INDEX,
        "score": score,
        "status": status,
        "tier": TIER[status],
        "sourceUrl": f"https://freedomhouse.org/country/{slug}/freedom-world/{FH_YEAR}",
        "year": FH_YEAR,
    }
