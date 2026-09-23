#!/usr/bin/env python3
"""Prompt-cache totals per session, read from session transcripts.

For each session this reports how many tokens were WRITTEN to the prompt cache,
what share of the session's cost those writes are, and how many of them were
cold rebuilds. Main chain and sub-agent transcripts are both read, and kept
apart, because they behave differently (see below).

WHERE THE FILES ARE. A session's main chain is ``<projects>/<slug>/<id>.jsonl``.
Its sub-agents are ``<projects>/<slug>/<id>/subagents/agent-*.jsonl`` — one
level deeper, so a ``*/*.jsonl`` glob never sees them. The parent session is
the DIRECTORY name, not the file stem.

DEDUP BY ``message.id``, KEEP THE LAST LINE. One API response is written as one
transcript line per content block, and every one of those lines repeats the
same ``message.usage``. Summing lines overcounts. Measured 2026-09-23 on one
main transcript and one sub-agent transcript: per-line sums ran 1.70x to 3.15x
over the deduped figure. Across 43 repeated ids the input and cache fields were
identical on every repeat, and the last line always carried the largest
``output_tokens`` — so the last line is the one kept.

SHARE OF COST, WITHOUT A DOLLAR FIGURE. Every field is weighted by its price as
a multiple of base input, and on all three current models those multiples are
the same: output 5x, cache read 0.1x, 5-minute cache write 1.25x, 1-hour cache
write 2x (``WEIGHTS`` below; source in ``WEIGHTS_SOURCE``). So a share can be
computed without a price constant. The TTL comes from
``usage.cache_creation.ephemeral_{5m,1h}_input_tokens``; on the transcripts
measured, the main chain wrote at 1h and the sub-agent at 5m. Write tokens with
no split are weighted at the 5-minute rate, which makes the share a LOWER
bound, and their count is reported so it cannot hide.

COLD REBUILD. A request whose cache write is at least ``REBUILD_RATIO`` of its
whole prompt (input + cache read + cache write): most of the prefix had to be
written again. The first request of every chain always looks like that, so it
is never counted as a rebuild. A session's first request and a sub-agent's
first request are reported as prefix writes instead. Calibrated on one session:
its resume wrote 164,496 tokens at ratio 1.00 and is caught; a 57,262-token
write at ratio 0.24 is not. Checked 2026-09-23 over the 60 newest sessions
(main-chain requests after the first, cache write >= 1,000): 4,475 fell below
0.3, none between 0.3 and 0.6, 173 at 0.6 or above — a clean gap. Sub-agent
chains have no such gap (21 of 1,740 fell between 0.3 and 0.6), so their
rebuild count is the softer number. A rebuild's CAUSE — cache expiry, resume,
compaction — is not told apart here; each one is a real write either way.

A NUMBER FROM HERE IS A SNAPSHOT. Live sessions keep appending while the scan
runs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _report_table import table as _table
from api_usage_report import usage_tokens

DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"

WEIGHTS = {
    "input_tokens": 1.0,
    "output_tokens": 5.0,
    "cache_read_input_tokens": 0.1,
    "write_5m": 1.25,
    "write_1h": 2.0,
    "write_unsplit": 1.25,
}
WEIGHTS_SOURCE = (
    "price ratios to base input, same on Opus 5 / Sonnet 5 / Haiku 4.5: "
    "wiki brain/claude-core/the-cost-anatomy-of-one-opus-session-2026-09-20 "
    "(claude-core-wiki fc69f96)"
)
REBUILD_RATIO = 0.5
WRITE_KINDS = ("write_1h", "write_5m", "write_unsplit")


# ---------------------------------------------------------------- extraction

def _int(value):
    ok = not isinstance(value, bool) and isinstance(value, (int, float))
    return int(value) if ok else 0


def _request(usage):
    """One request's token fields, with the cache write split by TTL."""
    fields, _ = usage_tokens(usage)
    split = usage.get("cache_creation")
    split = split if isinstance(split, dict) else {}
    w1h = _int(split.get("ephemeral_1h_input_tokens"))
    w5m = _int(split.get("ephemeral_5m_input_tokens"))
    total = fields["cache_creation_input_tokens"]
    if w1h + w5m > total:
        # The split claims more than the total: trust neither half of it.
        w1h, w5m = 0, 0
    fields["write_1h"] = w1h
    fields["write_5m"] = w5m
    fields["write_unsplit"] = total - w1h - w5m
    return fields


def read_chain(path):
    """(requests, stats) for one transcript, deduped by ``message.id``.

    Requests keep the order in which each id first appeared; the fields come
    from the LAST line carrying that id. Lines with no id cannot be deduped and
    are counted in ``stats['no_id']`` rather than summed or dropped silently.
    """
    stats = {"lines": 0, "no_id": 0, "parse_errors": 0, "read_error": None}
    order = []
    latest = {}
    try:
        handle = Path(path).open(encoding="utf-8", errors="replace")
    except OSError as exc:
        stats["read_error"] = str(exc)
        return [], stats
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                stats["parse_errors"] += 1
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            stats["lines"] += 1
            mid = message.get("id")
            if not isinstance(mid, str) or not mid:
                stats["no_id"] += 1
                continue
            if mid not in latest:
                order.append(mid)
            latest[mid] = _request(usage)
    return [latest[mid] for mid in order], stats


# ---------------------------------------------------------------- analysis

def _prompt(req):
    return (req["input_tokens"] + req["cache_read_input_tokens"]
            + req["cache_creation_input_tokens"])


def is_rebuild(req):
    prompt = _prompt(req)
    return prompt > 0 and req["cache_creation_input_tokens"] >= REBUILD_RATIO * prompt


def summarise_chain(requests):
    """Totals, first-request write, and rebuilds after the first request."""
    totals = {k: 0 for k in WEIGHTS}
    totals["cache_creation_input_tokens"] = 0
    for req in requests:
        for key in totals:
            totals[key] += req[key]
    later = [r for r in requests[1:] if is_rebuild(r)]
    return {
        "requests": len(requests),
        "totals": totals,
        "first_write": requests[0]["cache_creation_input_tokens"] if requests else 0,
        "rebuilds": len(later),
        "rebuild_tokens": sum(r["cache_creation_input_tokens"] for r in later),
    }


def weighted(totals):
    """(write units, all units) in base-input-token equivalents."""
    write = sum(totals[k] * WEIGHTS[k] for k in WRITE_KINDS)
    rest = sum(totals[k] * WEIGHTS[k]
               for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))
    return write, write + rest


def session_report(main_path):
    """Everything reported for one session: main chain plus its sub-agents."""
    main_path = Path(main_path)
    requests, stats = read_chain(main_path)
    main = summarise_chain(requests)
    sub_dir = main_path.parent / main_path.stem / "subagents"
    subs = []
    for sub_path in sorted(sub_dir.glob("*.jsonl")) if sub_dir.is_dir() else []:
        sub_requests, sub_stats = read_chain(sub_path)
        for key in ("lines", "no_id", "parse_errors"):
            stats[key] += sub_stats[key]
        if sub_stats["read_error"]:
            stats["sub_read_errors"] = stats.get("sub_read_errors", 0) + 1
        subs.append(summarise_chain(sub_requests))

    totals = dict(main["totals"])
    for sub in subs:
        for key in totals:
            totals[key] += sub["totals"][key]
    write_units, all_units = weighted(totals)
    return {
        "session": main_path.stem,
        "project": main_path.parent.name,
        "main_requests": main["requests"],
        "sub_agents": len(subs),
        "sub_requests": sum(s["requests"] for s in subs),
        "totals": totals,
        "write_share": write_units / all_units if all_units else 0.0,
        "main_first_write": main["first_write"],
        "main_rebuilds": main["rebuilds"],
        "main_rebuild_tokens": main["rebuild_tokens"],
        "sub_prefix_writes": sum(1 for s in subs if s["first_write"]),
        "sub_prefix_tokens": sum(s["first_write"] for s in subs),
        "sub_rebuilds": sum(s["rebuilds"] for s in subs),
        "sub_rebuild_tokens": sum(s["rebuild_tokens"] for s in subs),
        "stats": stats,
    }


def find_sessions(projects_dir, session_prefixes=None, limit=10):
    """Main transcripts, newest first, optionally filtered by id prefix."""
    root = Path(projects_dir)
    mains = [p for p in root.glob("*/*.jsonl") if p.is_file()]
    if session_prefixes:
        mains = [p for p in mains if any(p.stem.startswith(s) for s in session_prefixes)]
    mains.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mains if session_prefixes else mains[:limit]


# ---------------------------------------------------------------- output

def _human(n):
    for unit, size in (("M", 1_000_000), ("k", 1_000)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def format_report(reports):
    if not reports:
        return "No session transcripts found."
    rows = []
    for r in reports:
        t = r["totals"]
        rows.append([
            r["session"][:8],
            f"{r['main_requests']}/{r['sub_requests']}",
            _human(t["cache_creation_input_tokens"]),
            f"{_human(t['write_1h'])}/{_human(t['write_5m'])}/{_human(t['write_unsplit'])}",
            f"{r['write_share'] * 100:.1f}%",
            f"{r['main_rebuilds']} ({_human(r['main_rebuild_tokens'])})",
            f"{r['sub_prefix_writes']} ({_human(r['sub_prefix_tokens'])})",
            f"{r['sub_rebuilds']} ({_human(r['sub_rebuild_tokens'])})",
        ])
    headers = ["session", "reqs main/sub", "cache write", "1h/5m/unsplit",
               "write share", "main rebuilds", "sub prefix writes", "sub rebuilds"]
    lines = [_table(headers, rows), ""]
    no_id = sum(r["stats"]["no_id"] for r in reports)
    unsplit = sum(r["totals"]["write_unsplit"] for r in reports)
    lines.append(f"write share: {WEIGHTS_SOURCE}.")
    lines.append("  unsplit writes weighted at the 5m rate (lower bound): "
                 f"{_human(unsplit)} tokens.")
    lines.append(f"rebuild: cache write >= {REBUILD_RATIO:.0%} of the request's prompt, "
                 "first request of each chain excluded.")
    lines.append(f"deduped by message.id (last line kept); lines with no id, not counted: {no_id}.")
    # An unreadable file and an empty session print the same zero row, so say which.
    bad_main = sum(1 for r in reports if r["stats"]["read_error"])
    bad_sub = sum(r["stats"].get("sub_read_errors", 0) for r in reports)
    if bad_main or bad_sub:
        lines.append(f"unreadable: {bad_main} main transcript(s), {bad_sub} sub-agent "
                     "transcript(s); their rows under-count. See --json for the errors.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prompt-cache totals per session, read from session transcripts.")
    parser.add_argument("--projects", default=str(DEFAULT_PROJECTS))
    parser.add_argument("--session", action="append", default=[],
                        help="session id or prefix; repeatable")
    parser.add_argument("--limit", type=int, default=10,
                        help="newest N sessions when --session is not given")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    paths = find_sessions(args.projects, args.session, args.limit)
    reports = [session_report(p) for p in paths]
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        print(format_report(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
