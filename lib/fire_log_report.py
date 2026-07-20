#!/usr/bin/env python3
"""Summarise the cost-discipline fire-log.

The cost-discipline hook appends one JSON line per fired nudge to
``~/.claude/state/cost-discipline-log.jsonl`` (see ``hooks/cost-discipline.py``,
``log_fire``). Each entry has ``{ts, rule, action, session_id}`` plus rule-specific
extras (``file_path`` on the edit-loop rules, and others this report does not read).

Where the cost-ledger report (``cost_ledger_report.py``) answers "what floods
context", this answers the other half: "where is the methodology actually being
worked past". It ranks the rules that fire most, splits warn / info / block
(block = the hook hard-stopped a call), and shows which sessions rubbed against
discipline the most. ``--rule`` drills into one rule's file hotspots.

Usage::

    python lib/fire_log_report.py [--top N] [--since YYYY-MM-DD] [--rule NAME] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_LOG = Path.home() / ".claude" / "state" / "cost-discipline-log.jsonl"


# ---------------------------------------------------------------- loading

def load_fires(log_path):
    """Parse the JSONL fire-log; skip blank / malformed lines and non-dict rows."""
    out = []
    try:
        text = Path(log_path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


# ---------------------------------------------------------------- helpers

def _date(entry):
    # type-safe: a hand-edited / partially-written line could carry a non-string
    # ts (e.g. an epoch int); slicing that would raise and abort the whole report.
    ts = entry.get("ts")
    return ts[:10] if isinstance(ts, str) else ""


def _table(headers, rows, aligns=None):
    """Fixed-width text table. Mirrors the hardened ``cost_ledger_report._table``
    (kept as a self-contained copy so this CLI runs standalone): rows are
    normalised to the header count so a mismatched row can never silently drop a
    header column via ``zip``'s shortest-tuple behaviour."""
    n = len(headers)
    norm = [list(r)[:n] + [""] * (n - len(r)) for r in rows]
    aligns = aligns or ["<"] * n
    widths = [
        max([len(str(headers[i]))] + [len(str(r[i])) for r in norm])
        for i in range(n)
    ]

    def render(cells):
        return "  ".join(f"{str(c):{a}{w}}" for c, w, a in zip(cells, widths, aligns))

    out = [render(headers), render(["-" * w for w in widths])]
    out.extend(render(r) for r in norm)
    return "\n".join(out)


# ---------------------------------------------------------------- filtering

def filter_fires(fires, since=None, rule=None):
    rows = fires
    if since:
        rows = [x for x in rows if _date(x) >= since]
    if rule:
        rows = [x for x in rows if x.get("rule") == rule]
    return rows


# ---------------------------------------------------------------- aggregation

def totals(fires):
    dates = [d for d in (_date(x) for x in fires) if d]
    return {
        "fires": len(fires),
        "window": [min(dates), max(dates)] if dates else ["-", "-"],
        # count "?" the same way by_session/by_rule bucket a missing value, so the
        # headline counts always match the rows rendered in the tables below.
        "sessions": len({x.get("session_id") or "?" for x in fires}),
        "rules": len({x.get("rule") or "?" for x in fires}),
        "actions": dict(Counter(x.get("action") or "?" for x in fires)),
    }


def by_rule(fires):
    """{rule: {total, warn, info, block}} sorted by total descending."""
    acc = {}
    for x in fires:
        rule = x.get("rule") or "?"
        bucket = acc.setdefault(rule, {"total": 0, "warn": 0, "info": 0, "block": 0})
        bucket["total"] += 1
        action = x.get("action") or "?"
        if action in ("warn", "info", "block"):  # fixed set, not the bucket dict
            bucket[action] += 1
    return dict(sorted(acc.items(), key=lambda kv: kv[1]["total"], reverse=True))


def by_session(fires):
    """{session_id: count} sorted descending."""
    return dict(Counter(x.get("session_id") or "?" for x in fires).most_common())


def by_file(fires):
    """{file_path: count} for entries that carry a file_path, descending."""
    return dict(Counter(x["file_path"] for x in fires if x.get("file_path")).most_common())


# ---------------------------------------------------------------- formatting

def _cap(items, top):
    return items[:top] if top and top > 0 else items


def format_report(all_fires, top=15, since=None, rule=None):
    fires = filter_fires(all_fires, since=since, rule=rule)
    t = totals(fires)
    actions = t["actions"]
    ordered = [f"{a}:{actions[a]}" for a in ("warn", "info", "block") if a in actions]
    ordered += [f"{a}:{c}" for a, c in actions.items() if a not in ("warn", "info", "block")]

    parts = []
    parts.append("=" * 64)
    parts.append("COST-DISCIPLINE FIRE-LOG" + (f" — rule: {rule}" if rule else " — summary"))
    parts.append("=" * 64)
    parts.append(f"fires    : {t['fires']}")
    parts.append(f"window   : {t['window'][0]} .. {t['window'][1]}")
    parts.append(f"sessions : {t['sessions']}")
    parts.append(f"rules    : {t['rules']}")
    parts.append(f"actions  : {'  '.join(ordered) or '-'}")

    if not rule:
        parts.append("")
        parts.append("BY RULE (most-fired first — where discipline is worked past)")
        br_rows = [
            [r, str(v["total"]), str(v["warn"]), str(v["info"]), str(v["block"])]
            for r, v in _cap(list(by_rule(fires).items()), top)
        ]
        parts.append(
            _table(["RULE", "FIRES", "WARN", "INFO", "BLOCK"], br_rows,
                   ["<", ">", ">", ">", ">"])
            if br_rows else "  (none)"
        )

    parts.append("")
    parts.append("TOP SESSIONS (by fires)")
    bs_rows = [[sid[:12], str(c)] for sid, c in _cap(list(by_session(fires).items()), top)]
    parts.append(_table(["SESSION", "FIRES"], bs_rows, ["<", ">"]) if bs_rows else "  (none)")

    if rule:
        bf_rows = [[fp, str(c)] for fp, c in _cap(list(by_file(fires).items()), top)]
        if bf_rows:
            parts.append("")
            parts.append("FILE HOTSPOTS")
            parts.append(_table(["FILE", "FIRES"], bf_rows, ["<", ">"]))

    return "\n".join(parts)


def build_json(all_fires, top=15, since=None, rule=None):
    fires = filter_fires(all_fires, since=since, rule=rule)
    payload = {
        "totals": totals(fires),
        "by_rule": dict(_cap(list(by_rule(fires).items()), top)),
        "top_sessions": dict(_cap(list(by_session(fires).items()), top)),
    }
    if rule:
        payload["file_hotspots"] = dict(_cap(list(by_file(fires).items()), top))
    return payload


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarise the cost-discipline fire-log.")
    ap.add_argument("--file", default=str(DEFAULT_LOG), help="fire-log JSONL path")
    ap.add_argument("--top", type=int, default=15, help="rows per table (0 = all)")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="only fires on/after this date")
    ap.add_argument("--rule", help="drill into one rule (adds file hotspots)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    args = ap.parse_args(argv)

    log_path = Path(args.file)
    if not log_path.exists():
        print(f"No fire-log at {log_path}", file=sys.stderr)
        print("Written the first time a cost-discipline nudge fires.", file=sys.stderr)
        return 1

    fires = load_fires(log_path)
    if not fires:
        print(f"Fire-log is empty or unparseable: {log_path}", file=sys.stderr)
        return 1

    if args.json:
        payload = build_json(fires, top=args.top, since=args.since, rule=args.rule)
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(fires, top=args.top, since=args.since, rule=args.rule))
    return 0


if __name__ == "__main__":
    sys.exit(main())
