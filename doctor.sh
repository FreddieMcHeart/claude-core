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

# ── 6. docs/core wiki submodule resolves ─────────────────────────────────────
if [ -d "$CORE_DIR/docs/core" ] && [ -n "$(ls -A "$CORE_DIR/docs/core" 2>/dev/null)" ]; then
    _pass "wiki:docs/core submodule"
else
    _warn "wiki:docs/core" "submodule missing or empty — run ./install.sh (needs wiki_url)"
fi

# ── 7. relay hooks (only if downbeat is installed — CLI today, plugin someday) ─
if command -v downbeat >/dev/null 2>&1 || _plugin_enabled "downbeat"; then
    if grep -q "relay-inbox.py" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
        _pass "relay:hooks registered"
    else
        _warn "relay:hooks" "downbeat installed but hooks not in settings.json — run 'downbeat init'"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Summary: $PASS PASS, $WARN WARN"
[ "$WARN" -eq 0 ] && exit 0 || exit 1
