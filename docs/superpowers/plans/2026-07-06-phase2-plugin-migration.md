# Phase-2 Claude Code Plugin Migration (claude-core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace claude-core's hand-rolled `settings_merge.py` hook-merge with a native Claude Code plugin (`.claude-plugin/hooks.json`), add a migration path for the one real hand-merged install on this machine, and add optional (non-hard-dependency) downbeat-awareness to `doctor.sh` via `claude plugin list --json`.

**Architecture:** A new `.claude-plugin/plugin.json` + `hooks/hooks.json` make `cost-discipline.py` a native plugin hook. A new `lib/migrate_to_plugin.py` (install.sh's `--migrate-to-plugin` flag) exact-string-removes only the legacy hand-merged entries from `settings.json`, leaving every other hook (including downbeat's `relay-inbox.py`) untouched. `settings_merge.py` and its install.sh wiring are then deleted outright. `doctor.sh`'s two hook-related checks are rewritten around a shared `_plugin_enabled()` helper that shells to `claude plugin list --json`, ORed with each check's pre-existing legacy-detection method (never a straight replacement, since that would regress real, currently-working detection).

**Tech Stack:** Bash (install.sh, doctor.sh, bootstrap.sh), Python 3.11+ stdlib only (migrate_to_plugin.py, matching settings_merge.py's no-dependency style), pytest, GitHub Actions (portability.yml).

## Global Constraints

- Every step is additive/backward-compatible until the explicit retirement task (Task 3) — nothing may break for the real adopted instance on this machine mid-plan.
- No third-party Python dependencies anywhere in `lib/` (stdlib only — `json`, `os`, `sys`, `time`, `pathlib`, `argparse`), matching `settings_merge.py`'s existing constraint.
- Exact-string matching only when identifying legacy hook entries to remove — never fuzzy/substring matching (this was flagged as downbeat's own weaker approach in `init_cmd.py`; claude-core's migration must not repeat that mistake).
- `doctor.sh` must never exit non-zero due to a missing `claude` binary or a `claude plugin list` failure — those always degrade to "not detected" on that one signal, never abort the whole health check.
- Neither `plugin.json` (claude-core's or any future downbeat one) declares a `dependencies` relationship on the other — composability is runtime-optional only.

---

### Task 1: Plugin manifest + native hook declarations

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `hooks/hooks.json`
- Test: `tests/test_plugin_manifest.py`

**Interfaces:**
- Produces: `.claude-plugin/plugin.json` (plugin name `claude-core-hooks`, consumed by Task 4's `doctor.sh` detection and Task 6's real `claude plugin install` invocation) and `hooks/hooks.json` (the 4 native hook declarations, consumed by nothing else in this plan — it's the terminal artifact `claude plugin install` reads).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugin_manifest.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_plugin_json_shape():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "claude-core-hooks"
    assert "version" in data
    assert "description" in data

def test_hooks_json_covers_all_four_events():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    assert set(hooks.keys()) == {"PreToolUse", "PostToolUse", "SessionStart", "PostCompact"}

def test_hooks_json_commands_and_matchers_match_legacy_table():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    expected = {
        "PreToolUse": ("Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow", "pre-tool"),
        "PostToolUse": ("Agent|Task|Workflow", "post-tool"),
        "SessionStart": ("startup|resume", "session-start"),
        "PostCompact": (None, "post-compact"),
    }
    for event, (matcher, mode) in expected.items():
        groups = hooks[event]
        assert len(groups) == 1
        grp = groups[0]
        if matcher is None:
            assert "matcher" not in grp
        else:
            assert grp["matcher"] == matcher
        cmd = grp["hooks"][0]["command"]
        assert cmd == f'"${{CLAUDE_PLUGIN_ROOT}}"/hooks/cost-discipline.py {mode}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/claude-core && uv run pytest tests/test_plugin_manifest.py -v`
Expected: FAIL — `FileNotFoundError` on `.claude-plugin/plugin.json` (doesn't exist yet).

- [ ] **Step 3: Create the plugin manifest**

```json
{
  "name": "claude-core-hooks",
  "version": "0.1.0",
  "description": "Cost-discipline hook for Claude Code sessions: delegation reflexes, reader-agent nudges, model-tier awareness."
}
```
Write this to `.claude-plugin/plugin.json` (create the `.claude-plugin/` directory).

- [ ] **Step 4: Create the native hooks declaration**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py pre-tool" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Agent|Task|Workflow",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py post-tool" }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py session-start" }
        ]
      }
    ],
    "PostCompact": [
      {
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py post-compact" }
        ]
      }
    ]
  }
}
```
Write this to `hooks/hooks.json`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/dev/claude-core && uv run pytest tests/test_plugin_manifest.py -v`
Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/claude-core
git add .claude-plugin/plugin.json hooks/hooks.json tests/test_plugin_manifest.py
git commit -m "feat: add native Claude Code plugin manifest + hooks.json for cost-discipline"
```

---

### Task 2: `lib/migrate_to_plugin.py` — remove legacy hand-merged hook entries

**Files:**
- Create: `lib/migrate_to_plugin.py`
- Test: `tests/test_migrate_to_plugin.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `migrate(settings_path: Path, claude_dir: str) -> int` (returns count of removed hook entries), consumed by Task 3's `install.sh --migrate-to-plugin` flag wiring and by Task 6's real adoption run.

This must handle the real, verified shape of `settings.json`'s hook groups — confirmed live on this machine (2026-07-06, `python3 -c "import json; ..."` against the real `~/.claude/settings.json`):
- A group can hold **multiple** hook entries under one matcher (e.g. `SessionStart`'s `startup|resume` group currently holds `obsidian-hot-cache-inject.sh`, `cost-discipline.py session-start`, AND downbeat's `relay-inbox.py` — all three in the same group). Only the `cost-discipline.py` entry may ever be removed; the other two must survive untouched.
- A group can hold **exactly one** hook entry that IS the legacy one (e.g. `PreToolUse`'s big matcher group, `PostToolUse`'s `Agent|Task|Workflow` group) — removing it must drop the now-empty group entirely, not leave a stray `{"matcher": "...", "hooks": []}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate_to_plugin.py
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "migrate_to_plugin.py"
spec = importlib.util.spec_from_file_location("migrate_to_plugin", MOD)
mtp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtp)

CD = "/home/u/.claude"


def _legacy(mode):
    return f"{CD}/hooks/cost-discipline.py {mode}"


def _write(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    return p


def test_removes_solo_group_entirely(tmp_path):
    """A group containing ONLY the legacy hook is dropped, not left as hooks: []."""
    data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
                    "hooks": [{"type": "command", "command": _legacy("pre-tool")}],
                }
            ]
        }
    }
    p = _write(tmp_path, data)
    removed = mtp.migrate(p, CD)
    assert removed == 1
    result = json.loads(p.read_text())
    assert "PreToolUse" not in result["hooks"]


def test_removes_only_legacy_entry_from_mixed_group(tmp_path):
    """A group with the legacy hook PLUS other hooks (downbeat's relay-inbox.py,
    a hand-added user hook) keeps everything except the legacy entry."""
    data = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume",
                    "hooks": [
                        {"type": "command", "command": "/x/.claude/scripts/obsidian-hot-cache-inject.sh SessionStart"},
                        {"type": "command", "command": _legacy("session-start")},
                        {"type": "command", "command": "/x/.claude/hooks/relay-inbox.py"},
                    ],
                }
            ]
        }
    }
    p = _write(tmp_path, data)
    removed = mtp.migrate(p, CD)
    assert removed == 1
    result = json.loads(p.read_text())
    remaining_cmds = {h["command"] for h in result["hooks"]["SessionStart"][0]["hooks"]}
    assert remaining_cmds == {
        "/x/.claude/scripts/obsidian-hot-cache-inject.sh SessionStart",
        "/x/.claude/hooks/relay-inbox.py",
    }


def test_removes_all_four_events(tmp_path):
    data = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
                             "hooks": [{"type": "command", "command": _legacy("pre-tool")}]}],
            "PostToolUse": [{"matcher": "Agent|Task|Workflow",
                              "hooks": [{"type": "command", "command": _legacy("post-tool")}]}],
            "SessionStart": [{"matcher": "startup|resume",
                               "hooks": [{"type": "command", "command": _legacy("session-start")}]}],
            "PostCompact": [{"hooks": [{"type": "command", "command": _legacy("post-compact")}]}],
        }
    }
    p = _write(tmp_path, data)
    removed = mtp.migrate(p, CD)
    assert removed == 4
    result = json.loads(p.read_text())
    assert result["hooks"] == {}


def test_nothing_to_migrate_returns_zero_and_does_not_write(tmp_path):
    data = {"hooks": {"PostToolUse": [{"matcher": "Bash",
                       "hooks": [{"type": "command", "command": "/x/.claude/hooks/relay-poll-offer.py"}]}]}}
    p = _write(tmp_path, data)
    before = p.read_text()
    removed = mtp.migrate(p, CD)
    assert removed == 0
    assert p.read_text() == before  # untouched, no backup, no rewrite


def test_creates_backup_only_when_something_removed(tmp_path):
    data = {"hooks": {"PostCompact": [{"hooks": [{"type": "command", "command": _legacy("post-compact")}]}]}}
    p = _write(tmp_path, data)
    mtp.migrate(p, CD)
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1


def test_malformed_json_backs_up_and_exits_2(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not valid json")
    try:
        mtp.migrate(p, CD)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 2
    malformed = list(tmp_path.glob("settings.json.malformed-*"))
    assert len(malformed) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/claude-core && uv run pytest tests/test_migrate_to_plugin.py -v`
Expected: FAIL — `lib/migrate_to_plugin.py` doesn't exist (`ModuleNotFoundError`/spec load error).

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""migrate_to_plugin.py — remove legacy hand-merged cost-discipline hook entries
from settings.json ahead of switching to the native Claude Code plugin.

Only removes hook entries whose exact `command` string matches what the old
settings_merge.py wrote (`<claude_dir>/hooks/cost-discipline.py <mode>`, one of
4 events, exact-string match only). Every other hook entry in settings.json —
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/claude-core && uv run pytest tests/test_migrate_to_plugin.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/dev/claude-core
git add lib/migrate_to_plugin.py tests/test_migrate_to_plugin.py
git commit -m "feat: add migrate_to_plugin.py to remove legacy hand-merged cost-discipline hooks"
```

---

### Task 3: Retire the hand-merge path (install.sh, bootstrap.sh, settings_merge.py, smoke test)

**Files:**
- Modify: `install.sh` (add `--migrate-to-plugin` flag; delete step (f) hook-merge; delete `settings_merge.py` copy step (c2))
- Modify: `bootstrap.sh` (delete the `cost-discipline.py` hook-symlink loop)
- Delete: `lib/settings_merge.py`
- Delete: `tests/test_settings_merge.py`
- Modify: `tests/smoke_install.sh`
- Test: `tests/test_migrate_to_plugin.sh` (new, end-to-end shell integration test)

**Interfaces:**
- Consumes: `lib/migrate_to_plugin.py`'s `main()` CLI entrypoint from Task 2 (invoked as a subprocess, not imported).
- Produces: nothing new consumed by later tasks — this is where the old path is torn out.

- [ ] **Step 1: Add the `--migrate-to-plugin` flag to `install.sh`**

In `install.sh`, change the flag-parsing block (currently lines 15-25):

```bash
WITH_RELAY=0
WIKI_URL_OVERRIDE=""
MIGRATE_TO_PLUGIN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --doctor)             exec "$CORE_DIR/doctor.sh" ;;
        --with-relay)         WITH_RELAY=1 ;;
        --wiki-url)           WIKI_URL_OVERRIDE="${2:?--wiki-url needs a value}"; shift ;;
        --migrate-to-plugin)  MIGRATE_TO_PLUGIN=1 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done
```

Right after the preflight block (currently lines 27-32, after the `uv` check), add the migration short-circuit — before the `"=== claude-core install ==="` banner:

```bash
if [ "$MIGRATE_TO_PLUGIN" -eq 1 ]; then
    python3 "$CORE_DIR/lib/migrate_to_plugin.py" \
        --settings "$CLAUDE_DIR/settings.json" \
        --claude-dir "$CLAUDE_DIR" \
        || { echo "FATAL: migration failed (malformed settings.json?)" >&2; exit 1; }
    echo "→ Now run: claude plugin install $CORE_DIR/.claude-plugin"
    exit 0
fi
```

- [ ] **Step 2: Delete step (f) and step (c2) from `install.sh`**

Delete this block entirely (the old hook-merge step, was step f):
```bash
# ── f. Wire cost-discipline hook into settings.json (idempotent) ─────────────
echo "→ Merging cost-discipline hook into settings.json..."
python3 "$CORE_DIR/lib/settings_merge.py" \
    --settings "$CLAUDE_DIR/settings.json" \
    --claude-dir "$CLAUDE_DIR" \
    || { echo "FATAL: settings.json merge failed (malformed JSON?)" >&2; exit 1; }
```

Delete this block entirely (was step c2):
```bash
# c2. settings_merge.py — copy to ~/.claude/lib/ if absent
MERGE_SRC="$CORE_DIR/lib/settings_merge.py"
MERGE_DST="$CLAUDE_DIR/lib/settings_merge.py"
if [ -f "$MERGE_DST" ]; then
    echo "✓ $MERGE_DST already present — leaving (no clobber)"
else
    mkdir -p "$CLAUDE_DIR/lib"
    cp "$MERGE_SRC" "$MERGE_DST"
    echo "✓ Installed settings_merge.py → $MERGE_DST"
fi
```

Everything else in `install.sh` (skill symlinks, `platform.config.toml` copy, `config_loader.py` copy, `CLAUDE.md` symlink, wiki submodule step, `--with-relay` opt-in, the final `exec doctor.sh`) is unchanged.

- [ ] **Step 3: Remove the hook-symlink loop from `bootstrap.sh`**

Delete this block from `bootstrap.sh`:
```bash
CLAUDE_HOOKS="${CLAUDE_HOOKS:-$HOME/.claude/hooks}"
mkdir -p "$CLAUDE_HOOKS"
for h in cost-discipline.py; do
  target="$CORE_DIR/hooks/$h"; link="$CLAUDE_HOOKS/$h"
  [ -e "$target" ] || { echo "missing $target"; exit 1; }
  [ -L "$link" ] && rm "$link"
  [ -e "$link" ] && { echo "refusing to overwrite real file $link"; exit 1; }
  ln -s "$target" "$link"; echo "linked hook $h"
done
```

Update the final `echo` line in `bootstrap.sh` from:
```bash
echo "done. Next: (1) wire the hook into ~/.claude/settings.json (PreToolUse/PostToolUse/SessionStart/PostCompact); (2) add claude-core-wiki as a docs/core submodule; (3) run 'downbeat init' for relay."
```
to:
```bash
echo "done. Next: (1) run 'claude plugin install <this-repo>/.claude-plugin' to register cost-discipline hooks; (2) add claude-core-wiki as a docs/core submodule; (3) run 'downbeat init' for relay."
```

The skill-symlink loop above it (`models-router`, `delegation-discipline`, `claude-cost-audit`) is untouched.

- [ ] **Step 4: Delete the old settings-merge files**

```bash
cd ~/dev/claude-core
git rm lib/settings_merge.py tests/test_settings_merge.py
```

- [ ] **Step 5: Write the new end-to-end migration smoke test**

```bash
#!/usr/bin/env bash
# tests/test_migrate_to_plugin.sh — end-to-end: fresh isolated $HOME, hand-seed a
# realistic mixed settings.json (legacy cost-discipline entries interleaved with
# downbeat's relay-inbox.py and other unrelated hooks), run
# `install.sh --migrate-to-plugin`, assert only the legacy entries are gone.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -R "$SRC/." "$TMP/core"
CORE="$TMP/core"
HOME_DIR="$TMP/home"
CLAUDE_DIR="$HOME_DIR/.claude"
mkdir -p "$CLAUDE_DIR"

python3 - "$CLAUDE_DIR/settings.json" "$CLAUDE_DIR" <<'PYEOF'
import json, sys
path, claude_dir = sys.argv[1], sys.argv[2]
cd_cmd = lambda mode: f"{claude_dir}/hooks/cost-discipline.py {mode}"
data = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
             "hooks": [{"type": "command", "command": cd_cmd("pre-tool")}]},
        ],
        "SessionStart": [
            {"matcher": "startup|resume",
             "hooks": [
                 {"type": "command", "command": f"{claude_dir}/scripts/obsidian-hot-cache-inject.sh SessionStart"},
                 {"type": "command", "command": cd_cmd("session-start")},
                 {"type": "command", "command": f"{claude_dir}/hooks/relay-inbox.py"},
             ]},
        ],
        "PostCompact": [
            {"hooks": [{"type": "command", "command": cd_cmd("post-compact")}]},
        ],
    }
}
open(path, "w").write(json.dumps(data, indent=2))
PYEOF

FAIL=0
ck() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAIL=1; fi; }

echo "=== migrate ==="
HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/install.sh" --migrate-to-plugin

ck "PreToolUse group dropped entirely" \
   'python3 -c "import json; d=json.load(open(\"'"$CLAUDE_DIR"'/settings.json\")); exit(0 if \"PreToolUse\" not in d[\"hooks\"] else 1)"'
ck "SessionStart keeps obsidian + relay-inbox, drops cost-discipline" \
   'python3 -c "
import json
d = json.load(open(\"'"$CLAUDE_DIR"'/settings.json\"))
cmds = {h[\"command\"] for h in d[\"hooks\"][\"SessionStart\"][0][\"hooks\"]}
assert \"'"$CLAUDE_DIR"'/hooks/relay-inbox.py\" in cmds
assert \"'"$CLAUDE_DIR"'/scripts/obsidian-hot-cache-inject.sh SessionStart\" in cmds
assert not any(\"cost-discipline.py\" in c for c in cmds)
"'
ck "PostCompact group dropped entirely" \
   'python3 -c "import json; d=json.load(open(\"'"$CLAUDE_DIR"'/settings.json\")); exit(0 if \"PostCompact\" not in d[\"hooks\"] else 1)"'
ck "exactly one backup file" \
   '[ "$(ls "$CLAUDE_DIR"/settings.json.bak-* 2>/dev/null | wc -l | tr -d " ")" = "1" ]'

echo "=== idempotency: re-run migration ==="
OUT="$(HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/install.sh" --migrate-to-plugin 2>&1)"
ck "second run reports nothing to migrate" 'echo "$OUT" | grep -q "Nothing to migrate"'
ck "still exactly one backup file after re-run" \
   '[ "$(ls "$CLAUDE_DIR"/settings.json.bak-* 2>/dev/null | wc -l | tr -d " ")" = "1" ]'

echo ""; [ "$FAIL" -eq 0 ] && echo "MIGRATE SMOKE: all pass" || echo "MIGRATE SMOKE: FAILURES"; exit "$FAIL"
```

Make it executable: `chmod +x tests/test_migrate_to_plugin.sh`

- [ ] **Step 6: Update `tests/smoke_install.sh`**

Remove this assertion (fresh install no longer writes any hooks at all):
```bash
ck "settings has 4 events" 'python3 -c "import json;h=json.load(open(\"$CLAUDE_DIR/settings.json\"))[\"hooks\"];exit(0 if all(e in h for e in [\"PreToolUse\",\"PostToolUse\",\"SessionStart\",\"PostCompact\"]) else 1)"'
```

Replace it with:
```bash
ck "fresh install writes no hooks" '[ ! -f "$CLAUDE_DIR/settings.json" ] || python3 -c "import json,sys; d=json.load(open(\"$CLAUDE_DIR/settings.json\")); sys.exit(1 if d.get(\"hooks\") else 0)"'
```

Also remove the now-dead "hook paths rooted at CLAUDE_DIR" assertion block (lines 39-46 of the current file) — there are no hooks written by a fresh `install.sh` run anymore for it to check, since hook wiring is now the plugin's job, not `install.sh`'s.

- [ ] **Step 7: Run all affected tests**

Run each in sequence from `~/dev/claude-core`:

```bash
uv run pytest tests/ -v
```
Expected: no `test_settings_merge` collection (file deleted) and all remaining tests pass.

```bash
FAKE_WIKI="/tmp/fake-wiki-$(date +%s)"
mkdir -p "$FAKE_WIKI"
git -C "$FAKE_WIKI" init -q
git -C "$FAKE_WIKI" commit --allow-empty -q -m init
WIKI_URL="file://$FAKE_WIKI" bash tests/smoke_install.sh
```
Expected: `SMOKE: all pass`.

```bash
bash tests/test_migrate_to_plugin.sh
```
Expected: `MIGRATE SMOKE: all pass`.

- [ ] **Step 8: Commit**

```bash
cd ~/dev/claude-core
git add install.sh bootstrap.sh tests/smoke_install.sh tests/test_migrate_to_plugin.sh
git commit -m "feat: retire settings_merge.py hand-merge, add --migrate-to-plugin flag to install.sh"
```

---

### Task 4: `doctor.sh` — shared plugin-detection helper + both checks rewritten

**Files:**
- Modify: `doctor.sh`
- Test: `tests/test_doctor_plugin_detection.sh`

**Interfaces:**
- Consumes: Task 1's plugin name `claude-core-hooks` (check #5's target); today's existing `command -v downbeat` behavior (check #7, preserved as one side of an OR, per the spec correction that a straight replacement would regress current detection).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the shared `_plugin_enabled()` helper to `doctor.sh`**

Add this function near the top of `doctor.sh`, right after the `_pass`/`_warn` helper definitions (currently lines 12-13):

```bash
# Returns 0 if a Claude Code plugin named "$1" is installed and enabled, 1 otherwise.
# Fails safe: any missing `claude` binary, subprocess error, or bad JSON -> 1 (not
# detected), never aborts doctor.sh itself.
_plugin_enabled() {
    local name="$1"
    command -v claude >/dev/null 2>&1 || return 1
    claude plugin list --json 2>/dev/null | python3 -c "
import json, sys
name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for p in data:
    pid = p.get('id', '')
    if pid.split('@')[0] == name and p.get('enabled'):
        sys.exit(0)
sys.exit(1)
" "$name"
}
```

- [ ] **Step 2: Rewrite check #5 (hook:cost-discipline registered)**

Replace this block:
```bash
# ── 5. cost-discipline hook registered in settings.json ──────────────────────
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$CLAUDE_DIR/lib/settings_merge.py" ]; then
    if python3 "$CLAUDE_DIR/lib/settings_merge.py" --settings "$CLAUDE_DIR/settings.json" --claude-dir "$CLAUDE_DIR" --check >/dev/null 2>&1; then
        _pass "hook:cost-discipline registered"
    else
        _warn "hook:cost-discipline" "not fully registered in settings.json — run ./install.sh"
    fi
else
    _warn "hook:cost-discipline" "settings_merge.py absent — run ./install.sh"
fi
```

with:
```bash
# ── 5. cost-discipline hook registered — via new plugin OR legacy hand-merge ──
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
_legacy_cost_discipline_present() {
    [ -f "$CLAUDE_DIR/settings.json" ] || return 1
    python3 -c "
import json, sys
try:
    d = json.load(open('$CLAUDE_DIR/settings.json'))
except Exception:
    sys.exit(1)
target = '$CLAUDE_DIR/hooks/cost-discipline.py'
for grp_list in d.get('hooks', {}).values():
    for grp in grp_list:
        for h in grp.get('hooks', []):
            if h.get('command', '').startswith(target):
                sys.exit(0)
sys.exit(1)
"
}
if _plugin_enabled "claude-core-hooks"; then
    _pass "hook:cost-discipline registered (claude-core-hooks plugin)"
elif _legacy_cost_discipline_present; then
    _pass "hook:cost-discipline registered (legacy hand-merge — consider: ./install.sh --migrate-to-plugin && claude plugin install $CORE_DIR/.claude-plugin)"
else
    _warn "hook:cost-discipline" "not registered — run: claude plugin install $CORE_DIR/.claude-plugin"
fi
```

- [ ] **Step 3: Rewrite check #7 (relay hooks)**

Replace this block:
```bash
# ── 7. relay hooks (only if relay is installed) ──────────────────────────────
if command -v downbeat >/dev/null 2>&1; then
    if grep -q "relay-inbox.py" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
        _pass "relay:hooks registered"
    else
        _warn "relay:hooks" "downbeat installed but hooks not in settings.json — run 'downbeat init'"
    fi
fi
```

with:
```bash
# ── 7. relay hooks (only if downbeat is installed — CLI today, plugin someday) ─
if command -v downbeat >/dev/null 2>&1 || _plugin_enabled "downbeat"; then
    if grep -q "relay-inbox.py" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
        _pass "relay:hooks registered"
    else
        _warn "relay:hooks" "downbeat installed but hooks not in settings.json — run 'downbeat init'"
    fi
fi
```

- [ ] **Step 4: Write the test**

```bash
#!/usr/bin/env bash
# tests/test_doctor_plugin_detection.sh — fake a `claude` binary on PATH that
# echoes a canned `plugin list --json` payload, run doctor.sh against it, assert
# checks #5 and #7 react correctly to plugin presence/absence, and that a
# missing `claude` binary never crashes doctor.sh.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -R "$SRC/." "$TMP/core"
CORE="$TMP/core"
HOME_DIR="$TMP/home"
CLAUDE_DIR="$HOME_DIR/.claude"
mkdir -p "$CLAUDE_DIR" "$TMP/fakebin"

FAIL=0
ck() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAIL=1; fi; }

echo "=== case 1: claude-core-hooks plugin enabled, no legacy entry, no downbeat ==="
cat > "$TMP/fakebin/claude" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2" = "plugin list" ]; then
  echo '[{"id":"claude-core-hooks@local","enabled":true},{"id":"other-plugin@x","enabled":true}]'
fi
EOF
chmod +x "$TMP/fakebin/claude"
echo '{"hooks":{}}' > "$CLAUDE_DIR/settings.json"
OUT="$(PATH="$TMP/fakebin:$PATH" HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/doctor.sh" 2>&1)"
ck "check5 passes via plugin" 'echo "$OUT" | grep -q "PASS  hook:cost-discipline registered (claude-core-hooks plugin)"'
ck "check7 silent (downbeat absent)" '! echo "$OUT" | grep -q "relay:hooks"'

echo "=== case 2: no plugin, no downbeat binary, no legacy entry -> WARN ==="
cat > "$TMP/fakebin/claude" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2" = "plugin list" ]; then
  echo '[]'
fi
EOF
OUT="$(PATH="$TMP/fakebin:$PATH" HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/doctor.sh" 2>&1)"
ck "check5 warns when nothing registered" 'echo "$OUT" | grep -q "WARN  hook:cost-discipline"'

echo "=== case 3: no claude binary at all -> doctor.sh does not crash ==="
OUT="$(PATH="/usr/bin:/bin" HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/doctor.sh" 2>&1)"
ck "doctor.sh still produces a Summary line without claude on PATH" 'echo "$OUT" | grep -q "^Summary:"'

echo ""; [ "$FAIL" -eq 0 ] && echo "DOCTOR SMOKE: all pass" || echo "DOCTOR SMOKE: FAILURES"; exit "$FAIL"
```

Make it executable: `chmod +x tests/test_doctor_plugin_detection.sh`

- [ ] **Step 5: Run the test**

Run: `cd ~/dev/claude-core && bash tests/test_doctor_plugin_detection.sh`
Expected: `DOCTOR SMOKE: all pass`

- [ ] **Step 6: Commit**

```bash
cd ~/dev/claude-core
git add doctor.sh tests/test_doctor_plugin_detection.sh
git commit -m "feat: doctor.sh detects claude-core-hooks/downbeat via claude plugin list --json (ORed with legacy checks)"
```

---

### Task 5: Wire new tests into `portability.yml` CI

**Files:**
- Modify: `.github/workflows/portability.yml`

**Interfaces:**
- Consumes: `tests/test_migrate_to_plugin.sh` (Task 3), `tests/test_doctor_plugin_detection.sh` (Task 4), `tests/test_plugin_manifest.py` and `tests/test_migrate_to_plugin.py` (Tasks 1-2, already picked up by the existing `pytest` step since they live under `tests/`).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the two new shell scripts to the CI run steps**

In `.github/workflows/portability.yml`, find the step that runs `tests/smoke_install.sh` and add two new steps immediately after it (same job, same matrix, so they run on both `ubuntu-latest`/`macos-latest` × `3.11`/`3.13`):

```yaml
      - name: Run migrate-to-plugin smoke test
        run: bash tests/test_migrate_to_plugin.sh
      - name: Run doctor.sh plugin-detection smoke test
        run: bash tests/test_doctor_plugin_detection.sh
```

Note: `test_doctor_plugin_detection.sh` fakes `claude` on `PATH` itself — it does not require a real Claude Code install in the CI runner, so no other CI setup changes are needed.

- [ ] **Step 2: Verify locally what CI will run**

Run: `cd ~/dev/claude-core && bash tests/smoke_install.sh && bash tests/test_migrate_to_plugin.sh && bash tests/test_doctor_plugin_detection.sh && uv run pytest tests/ -v && uv run ruff check .`
Expected: every command exits 0; `ruff check .` reports no issues (delete of `settings_merge.py`/`test_settings_merge.py` must not leave dangling imports elsewhere — confirm with a repo-wide grep: `grep -rn "settings_merge" --include="*.py" --include="*.sh" .` returns nothing outside `docs/`).

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-core
git add .github/workflows/portability.yml
git commit -m "ci: run migrate-to-plugin and doctor plugin-detection smoke tests in portability.yml"
```

- [ ] **Step 4: Push and confirm CI is green**

```bash
git push origin main
gh run watch --repo FreddieMcHeart/claude-core $(gh run list --repo FreddieMcHeart/claude-core --limit 1 --json databaseId --jq '.[0].databaseId')
```
Expected: all matrix legs (`ubuntu-latest`/`macos-latest` × `3.11`/`3.13`) complete with conclusion `success`.

(Adjust `FreddieMcHeart/claude-core` if the actual remote differs — check with `git remote get-url origin` first if unsure.)

---

### Task 6: Real adoption on this machine

**Files:** none (operational task, no code changes — verification only).

**Interfaces:** Consumes everything from Tasks 1-5, already merged to `main` and CI-green.

- [ ] **Step 1: Snapshot current state for comparison**

```bash
cp ~/.claude/settings.json /tmp/settings.before-migration.json
```

- [ ] **Step 2: Run the migration**

```bash
cd ~/dev/claude-core
./install.sh --migrate-to-plugin
```
Expected output: `✓ Removed 4 legacy cost-discipline hook entr(y|ies) from settings.json` (this machine has all 4 events hand-merged, confirmed in this planning session) followed by `→ Now run: claude plugin install ~/dev/claude-core/.claude-plugin`.

- [ ] **Step 3: Diff before/after to confirm selective removal**

```bash
diff /tmp/settings.before-migration.json ~/.claude/settings.json
```
Expected: only the 4 `cost-discipline.py` hook entries (and any now-empty groups) are gone; every other hook (`relay-inbox.py`, `relay-poll-offer.py`, `atlantis-poll-check.sh`, `auth-cmd-rewrite.py`, `obsidian-hot-cache-inject.sh`, the Supacode-managed entries) is present and byte-identical to before.

- [ ] **Step 4: Install the plugin**

```bash
claude plugin install ~/dev/claude-core/.claude-plugin
```

- [ ] **Step 5: Confirm no double-firing**

Start a fresh Claude Code session in any directory and run one throwaway tool call (e.g. `ls`). Confirm the cost-discipline `SessionStart` banner appears exactly once (not duplicated) and no error is printed about the hook.

- [ ] **Step 6: Run doctor.sh and confirm the new plugin-aware checks pass**

```bash
cd ~/dev/claude-core && ./doctor.sh
```
Expected: `PASS  hook:cost-discipline registered (claude-core-hooks plugin)` and (since `downbeat` CLI is present on this machine) `PASS  relay:hooks registered`.

- [ ] **Step 7: Update the roadmap**

Append a `SHIPPED` entry to `~/mama/harness-portability-roadmap-2026-06-30.md` recording: plugin adopted on this machine, `settings_merge.py` retired, both `doctor.sh` checks confirmed passing, and that this plan's outcome (not the spec alone) is what gets forwarded to `Claude-Cost-Optimazing-child` as the concrete reference implementation.

---

## Self-Review Notes (already applied above, recorded for the record)

- **Spec coverage:** Task 1 covers "Architecture" (plugin manifest + hooks.json). Task 2 covers "Migration mechanism" bullets 1-6. Task 3 covers "install.sh's normal run", `settings_merge.py` deletion, `bootstrap.sh`'s hook-symlink removal, and the `tests/smoke_install.sh` changes from the spec's Testing section. Task 4 covers both corrected `doctor.sh` checks (the OR-condition fixes from 2026-07-06). Task 5 covers the `portability.yml` CI section. Task 6 covers "Manual validation (this machine)" and "Rollout order" step 1.
- **Placeholder scan:** no TBD/TODO; the one item the spec explicitly deferred (doctor.sh's exact plugin-detection mechanism) was resolved via direct live inspection during planning (`claude plugin list --json` verified working, exact `id`/`enabled` shape captured) rather than left open.
- **Type/name consistency:** `migrate()`'s signature (`settings_path, claude_dir: str) -> int`) is used identically in Task 2's tests, Task 3's `install.sh` wiring (via CLI, not import), and Task 6's manual run. Plugin name `claude-core-hooks` is consistent across Task 1's `plugin.json`, Task 4's `doctor.sh` check #5, and Task 6's verification step.
