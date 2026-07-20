#!/usr/bin/env python3
"""Capture and compare cohort baselines of Claude Code session cost metrics.

Freezes a normalised statistic over the two cost-discipline data sources — the
per-session cost ledger (``~/.claude/cost-ledger/``) and the fire-log
(``~/.claude/state/cost-discipline-log.jsonl``) — under a cohort label (e.g.
``pre-ccm-lite``), so a later cohort can be diffed against it with ``compare``.

Metrics are normalised PER SESSION (mean / median) because cohorts differ in
size; raw totals are recorded for reference only.

**Read this before trusting a delta.** Per-session means do NOT control for task
mix — two cohorts are only comparable if the work in them is similar in kind. A
cohort of huge refactors vs one of tiny Q&A will differ regardless of any
intervention. Treat a compare delta as signal only when the cohorts are
comparable and n is reasonably large. The honest primary "context bloat"
outcomes are ``result_tokens_est`` per session and the volume-driven fire rate.

Usage::

    python lib/metrics_snapshot.py capture --label pre-ccm-lite [--since D] [--until D]
    python lib/metrics_snapshot.py compare <baseline.json> <other.json>
    python lib/metrics_snapshot.py list
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import cost_ledger_report as clr
import fire_log_report as flr
from _report_table import table

BASELINE_DIR = Path.home() / ".claude" / "metrics-baselines"

# The volume-driven nudges — the fires that ccm-lite in the session loop should
# most reduce (context flood, bulk reads, edit churn). Tracked per session.
VOLUME_RULES = [
    "aggregate_15", "aggregate_25", "size_3mb", "tool_result_oversize",
    "edit_loop", "streak_4", "block_read_streak",
]


# ---------------------------------------------------------------- helpers

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _mean(xs):
    return round(statistics.mean(xs), 2) if xs else 0.0


def _median(xs):
    return round(statistics.median(xs), 2) if xs else 0.0


def _in_window(date, since, until):
    if not date:
        return not since  # undateable rows only pass when no lower bound is set
    if since and date < since:
        return False
    if until and date > until:
        return False
    return True


# ---------------------------------------------------------------- metrics

def ledger_metrics(ledgers):
    """Per-session normalised ledger stats over the active (non-zero) sessions."""
    active = clr.filter_ledgers(ledgers, show_all=False)
    tokens = [_num(x.get("tool_result_tokens_est")) for x in active]
    calls = [_num(x.get("tool_calls_total")) for x in active]
    metered = [_num(x.get("metered_results")) for x in active]
    aggr = [_num(x.get("aggregate_reads")) for x in active]
    usd = [_num(x.get("cache_reread_usd_per_turn_est")) for x in active]
    rollup = clr.by_tool_rollup(active)
    total_chars = sum(v["chars"] for v in rollup.values()) or 1
    return {
        "n_sessions": len(active),
        "result_tokens_est": {
            "total": sum(tokens),
            "per_session_mean": _mean(tokens),
            "per_session_median": _median(tokens),
        },
        "tool_calls_per_session_mean": _mean(calls),
        "metered_results_per_session_mean": _mean(metered),
        "aggregate_reads_per_session_mean": _mean(aggr),
        "cache_reread_usd_per_turn": {"mean": _mean(usd), "median": _median(usd)},
        "by_tool_volume_share_pct": {
            tool: round(100 * v["chars"] / total_chars, 1) for tool, v in rollup.items()
        },
    }


def firelog_metrics(fires):
    """Per-session normalised fire-log stats."""
    n_sessions = len({x.get("session_id") or "?" for x in fires}) or 1
    n_fires = len(fires)
    actions = {}
    for x in fires:
        a = x.get("action") or "?"
        actions[a] = actions.get(a, 0) + 1
    by_rule = flr.by_rule(fires)
    return {
        "n_fires": n_fires,
        "n_sessions_with_fires": n_sessions,
        "fires_per_session_mean": round(n_fires / n_sessions, 2),
        "block_rate": round(actions.get("block", 0) / n_fires, 3) if n_fires else 0.0,
        "actions": actions,
        "volume_rule_fires_per_session": {
            rule: round(by_rule.get(rule, {}).get("total", 0) / n_sessions, 3)
            for rule in VOLUME_RULES
        },
    }


def build_snapshot(label, ledgers, fires, since=None, until=None, captured_at=None):
    led = [x for x in ledgers if _in_window(clr._date(x), since, until)]
    fir = [x for x in fires if _in_window(flr._date(x), since, until)]
    return {
        "label": label,
        "captured_at": captured_at,
        "window": {"since": since, "until": until},
        "ledger": ledger_metrics(led),
        "fire_log": firelog_metrics(fir),
    }


# ---------------------------------------------------------------- compare

def _scalars(snap):
    ledger = snap.get("ledger", {})
    fire = snap.get("fire_log", {})
    rt = ledger.get("result_tokens_est", {})
    usd = ledger.get("cache_reread_usd_per_turn", {})
    out = {
        "ledger.n_sessions": ledger.get("n_sessions", 0),
        "ledger.tokens/sess mean": rt.get("per_session_mean", 0),
        "ledger.tokens/sess median": rt.get("per_session_median", 0),
        "ledger.$/turn mean": usd.get("mean", 0),
        "ledger.tool_calls/sess": ledger.get("tool_calls_per_session_mean", 0),
        "ledger.metered/sess": ledger.get("metered_results_per_session_mean", 0),
        "ledger.aggr_reads/sess": ledger.get("aggregate_reads_per_session_mean", 0),
        "fire.fires/sess": fire.get("fires_per_session_mean", 0),
        "fire.block_rate": fire.get("block_rate", 0),
    }
    for rule, val in (fire.get("volume_rule_fires_per_session") or {}).items():
        out[f"fire.{rule}/sess"] = val
    return out


def format_compare(a, b):
    sa, sb = _scalars(a), _scalars(b)
    rows = []
    for key in sa:
        va, vb = _num(sa.get(key, 0)), _num(sb.get(key, 0))
        delta = round(vb - va, 3)
        pct = f"{round(100 * (vb - va) / va, 1)}%" if va else "-"
        rows.append([key, str(va), str(vb), (f"+{delta}" if delta > 0 else str(delta)), pct])
    la, lb = a.get("label", "A"), b.get("label", "B")
    head = f"COMPARE — {la}  ->  {lb}  (lower is better for volume/fires)"
    tbl = table(["METRIC", la, lb, "Δ", "%"], rows, ["<", ">", ">", ">", ">"])
    return head + "\n" + "=" * len(head) + "\n" + tbl


# ---------------------------------------------------------------- cli

def _load_snapshot(path):
    return json.loads(Path(path).read_text())


def _write_snapshot(snap, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = snap["label"].replace("/", "-")
    path = out_dir / f"{safe}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, indent=2))
    tmp.replace(path)
    return path


def _cmd_capture(args):
    ledgers = clr.load_ledgers(Path(args.ledger_dir))
    fires = flr.load_fires(Path(args.fire_log))
    if not ledgers and not fires:
        print("No ledger or fire-log data found to snapshot.", file=sys.stderr)
        return 1
    snap = build_snapshot(
        args.label, ledgers, fires,
        since=args.since, until=args.until,
        captured_at=datetime.now(UTC).isoformat(),
    )
    path = _write_snapshot(snap, Path(args.out))
    print(f"captured '{args.label}' -> {path}")
    print(json.dumps(snap, indent=2))
    return 0


def _cmd_compare(args):
    a, b = _load_snapshot(args.baseline), _load_snapshot(args.other)
    print(format_compare(a, b))
    return 0


def _cmd_list(args):
    out_dir = Path(args.out)
    if not out_dir.exists():
        print(f"No baselines yet at {out_dir}", file=sys.stderr)
        return 1
    for p in sorted(out_dir.glob("*.json")):
        snap = _load_snapshot(p)
        n = snap.get("ledger", {}).get("n_sessions", 0)
        print(f"{snap.get('label'):24}  {snap.get('captured_at', '?')}  ({n} ledger sessions)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture/compare session-cost metric baselines.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="snapshot current metrics under a cohort label")
    cap.add_argument("--label", required=True, help="cohort label, e.g. pre-ccm-lite")
    cap.add_argument("--ledger-dir", default=str(clr.DEFAULT_DIR), help="cost-ledger directory")
    cap.add_argument("--fire-log", default=str(flr.DEFAULT_LOG), help="fire-log JSONL path")
    cap.add_argument("--since", metavar="YYYY-MM-DD", help="lower date bound (inclusive)")
    cap.add_argument("--until", metavar="YYYY-MM-DD", help="upper date bound (inclusive)")
    cap.add_argument("--out", default=str(BASELINE_DIR), help="baseline output directory")
    cap.set_defaults(func=_cmd_capture)

    cmp = sub.add_parser("compare", help="diff two saved snapshots")
    cmp.add_argument("baseline", help="path to the baseline snapshot JSON")
    cmp.add_argument("other", help="path to the snapshot to compare against it")
    cmp.set_defaults(func=_cmd_compare)

    lst = sub.add_parser("list", help="list saved baselines")
    lst.add_argument("--out", default=str(BASELINE_DIR), help="baseline directory")
    lst.set_defaults(func=_cmd_list)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
