#!/usr/bin/env python3
"""settings_merge.py — idempotently merge the cost-discipline hook into settings.json.

Self-contained (no relay dependency). Registers the cost-discipline.py command under four
hook events, skipping any already present. Malformed settings.json -> timestamped backup +
exit 2, original untouched. Atomic write; a .bak-<ts> is made only when a write happens.

Usage:
    python3 settings_merge.py --settings ~/.claude/settings.json --claude-dir ~/.claude
    python3 settings_merge.py --settings ... --claude-dir ... --check   # dry-run, exit 0/1
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# (event, matcher, mode-arg). matcher "" => group has no "matcher" key.
# Matchers below reproduce the owner's live settings.json shape (Task 2 Step 1):
#   PreToolUse:   matcher "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow"
#   PostToolUse:  matcher "Agent|Task|Workflow"
#   SessionStart: matcher "startup|resume"
#   PostCompact:  no matcher key at all
HOOK_EVENTS = [
    ("PreToolUse",   "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow", "pre-tool"),
    ("PostToolUse",  "Agent|Task|Workflow", "post-tool"),
    ("SessionStart", "startup|resume", "session-start"),
    ("PostCompact",  "", "post-compact"),
]


def hook_command(claude_dir: str, mode: str) -> str:
    # Must match the owner's live command format exactly (Task 2 Step 1):
    # "<claude_dir>/hooks/cost-discipline.py <mode>" — absolute path, no "python3 " prefix.
    return f"{claude_dir}/hooks/cost-discipline.py {mode}"


def _group(matcher: str, command: str) -> dict:
    grp = {"hooks": [{"type": "command", "command": command}]}
    return {"matcher": matcher, **grp} if matcher else grp


def _already_registered(event_list: list, command: str) -> bool:
    for grp in event_list:
        for h in grp.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def _would_change(data: dict, claude_dir: str) -> bool:
    hooks = data.get("hooks", {})
    for event, _matcher, mode in HOOK_EVENTS:
        if not _already_registered(hooks.get(event, []), hook_command(claude_dir, mode)):
            return True
    return False


def _read(settings_path: Path):
    """Return (data, existed). Malformed -> backup + SystemExit(2)."""
    if not settings_path.exists():
        return {}, False
    try:
        return json.loads(settings_path.read_text()), True
    except json.JSONDecodeError:
        bak = settings_path.with_name(f"{settings_path.name}.malformed-{int(time.time())}")
        bak.write_bytes(settings_path.read_bytes())
        print(f"ERROR: {settings_path} is not valid JSON. Backed up to {bak}. "
              f"Aborting without modifying it.", file=sys.stderr)
        raise SystemExit(2)


def merge(settings_path: Path, claude_dir: str) -> str:
    settings_path = Path(settings_path)
    data, existed = _read(settings_path)

    hooks = data.setdefault("hooks", {})
    changed = False
    for event, matcher, mode in HOOK_EVENTS:
        cmd = hook_command(claude_dir, mode)
        lst = hooks.setdefault(event, [])
        if not _already_registered(lst, cmd):
            lst.append(_group(matcher, cmd))
            changed = True

    if not changed:
        return "unchanged"

    if existed:
        bak = settings_path.with_name(f"{settings_path.name}.bak-{int(time.time())}")
        bak.write_bytes(settings_path.read_bytes())
    tmp = settings_path.with_name(f"{settings_path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, settings_path)
    return "created" if not existed else "updated"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"))
    ap.add_argument("--check", action="store_true",
                    help="dry-run: exit 0 if fully registered, 1 if a merge is needed")
    args = ap.parse_args(argv)
    settings_path = Path(args.settings)

    if args.check:
        data, _ = _read(settings_path)   # malformed still exits 2
        return 1 if _would_change(data, args.claude_dir) else 0

    result = merge(settings_path, args.claude_dir)
    print(f"settings_merge: {result} ({settings_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
