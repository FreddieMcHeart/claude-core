#!/usr/bin/env python3
"""migrate_to_plugin.py — remove legacy hand-merged cost-discipline hook entries
from settings.json ahead of switching to the native Claude Code plugin.

Only removes hook entries whose exact `command` string matches what the old
settings_merge.py wrote (`<claude_dir>/hooks/cost-discipline.py <mode>`, one of
5 events, exact-string match only). Every other hook entry in settings.json —
downbeat's relay-inbox.py, hand-added user hooks, anything else — is left
untouched, byte-for-byte. A group left with zero hooks after removal is
dropped entirely rather than left as a stray empty entry.

Usage:
    python3 migrate_to_plugin.py --settings ~/.claude/settings.json --claude-dir ~/.claude
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Same table settings_merge.py used to write — copied here since that module is retired.
HOOK_EVENTS = [
    ("PreToolUse",   "pre-tool"),
    ("PostToolUse",  "post-tool"),
    ("SessionStart", "session-start"),
    ("PostCompact",  "post-compact"),
    ("UserPromptSubmit", "user-prompt-submit"),
]


def legacy_command(claude_dir: str, mode: str) -> str:
    return f"{claude_dir}/hooks/cost-discipline.py {mode}"


def _read(settings_path: Path):
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


def migrate(settings_path, claude_dir: str) -> int:
    """Remove legacy cost-discipline hook entries. Returns count removed."""
    settings_path = Path(settings_path)
    data, existed = _read(settings_path)
    if not existed:
        return 0

    hooks = data.get("hooks", {})
    removed = 0

    for event, mode in HOOK_EVENTS:
        cmd = legacy_command(claude_dir, mode)
        groups = hooks.get(event)
        if not groups:
            continue
        new_groups = []
        for grp in groups:
            grp_hooks = grp.get("hooks", [])
            kept = [h for h in grp_hooks if h.get("command") != cmd]
            removed += len(grp_hooks) - len(kept)
            if kept:
                grp["hooks"] = kept
                new_groups.append(grp)
            # else: this group is now empty -> drop it entirely
        if new_groups:
            hooks[event] = new_groups
        elif event in hooks:
            del hooks[event]

    if removed == 0:
        return 0

    bak = settings_path.with_name(f"{settings_path.name}.bak-{int(time.time())}")
    bak.write_bytes(settings_path.read_bytes())
    tmp = settings_path.with_name(f"{settings_path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, settings_path)
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"))
    args = ap.parse_args(argv)

    removed = migrate(Path(args.settings), args.claude_dir)
    if removed == 0:
        print("Nothing to migrate — no legacy cost-discipline hook entries found.")
    else:
        plural = "y" if removed == 1 else "ies"
        print(f"✓ Removed {removed} legacy cost-discipline hook entr{plural} from settings.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
