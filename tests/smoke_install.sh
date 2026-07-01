#!/usr/bin/env bash
# Fresh-$HOME end-to-end smoke test for claude-platform install (no relay).
#
# Runs install.sh from an isolated COPY of this repo against an isolated
# $HOME, so it can never mutate the real ~/dev/claude-core (no submodule
# add/deinit against the real repo). Cleanup is just deleting the temp dir.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
# CI always sets WIKI_URL (a file:// throwaway). A bare local run must set it
# explicitly — no personal URL is baked in (this repo is public).
WIKI_URL="${WIKI_URL:?set WIKI_URL to a wiki repo URL (CI uses a file:// throwaway; see docs/superpowers/specs)}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -R "$SRC/." "$TMP/core"
CORE="$TMP/core"
HOME_DIR="$TMP/home"
CLAUDE_DIR="$HOME_DIR/.claude"

# git >=2.38 blocks the file:// protocol for submodule clones (CVE-2022-39253).
# The CI/test wiki is a local file:// throwaway, so permit it — scoped to this
# test's isolated HOME only, never the real machine. Harmless for real ssh:// wikis.
mkdir -p "$HOME_DIR"
HOME="$HOME_DIR" git config --global protocol.file.allow always

FAIL=0
ck() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAIL=1; fi; }

echo "=== run 1 (fresh install) ==="
HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/install.sh" --wiki-url "$WIKI_URL" >/dev/null 2>&1 || true

ck "skills symlinked"      '[ -L "$CLAUDE_DIR/skills/models-router" ]'
ck "hook symlinked"        '[ -L "$CLAUDE_DIR/hooks/cost-discipline.py" ]'
ck "config copied"         '[ -f "$CLAUDE_DIR/platform.config.toml" ]'
ck "settings has 4 events" 'python3 -c "import json;h=json.load(open(\"$CLAUDE_DIR/settings.json\"))[\"hooks\"];exit(0 if all(e in h for e in [\"PreToolUse\",\"PostToolUse\",\"SessionStart\",\"PostCompact\"]) else 1)"'
ck "wiki submodule present" '[ -n "$(ls -A "$CORE/docs/core" 2>/dev/null)" ]'
ck "hook paths rooted at CLAUDE_DIR" 'CLAUDE_DIR="$CLAUDE_DIR" python3 -c "
import json, os, sys
cd = os.environ[\"CLAUDE_DIR\"]
d = json.load(open(os.path.join(cd, \"settings.json\")))
cmds = [h.get(\"command\",\"\") for grp in d.get(\"hooks\",{}).values() for g in grp for h in g.get(\"hooks\",[])]
cd_cmds = [c for c in cmds if \"cost-discipline\" in c]
sys.exit(0 if cd_cmds and all(c.split()[0].startswith(cd) for c in cd_cmds) else 1)
"'

echo "=== run 2 (idempotency) ==="
cp "$CLAUDE_DIR/settings.json" "$TMP/settings.before"
HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/install.sh" --wiki-url "$WIKI_URL" >/dev/null 2>&1 || true
ck "settings unchanged on re-run" 'diff -q "$TMP/settings.before" "$CLAUDE_DIR/settings.json" >/dev/null'
ck "no stray settings backup"     '[ -z "$(ls "$CLAUDE_DIR/"settings.json.bak-* 2>/dev/null)" ]'

echo ""; [ "$FAIL" -eq 0 ] && echo "SMOKE: all pass" || echo "SMOKE: FAILURES"; exit "$FAIL"
