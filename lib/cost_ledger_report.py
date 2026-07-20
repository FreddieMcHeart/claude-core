#!/usr/bin/env python3
"""Summarise the cross-session cost ledger.

The cost-discipline hook writes one JSON file per session under
``~/.claude/cost-ledger/<session_id>.json`` (see ``hooks/cost-discipline.py``,
``build_cost_ledger``). This reads them, aggregates, and prints a compact
report: totals, a by-tool rollup, and a per-session table.

Unlike the ``claude-cost-audit`` skill — which parses full session JSONL via a
Haiku worker — this reads only the pre-aggregated ledgers, so it is instant and
needs no model call. It is the cheap first look; drop to the JSONL audit only
when a session needs drilling into.

Zero-activity sessions (seeded at SessionStart but with no metered results) are
hidden by default; pass ``--all`` to include them.

Usage::

    python lib/cost_ledger_report.py [--top N] [--all] [--since YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_DIR = Path.home() / ".claude" / "cost-ledger"


# ---------------------------------------------------------------- loading

def load_ledgers(ledger_dir):
    """Read + parse every ``<session>.json``; skip unreadable/malformed files."""
    out = []
    for p in sorted(Path(ledger_dir).glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


# ---------------------------------------------------------------- helpers

def _date(led):
    return (led.get("updated_at") or led.get("started_at") or "")[:10]


def is_zero_session(led):
    """A ledger seeded at SessionStart that never metered a result."""
    return not led.get("metered_results") and not led.get("tool_result_chars")


def _tool_chars(value):
    """by_tool values are ``{chars, tokens}`` in a written ledger, but tolerate a
    bare int in case a raw state dict is passed in."""
    return value.get("chars", 0) if isinstance(value, dict) else (value or 0)


def _tool_tokens(value):
    return value.get("tokens", 0) if isinstance(value, dict) else 0


def _top_tool(led):
    by_tool = led.get("tool_result_chars_by_tool") or {}
    if not by_tool:
        return "-"
    return max(by_tool.items(), key=lambda kv: _tool_chars(kv[1]))[0]


def _short_tool(name):
    """mcp__server__tool -> mcp:tool so the table column stays narrow."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        return "mcp:" + (parts[-1] if parts[-1] else parts[1] if len(parts) > 1 else name)
    return name


# ---------------------------------------------------------------- filtering

def filter_ledgers(ledgers, show_all=False, since=None):
    rows = ledgers
    if since:
        rows = [x for x in rows if _date(x) >= since]
    if not show_all:
        rows = [x for x in rows if not is_zero_session(x)]
    return rows


# ---------------------------------------------------------------- aggregation

def totals(ledgers):
    dates = [d for d in (_date(x) for x in ledgers) if d]
    models = {}
    for x in ledgers:
        name = x.get("main_model") or "?"
        models[name] = models.get(name, 0) + 1
    return {
        "sessions": len(ledgers),
        "window": [min(dates), max(dates)] if dates else ["-", "-"],
        "tool_calls_total": sum(x.get("tool_calls_total", 0) for x in ledgers),
        "metered_results": sum(x.get("metered_results", 0) for x in ledgers),
        "result_chars": sum(x.get("tool_result_chars", 0) for x in ledgers),
        "result_tokens_est": sum(x.get("tool_result_tokens_est", 0) for x in ledgers),
        "aggregate_reads": sum(x.get("aggregate_reads", 0) for x in ledgers),
        "models": models,
    }


def by_tool_rollup(ledgers):
    """{tool: {chars, tokens}} summed across ledgers, sorted by chars descending."""
    acc = {}
    for x in ledgers:
        for tool, value in (x.get("tool_result_chars_by_tool") or {}).items():
            bucket = acc.setdefault(tool, {"chars": 0, "tokens": 0})
            bucket["chars"] += _tool_chars(value)
            bucket["tokens"] += _tool_tokens(value)
    return dict(sorted(acc.items(), key=lambda kv: kv[1]["chars"], reverse=True))


def per_session_rows(ledgers, top=15):
    """Ledgers sorted by est result-tokens descending; ``top<=0`` returns all."""
    ranked = sorted(ledgers, key=lambda x: x.get("tool_result_tokens_est", 0), reverse=True)
    return ranked[:top] if top and top > 0 else ranked


# ---------------------------------------------------------------- formatting

def _table(headers, rows, aligns=None):
    """Render a fixed-width text table. aligns: list of '<'/'>' per column."""
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    widths = [max(len(str(c)) for c in col) for col in cols]
    aligns = aligns or ["<"] * len(headers)

    def render(cells):
        return "  ".join(f"{str(c):{a}{w}}" for c, w, a in zip(cells, widths, aligns))

    out = [render(headers), render(["-" * w for w in widths])]
    out.extend(render(r) for r in rows)
    return "\n".join(out)


def format_report(all_ledgers, top=15, show_all=False, since=None):
    shown = filter_ledgers(all_ledgers, show_all=show_all, since=since)
    in_window = filter_ledgers(all_ledgers, show_all=True, since=since)
    hidden = len(in_window) - len(shown)

    t = totals(shown)
    parts = []
    parts.append("=" * 64)
    parts.append("COST LEDGER — cross-session summary")
    parts.append("=" * 64)
    models = ", ".join(f"{k}:{v}" for k, v in t["models"].items()) or "-"
    parts.append(
        f"sessions        : {t['sessions']}"
        + (f"  (+{hidden} zero-activity hidden; --all to show)" if hidden else "")
    )
    parts.append(f"window          : {t['window'][0]} .. {t['window'][1]}")
    parts.append(f"tool calls      : {t['tool_calls_total']}")
    parts.append(f"metered results : {t['metered_results']}")
    parts.append(
        f"result volume   : {t['result_chars']:,} chars"
        f"  (~{t['result_tokens_est'] // 1000}k tokens est)"
    )
    parts.append(f"aggregate reads : {t['aggregate_reads']}")
    parts.append(f"main models     : {models}")

    parts.append("")
    parts.append("BY TOOL (metered result volume)")
    bt = by_tool_rollup(shown)
    bt_rows = [
        [tool, f"{v['chars']:,}", f"{v['tokens'] // 1000}k"]
        for tool, v in bt.items()
    ]
    parts.append(
        _table(["TOOL", "CHARS", "~KTOK"], bt_rows, ["<", ">", ">"])
        if bt_rows else "  (none)"
    )

    parts.append("")
    label = "ALL SESSIONS" if top <= 0 else f"TOP {min(top, len(shown))} SESSIONS"
    parts.append(f"{label} (by est result-tokens)")
    ps_rows = [
        [
            _date(x) or "-",
            (x.get("main_model") or "?"),
            str(x.get("tool_calls_total", 0)),
            str(x.get("metered_results", 0)),
            f"{x.get('tool_result_tokens_est', 0) // 1000}k",
            str(x.get("aggregate_reads", 0)),
            f"{x.get('cache_reread_usd_per_turn_est', 0):.2f}",
            (Path(x.get("cwd", "")).name or "?")[:14],
            _short_tool(_top_tool(x))[:22],
        ]
        for x in per_session_rows(shown, top=top)
    ]
    headers = ["DATE", "MODEL", "CALLS", "METRD", "~KTOK", "AGGR", "$/TURN", "CWD", "TOP-TOOL"]
    aligns = ["<", "<", ">", ">", ">", ">", ">", "<", "<"]
    parts.append(_table(headers, ps_rows, aligns) if ps_rows else "  (no active sessions)")

    return "\n".join(parts)


def build_json(all_ledgers, top=15, show_all=False, since=None):
    shown = filter_ledgers(all_ledgers, show_all=show_all, since=since)
    return {
        "totals": totals(shown),
        "by_tool": by_tool_rollup(shown),
        "sessions": per_session_rows(shown, top=top),
    }


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarise the cross-session cost ledger.")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="ledger directory")
    ap.add_argument("--top", type=int, default=15, help="rows in the per-session table (0 = all)")
    ap.add_argument("--all", action="store_true", help="include zero-activity sessions")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="only sessions on/after this date")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    args = ap.parse_args(argv)

    ledger_dir = Path(args.dir)
    if not ledger_dir.exists():
        print(f"No cost-ledger directory at {ledger_dir}", file=sys.stderr)
        print("Created on the first session the cost-discipline hook runs in.", file=sys.stderr)
        return 1

    ledgers = load_ledgers(ledger_dir)
    if not ledgers:
        print(f"No ledger files found in {ledger_dir}", file=sys.stderr)
        return 1

    if args.json:
        payload = build_json(ledgers, top=args.top, show_all=args.all, since=args.since)
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(ledgers, top=args.top, show_all=args.all, since=args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
