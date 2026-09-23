"""Locate the source workbooks in a folder, whatever they have been renamed to.

Files arrive from Drive, email and Slack, so the same workbook turns up as
`Coupon_Working_Aug_2026.xlsx`, `Coupon Working Aug 2026.xlsx` or
`Coupon Working Aug 2026 (1).xlsx`. Matching on an exact filename makes the
tools brittle for no good reason, so we match on the distinctive words instead.
"""
from __future__ import annotations

import re
from pathlib import Path

# key -> (words that must all appear, words that must not appear, extensions)
PATTERNS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    # The monthly coupon working. Must not pick up the formula version, which
    # holds a different month's data behind Google-Sheets-only functions.
    "coupon_working": (("coupon", "working"), ("automated",), (".xlsx", ".xlsm")),
    # The formula version, used as documentation of the rules.
    "coupon_working_automated": (("coupon", "working", "automated"), (), (".xlsx",)),
    "incentive_report": (("incentive", "report"), (), (".xlsx", ".xlsm")),
    "agent_details": (("agent", "details"), (), (".xlsx",)),
    "email_list": (("email",), (), (".xlsx",)),
    "sales_data": (("sales", "data"), (), (".csv", ".xlsx")),
}

LABELS = {
    "coupon_working": "the monthly Coupon Working workbook",
    "incentive_report": "the Field Incentive Report workbook",
    "agent_details": "the Coupon Agent Details file",
    "email_list": "the employee email list",
    "sales_data": "the raw sales extract",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find(source: Path, key: str) -> Path | None:
    """Return the best match for `key` in `source`, or None."""
    want, avoid, exts = PATTERNS[key]
    matches: list[Path] = []
    for p in sorted(source.iterdir()):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        stem = _norm(p.stem)
        if all(w in stem for w in want) and not any(a in stem for a in avoid):
            matches.append(p)
    if not matches:
        return None
    # Prefer the shortest name: 'Coupon Working Aug 2026.xlsx' over a copy
    # suffixed '(1)' or 'final v2'.
    return min(matches, key=lambda p: len(p.name))


def require(source: Path, *keys: str) -> dict[str, Path]:
    """Resolve several files at once, with a message naming what is missing."""
    if not source.is_dir():
        raise SystemExit(f"Not a folder: {source}")

    found, missing = {}, []
    for key in keys:
        p = find(source, key)
        if p is None:
            missing.append(key)
        else:
            found[key] = p

    if missing:
        listing = "\n".join(f"    {p.name}" for p in sorted(source.iterdir())
                            if p.is_file()) or "    (the folder is empty)"
        wanted = "\n".join(f"    - {LABELS.get(k, k)}" for k in missing)
        raise SystemExit(
            f"Could not find these in {source}:\n{wanted}\n\n"
            f"The folder contains:\n{listing}\n\n"
            "Filenames can use spaces or underscores; what matters is that the "
            "distinctive words are present."
        )
    return found
