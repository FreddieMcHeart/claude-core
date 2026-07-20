#!/usr/bin/env python3
"""Capture and compare cohort snapshots of Claude Code session cost metrics.

Freezes a snapshot over the two cost-discipline data sources — the per-session
cost ledger (``~/.claude/cost-ledger/``) and the fire-log
(``~/.claude/state/cost-discipline-log.jsonl``) — under a cohort label (e.g.
``pre-ccm-lite``), so a later cohort can be diffed with ``compare``.

**This is a DESCRIPTIVE tool, not a causal instrument.** A before/after
``compare`` delta is a weak, confounded signal — read it as "what the numbers
did", never as "the intervention caused this". The specific confounds this tool
does NOT correct for are recorded in every snapshot's ``caveats`` field and
summarised here:

- Ledger metrics (``result_tokens_est``, ``$/turn``, ``metered``,
  ``aggregate_reads``, by-tool share) are **since the last compaction**, not
  session totals — they move with how often/recently a session compacted, which
  an in-the-loop change is itself likely to shift.
- The ledger and the fire-log cover **different session populations** (the
  ledger exists only from 2026-07-18; the fire-log goes back to May), so a
  ``/sess`` figure means a different denominator on each side.
- Fire rates are per **firing** session (sessions that fired >=1 nudge). A cohort
  with more zero-fire sessions — arguably the goal — can look unchanged, because
  those clean sessions are absent from both numerator and denominator.
- Not corrected either: ``/handoff``+``/clear`` adoption, rule-threshold edits
  shipped in the same window, and interactive-vs-agent session mix.
- Even setting all that aside, per-session means do not control for task mix.

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

# Volume-driven nudges — the fires an in-the-loop memory layer would most plausibly
# reduce (context flood, bulk reads, edit churn). Tracked per firing session.
VOLUME_RULES = [
    "aggregate_15", "aggregate_25", "size_3mb", "tool_result_oversize",
    "edit_loop", "streak_4", "block_read_streak",
]

# Baked into every snapshot so a reader of the JSON sees the validity limits,
# not just a reader of this docstring.
CAVEATS = [
    "Descriptive only, not a causal instrument; cross-cohort deltas are confounded.",
    "Ledger metrics (result_tokens_est, $/turn, metered, aggregate_reads, by-tool) are "
    "SINCE THE LAST COMPACTION, not session totals — they move with compaction frequency.",
    "Ledger and fire-log cover different session populations (ledger since 2026-07-18; "
    "fire-log since May), so '/sess' denominators differ across the two sources.",
    "Fire rates are per FIRING session; zero-fire sessions are excluded, so a cohort with "
    "more clean sessions can look unchanged even if discipline actually improved.",
    "Not corrected: /handoff+/clear adoption, rule-threshold edits in the same window, "
    "interactive-vs-agent session mix, and task-mix differences between cohorts.",
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
        # An undateable row cannot be placed in a bounded window; admit it only
        # when the window is fully open (no bound on either side).
        return not since and not until
    if since and date < since:
        return False
    if until and date > until:
        return False
    return True


# ---------------------------------------------------------------- metrics

def ledger_metrics(ledgers):
    """Per-session ledger stats over active (non-zero) sessions. Every value here
    is SINCE THE LAST COMPACTION for its session, not a session lifetime total."""
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
        "window_note": "since last compaction",
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
    """Per-FIRING-session fire-log stats. The denominator is sessions that fired
    at least one nudge — NOT all active sessions — so a cohort with more zero-fire
    sessions can read unchanged here even if discipline improved."""
    n_firing = len({x.get("session_id") or "?" for x in fires})
    divisor = n_firing or 1
    n_fires = len(fires)
    actions = {}
    for x in fires:
        a = x.get("action") or "?"
        actions[a] = actions.get(a, 0) + 1
    by_rule = flr.by_rule(fires)
    return {
        "n_fires": n_fires,
        "n_firing_sessions": n_firing,
        "fires_per_firing_session_mean": round(n_fires / divisor, 2),
        "block_rate_of_fires": round(actions.get("block", 0) / n_fires, 3) if n_fires else 0.0,
        "actions": actions,
        "volume_rule_fires_per_firing_session": {
            rule: round(by_rule.get(rule, {}).get("total", 0) / divisor, 3)
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
        "caveats": CAVEATS,
        "ledger": ledger_metrics(led),
        "fire_log": firelog_metrics(fir),
    }


# ---------------------------------------------------------------- compare

def _scalars(snap):
    """Flatten a snapshot to comparable scalars. Ledger keys carry a ``*`` marker:
    per-ledger-session AND since-last-compaction; fire keys are per firing session."""
    ledger = snap.get("ledger", {})
    fire = snap.get("fire_log", {})
    rt = ledger.get("result_tokens_est", {})
    usd = ledger.get("cache_reread_usd_per_turn", {})
    out = {
        "ledger.n_sessions": ledger.get("n_sessions", 0),
        "ledger.tokens/sess* mean": rt.get("per_session_mean", 0),
        "ledger.tokens/sess* median": rt.get("per_session_median", 0),
        "ledger.$/turn* mean": usd.get("mean", 0),
        "ledger.tool_calls/sess*": ledger.get("tool_calls_per_session_mean", 0),
        "ledger.metered/sess*": ledger.get("metered_results_per_session_mean", 0),
        "ledger.aggr_reads/sess*": ledger.get("aggregate_reads_per_session_mean", 0),
        "fire.n_firing_sessions": fire.get("n_firing_sessions", 0),
        "fire.fires/firing-sess": fire.get("fires_per_firing_session_mean", 0),
        "fire.block_rate_of_fires": fire.get("block_rate_of_fires", 0),
    }
    for rule, val in (fire.get("volume_rule_fires_per_firing_session") or {}).items():
        out[f"fire.{rule}/firing-sess"] = val
    return out


def format_compare(a, b):
    sa, sb = _scalars(a), _scalars(b)
    keys = list(sa.keys()) + [k for k in sb if k not in sa]  # union, order-stable
    rows = []
    for key in keys:
        va, vb = sa.get(key), sb.get(key)
        a_cell = str(_num(va)) if va is not None else "—"
        b_cell = str(_num(vb)) if vb is not None else "—"
        if va is None or vb is None:
            delta, pct = "—", ("only-B" if va is None else "only-A")
        else:
            d = round(_num(vb) - _num(va), 3)
            delta = f"+{d}" if d > 0 else str(d)
            pct = f"{round(100 * (_num(vb) - _num(va)) / _num(va), 1)}%" if _num(va) else "-"
        rows.append([key, a_cell, b_cell, delta, pct])
    la, lb = a.get("label", "A"), b.get("label", "B")
    head = f"COMPARE — {la}  ->  {lb}   (DESCRIPTIVE / confounded — see caveats)"
    foot = ("* per-ledger-session AND since-last-compaction; fire rows are per FIRING "
            "session. Denominators differ across the two sources; a delta is not causal.")
    tbl = table(["METRIC", la, lb, "Δ", "%"], rows, ["<", ">", ">", ">", ">"])
    return f"{head}\n{'=' * len(head)}\n{tbl}\n{foot}"


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
    try:
        a, b = _load_snapshot(args.baseline), _load_snapshot(args.other)
    except (OSError, ValueError) as e:
        print(f"Could not read a snapshot: {e}", file=sys.stderr)
        return 1
    print(format_compare(a, b))
    return 0


def _cmd_list(args):
    out_dir = Path(args.out)
    if not out_dir.exists():
        print(f"No baselines yet at {out_dir}", file=sys.stderr)
        return 1
    for p in sorted(out_dir.glob("*.json")):
        try:
            snap = _load_snapshot(p)
        except (OSError, ValueError):
            continue  # tolerate a stray/half-written file, like the sibling loaders
        n = snap.get("ledger", {}).get("n_sessions", 0)
        print(f"{snap.get('label'):24}  {snap.get('captured_at', '?')}  ({n} ledger sessions)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture/compare session-cost metric snapshots.")
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
