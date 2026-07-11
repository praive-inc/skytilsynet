#!/usr/bin/env python3
"""CSV formula-injection guard shared by the intake server and the review CLI.

Both the server exporter (server/foi_intake.py) and the offline review tooling
(scripts/foi_review.py) emit CSV built from untrusted public submissions, and the
operator who opens that CSV in Excel/LibreOffice/Sheets is the intended victim of
a formula-injection payload (CWE-1236). Keep the neutralizer here, in one shared
place, so neither package has to reach into the other to reuse it."""

# Leading characters a spreadsheet treats as the start of a formula (CWE-1236).
_CSV_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutralize a single CSV cell against spreadsheet formula injection: prefix a
    leading formula character with a `'` so Excel/LibreOffice/Sheets treat the cell
    as text. Submissions are untrusted public input and the operator who exports and
    opens the CSV is the intended victim, so apply this to every emitted cell."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_CSV_FORMULA_LEADERS) else s
