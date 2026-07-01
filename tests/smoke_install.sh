#!/usr/bin/env bash
# Fresh-$HOME end-to-end smoke test for claude-platform install (no relay).
#
# Runs install.sh from an isolated COPY of this repo against an isolated
# $HOME, so it can never mutate the real ~/dev/claude-core (no submodule
# add/deinit against the real repo). Cleanup is just deleting the temp dir.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
WIKI_URL="${WIKI_URL:-git@github.com:FreddieMcHeart/claude-core-wiki.git}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -R "$SRC/." "$TMP/core"
CORE="$TMP/core"
HOME_DIR="$TMP/home"
CLAUDE_DIR="$HOME_DIR/.claude"

FAIL=0
ck() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAIL=1; fi; }

echo "=== run 1 (fresh install) ==="
HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/install.sh" --wiki-url "$WIKI_URL" >/dev/null 2>&1 || true

ck "skills symlinked"      '[ -L "$CLAUDE_DIR/skills/models-router" ]'
ck "hook symlinked"        '[ -L "$CLAUDE_DIR/hooks/cost-discipline.py" ]'
ck "config copied"         '[ -f "$CLAUDE_DIR/platform.config.toml" ]'
ck "settings has 4 events" 'python3 -c "import json;h=json.load(open(\"$CLAUDE_DIR/settings.json\"))[\"hooks\"];exit(0 if all(e in h for e in [\"PreToolUse\",\"PostToolUse\",\"SessionStart\",\"PostCompact\"]) else 1)"'
ck "wiki submodule present" '[ -n "$(ls -A "$CORE/docs/core" 2>/dev/null)" ]'
ck "no host /Users path in settings" '! grep -q "/Users/" "$CLAUDE_DIR/settings.json"'

echo "=== run 2 (idempotency) ==="
cp "$CLAUDE_DIR/settings.json" "$TMP/settings.before"
HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/install.sh" --wiki-url "$WIKI_URL" >/dev/null 2>&1 || true
ck "settings unchanged on re-run" 'diff -q "$TMP/settings.before" "$CLAUDE_DIR/settings.json" >/dev/null'
ck "no stray settings backup"     '[ -z "$(ls "$CLAUDE_DIR/"settings.json.bak-* 2>/dev/null)" ]'

echo ""; [ "$FAIL" -eq 0 ] && echo "SMOKE: all pass" || echo "SMOKE: FAILURES"; exit "$FAIL"
