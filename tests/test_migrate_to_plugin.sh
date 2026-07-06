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
