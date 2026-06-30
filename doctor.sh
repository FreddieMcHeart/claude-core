#!/usr/bin/env bash
# claude-core health check — PASS/WARN each component; exits 1 if any WARN.
# Usage:
#   ./doctor.sh
#   CLAUDE_DIR=/custom ./doctor.sh   — check against a non-default target dir
set -uo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
PASS=0
WARN=0

_pass() { echo "PASS  $1"; PASS=$((PASS + 1)); }
_warn() { echo "WARN  $1 — $2"; WARN=$((WARN + 1)); }

# ── 1. Core skill symlinks ────────────────────────────────────────────────────
for skill in models-router delegation-discipline claude-cost-audit; do
    link="$CLAUDE_DIR/skills/$skill"
    if [ -L "$link" ] && [ -e "$link" ]; then
        _pass "skill:$skill"
    elif [ -L "$link" ]; then
        _warn "skill:$skill" "dangling symlink — rerun ./bootstrap.sh"
    else
        _warn "skill:$skill" "missing — run ./install.sh"
    fi
done

# ── 2. config_loader.py present and functional ───────────────────────────────
LOADER="$CLAUDE_DIR/lib/config_loader.py"
if [ -f "$LOADER" ]; then
    PR=$(python3 "$LOADER" project_root 2>/dev/null || true)
    if [ -n "$PR" ]; then
        _pass "config_loader (project_root=$PR)"
        if [ -d "$PR" ]; then
            _pass "project_root:resolves ($PR exists)"
        else
            _warn "project_root:resolves" "'$PR' is not an existing directory — edit $CLAUDE_DIR/platform.config.toml"
        fi
    else
        _warn "config_loader" "ran but returned empty output — inspect $LOADER"
    fi
else
    _warn "config_loader" "$LOADER absent — copy lib/config_loader.py from your harness repo into ~/.claude/lib/"
fi

# ── 3. ~/.claude/CLAUDE.md present ───────────────────────────────────────────
if [ -e "$CLAUDE_DIR/CLAUDE.md" ]; then
    _pass "claude_md ($CLAUDE_DIR/CLAUDE.md)"
else
    _warn "claude_md" "$CLAUDE_DIR/CLAUDE.md absent — run ./install.sh"
fi

# ── 4. platform.config.toml present ──────────────────────────────────────────
if [ -f "$CLAUDE_DIR/platform.config.toml" ]; then
    _pass "platform_config ($CLAUDE_DIR/platform.config.toml)"
else
    _warn "platform_config" "$CLAUDE_DIR/platform.config.toml absent — run ./install.sh"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Summary: $PASS PASS, $WARN WARN"
[ "$WARN" -eq 0 ] && exit 0 || exit 1
