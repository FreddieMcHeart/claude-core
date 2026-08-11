#!/usr/bin/env python3
"""Real API token usage per rate-limit window, read from session transcripts.

Claude Code prints two rate-limit gauges — a five-hour window and a seven-day
one — as PERCENTAGES with a reset time, and nothing else. The absolute token
count behind the percentage is not published anywhere the agent can read, so
"86% of weekly" cannot be turned into "how many tokens is that" from the status
line alone. This reads the other end: every session transcript carries the
actual per-request ``message.usage`` the API returned, timestamped. Summing
those over the window gives the numerator the harness withholds.

WHY NOT THE COST LEDGER. ``lib/cost_ledger_report.py`` reads
``~/.claude/cost-ledger/`` and reports "tokens" that are ``chars / 3.5``
estimates of TOOL OUTPUT volume. That is a different quantity measured for a
different purpose (context drag), and it is not what the API billed. The two
must not be compared or added.

WHY NOT THE INSTALLED ``claude-cost``. That tool reads the same transcripts and
sums the same fields, so its token arithmetic is sound. Its PRICING is not, in
three separate ways measured 2026-08-11: its table names ``claude-opus-4-6`` /
``claude-sonnet-4-6`` / ``claude-haiku-4-5`` while the transcripts on this
machine carry ``claude-opus-5``, ``claude-opus-4-8``, ``claude-opus-4-7`` and
``claude-sonnet-5`` — an empty intersection; a substring fallback then applies
the older rate silently rather than failing; and ``cache_creation_input_tokens``
is never read at all. This module therefore reports TOKENS ONLY. Adding a
dollar figure would mean adopting a price constant with no cited source, which
is the specific defect that put a wrong ``$/turn`` on the status line for a day.

THE TOP-LEVEL / ITERATIONS RULE, and it is measured rather than assumed.
``message.usage`` carries the four token fields at the top level AND, on 99.6%
of records, again inside a single-element ``iterations`` array. Over 19,379
assistant records spanning six sessions and two months:

  - both non-zero: 19,302 records, and the two sums are EQUAL in every one
  - every ``iterations`` array had exactly one element; none had more
  - top level all-zero while ``iterations`` carried real figures: 4 records
    (0.02%), worth 2,428,387 tokens of 7,280,657,415 — a 0.033% under-count

So the correct reader takes the LARGER of the two and never adds them; adding
would double almost every record. The 4-record case is small but free to handle,
and ``iterations`` longer than one was never observed — so it is counted and
REPORTED rather than silently assumed away, because an unobserved shape turning
up is exactly what a summary should not swallow.

A NUMBER FROM HERE IS A SNAPSHOT, not a constant. Live sessions append to their
transcripts while the scan runs; two runs minutes apart legitimately differ. The
report states its own scan time for that reason.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _report_table import table as _table

DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_WINDOWS = Path.home() / ".claude" / "state" / "rate-limits.json"

# The four fields the API reports and bills on. Kept as a tuple rather than
# summed inline because every consumer below wants the breakdown too: cache
# reads are roughly an order of magnitude cheaper than fresh input, so a single
# blended token total would mislead in the expensive direction.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

WINDOWS = {"5h": timedelta(hours=5), "7d": timedelta(days=7)}


# ---------------------------------------------------------------- extraction

def _fields(d):
    """The four token fields as a plain dict, coercing junk to 0."""
    out = {}
    for field in TOKEN_FIELDS:
        value = d.get(field)
        ok = not isinstance(value, bool) and isinstance(value, (int, float))
        out[field] = int(value) if ok else 0
    return out


def usage_tokens(usage):
    """(fields, multi_iteration) for one ``message.usage`` object.

    Returns the four token fields from whichever source reports more — the top
    level, or the ``iterations`` array summed. See the module docstring for the
    measurement behind this: the two are equal wherever both are non-zero, so
    "larger" is a tie-break, not a guess, and adding them would double-count.

    ``multi_iteration`` is True when ``iterations`` held more than one element.
    That shape was never observed and the caller reports the count, because a
    silently-handled unknown shape is indistinguishable from one that never
    occurred.
    """
    if not isinstance(usage, dict):
        return _fields({}), False

    top = _fields(usage)
    iters = usage.get("iterations")
    if not isinstance(iters, list) or not iters:
        return top, False

    merged = {field: 0 for field in TOKEN_FIELDS}
    for entry in iters:
        if not isinstance(entry, dict):
            continue
        for field, value in _fields(entry).items():
            merged[field] += value

    if sum(merged.values()) > sum(top.values()):
        return merged, len(iters) > 1
    return top, len(iters) > 1


def _parse_ts(value):
    """A transcript ``timestamp`` as an aware datetime, or None if unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- scanning

def scan_file(path, since, until):
    """Aggregate one transcript. Returns a dict, never raises.

    ``read_error`` is its own key rather than an exception or a zero, because a
    file that could not be opened and a file with nothing in the window produce
    identical totals and must not produce identical reports.
    """
    out = {
        "records": 0,
        "in_window": 0,
        "no_timestamp": 0,
        "parse_errors": 0,
        "multi_iteration": 0,
        "read_error": None,
        "by_model": {},
        "tokens": {field: 0 for field in TOKEN_FIELDS},
        "first_ts": None,
        "last_ts": None,
    }
    try:
        handle = Path(path).open(encoding="utf-8", errors="replace")
    except OSError as exc:
        out["read_error"] = str(exc)
        return out

    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                out["parse_errors"] += 1
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue

            out["records"] += 1
            stamp = _parse_ts(rec.get("timestamp"))
            if stamp is None:
                out["no_timestamp"] += 1
                continue
            if stamp < since or stamp > until:
                continue

            fields, multi = usage_tokens(usage)
            if multi:
                out["multi_iteration"] += 1
            out["in_window"] += 1
            for field, value in fields.items():
                out["tokens"][field] += value
            model = message.get("model") or "unknown"
            bucket = out["by_model"].setdefault(model, {f: 0 for f in TOKEN_FIELDS})
            for field, value in fields.items():
                bucket[field] += value
            if out["first_ts"] is None or stamp < out["first_ts"]:
                out["first_ts"] = stamp
            if out["last_ts"] is None or stamp > out["last_ts"]:
                out["last_ts"] = stamp
    return out


def scan(projects_dir, since, until):
    """Walk every transcript under ``projects_dir`` and aggregate the window.

    Files whose mtime predates ``since`` are skipped without being opened. That
    is sound because transcripts are append-only, so a file's last write is at
    or after its newest record — but it is an assumption about the harness, so
    the count of files skipped this way is reported rather than hidden.
    """
    sessions = {}
    stats = {
        "files_seen": 0,
        "files_skipped_mtime": 0,
        "files_unreadable": 0,
        "parse_errors": 0,
        "multi_iteration": 0,
        "no_timestamp": 0,
    }
    root = Path(projects_dir)
    for path in sorted(root.glob("*/*.jsonl")):
        stats["files_seen"] += 1
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            mtime = None
        if mtime is not None and mtime < since:
            stats["files_skipped_mtime"] += 1
            continue
        result = scan_file(path, since, until)
        if result["read_error"] is not None:
            stats["files_unreadable"] += 1
            continue
        stats["parse_errors"] += result["parse_errors"]
        stats["multi_iteration"] += result["multi_iteration"]
        stats["no_timestamp"] += result["no_timestamp"]
        if result["in_window"] == 0:
            continue
        sessions[path.stem] = {
            "session_id": path.stem,
            "project": path.parent.name,
            "records": result["in_window"],
            "tokens": result["tokens"],
            "by_model": result["by_model"],
            "first_ts": result["first_ts"],
            "last_ts": result["last_ts"],
        }
    return sessions, stats


# ---------------------------------------------------------------- windows

def load_windows(path=None):
    """Rate-limit percentages and reset times, or {} when unavailable.

    Written by the status-line wrapper, which is the only thing on this machine
    that receives them. Absent is the normal case on a fresh install, and the
    caller must degrade rather than fail: the token totals do not depend on this
    file, only the share-of-limit figures do.
    """
    target = Path(DEFAULT_WINDOWS if path is None else path)
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_window(name, windows, now=None):
    """(since, until, source) for a named window.

    Anchors on the harness's own ``resets_at`` when it is available, so the
    boundary matches the percentage the status line prints. Falls back to
    "ending now", which is off by however far into the window we are — a real
    difference, so ``source`` names which was used instead of leaving the reader
    to assume the exact one.
    """
    now = datetime.now(UTC) if now is None else now
    span = WINDOWS[name]
    key = "five_hour" if name == "5h" else "seven_day"
    entry = windows.get(key) if isinstance(windows.get(key), dict) else {}
    resets_at = entry.get("resets_at")
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        until = datetime.fromtimestamp(float(resets_at), tz=UTC)
        return until - span, until, "resets_at"
    return now - span, now, "now"


# ---------------------------------------------------------------- rollups

def totals(sessions):
    out = {field: 0 for field in TOKEN_FIELDS}
    for entry in sessions.values():
        for field, value in entry["tokens"].items():
            out[field] += value
    return out


def by_model(sessions):
    out = {}
    for entry in sessions.values():
        for model, fields in entry["by_model"].items():
            bucket = out.setdefault(model, {f: 0 for f in TOKEN_FIELDS})
            for field, value in fields.items():
                bucket[field] += value
    return out


def session_rows(sessions, window_total, used_percentage=None, top=15):
    """Per-session rows, largest first.

    ``pct_points`` is the session's share of the rate-limit window expressed in
    percentage points of the harness's own gauge — session tokens over window
    tokens, times the percentage the harness reports. That needs no knowledge of
    the limit itself, which is deliberate: the limit could only be recovered by
    dividing by a rounded percentage, and at 43% a single point of rounding is
    worth 2.3% of the answer.
    """
    rows = sorted(sessions.values(), key=lambda e: -sum(e["tokens"].values()))
    if top:
        rows = rows[:top]
    out = []
    for entry in rows:
        total = sum(entry["tokens"].values())
        share = (total / window_total) if window_total else 0.0
        out.append({
            "session_id": entry["session_id"],
            "project": entry["project"],
            "records": entry["records"],
            "tokens": entry["tokens"],
            "total": total,
            "share": share,
            "pct_points": (share * used_percentage) if used_percentage is not None else None,
            "tok_per_min": _rate(total, entry["first_ts"], entry["last_ts"]),
        })
    return out


def _rate(total, first_ts, last_ts):
    """Tokens per minute over the session's own observed span, or None.

    Deliberately NOT over the whole window: a session active for four minutes
    inside a seven-day window has a meaningful rate over four minutes and a
    meaningless one over seven days.
    """
    if first_ts is None or last_ts is None:
        return None
    minutes = (last_ts - first_ts).total_seconds() / 60.0
    if minutes <= 0:
        return None
    return total / minutes


# ---------------------------------------------------------------- output

def build_json(name, sessions, stats, since, until, source, used_percentage, top=15):
    tot = totals(sessions)
    window_total = sum(tot.values())
    minutes = (until - since).total_seconds() / 60.0
    return {
        "window": {
            "name": name,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "boundary_source": source,
            "used_percentage": used_percentage,
        },
        "scanned_at": datetime.now(UTC).isoformat(),
        "totals": tot,
        "total_tokens": window_total,
        "tok_per_min_window": (window_total / minutes) if minutes > 0 else None,
        "sessions": session_rows(sessions, window_total, used_percentage, top=top),
        "by_model": by_model(sessions),
        "coverage": stats,
    }


def _human(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def format_report(payload):
    win = payload["window"]
    lines = [
        f"API token usage — {win['name']} window",
        f"  {win['since']}  ->  {win['until']}   (boundary from {win['boundary_source']})",
    ]
    if win["boundary_source"] == "now":
        lines.append("  NOTE: no resets_at available, window ends now rather than "
                     "at the real reset.")
    if win["used_percentage"] is None:
        lines.append("  NOTE: no used_percentage available, so share-of-limit is not computed.")
    else:
        lines.append(f"  harness reports this window {win['used_percentage']}% used")
    lines.append("")

    tot = payload["totals"]
    lines.append(f"total {_human(payload['total_tokens'])} tokens"
                 f"   input {_human(tot['input_tokens'])}"
                 f" · output {_human(tot['output_tokens'])}"
                 f" · cache-read {_human(tot['cache_read_input_tokens'])}"
                 f" · cache-write {_human(tot['cache_creation_input_tokens'])}")
    rate = payload["tok_per_min_window"]
    if rate is not None:
        lines.append(f"averaged over the window: {rate:,.0f} tok/min")
    lines.append("")

    rows = []
    for row in payload["sessions"]:
        rows.append([
            row["session_id"][:8],
            row["project"][:28],
            str(row["records"]),
            _human(row["total"]),
            f"{row['share'] * 100:.1f}%",
            "-" if row["pct_points"] is None else f"{row['pct_points']:.1f}",
            "-" if row["tok_per_min"] is None else f"{row['tok_per_min']:,.0f}",
        ])
    if rows:
        lines.append(_table(
            ["session", "project", "msgs", "tokens", "share", "pts", "tok/min"],
            rows,
            ["<", "<", ">", ">", ">", ">", ">"],
        ))
        lines.append("")

    if payload["by_model"]:
        rows = [[m, _human(sum(f.values()))] for m, f in
                sorted(payload["by_model"].items(), key=lambda kv: -sum(kv[1].values()))]
        lines.append(_table(["model", "tokens"], rows, ["<", ">"]))
        lines.append("")

    cov = payload["coverage"]
    lines.append(
        f"coverage: {cov['files_seen']} transcripts seen, "
        f"{cov['files_skipped_mtime']} skipped as older than the window, "
        f"{cov['files_unreadable']} unreadable, "
        f"{cov['parse_errors']} unparseable lines, "
        f"{cov['no_timestamp']} records without a timestamp"
    )
    if cov["multi_iteration"]:
        lines.append(
            f"  {cov['multi_iteration']} record(s) carried more than one usage iteration — "
            "a shape never seen when this reader was measured; the larger of top-level and "
            "the iteration sum was used, but the count is surfaced rather than assumed away."
        )
    lines.append(f"scanned at {payload['scanned_at']} — live sessions grow during the scan, "
                 "so this is a snapshot rather than a stable figure.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Real API token usage per rate-limit window, from session transcripts.")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="5h", help="which window")
    ap.add_argument("--projects", default=str(DEFAULT_PROJECTS), help="transcripts directory")
    ap.add_argument("--windows-file", default=str(DEFAULT_WINDOWS),
                    help="rate-limit percentages written by the status line")
    ap.add_argument("--top", type=int, default=15, help="rows in the session table (0 = all)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    args = ap.parse_args(argv)

    projects = Path(args.projects)
    if not projects.is_dir():
        print(f"No transcripts directory at {projects}", file=sys.stderr)
        return 1

    windows = load_windows(args.windows_file)
    since, until, source = resolve_window(args.window, windows)
    key = "five_hour" if args.window == "5h" else "seven_day"
    entry = windows.get(key) if isinstance(windows.get(key), dict) else {}
    used = entry.get("used_percentage")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        used = None

    sessions, stats = scan(projects, since, until)
    payload = build_json(args.window, sessions, stats, since, until, source, used, top=args.top)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
