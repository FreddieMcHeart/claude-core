#!/usr/bin/env bash
# claude-core health check — PASS/WARN each component; exits 1 if any WARN.
# Usage:
#   ./doctor.sh
#   CLAUDE_DIR=/custom ./doctor.sh   — check against a non-default target dir
#   CORE_DIR=/path ./doctor.sh       — check a different claude-core checkout
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
for skill in models-router delegation-discipline claude-cost-audit harvest; do
    link="$CLAUDE_DIR/skills/$skill"
    if [ -L "$link" ] && [ -e "$link" ]; then
        _pass "skill:$skill"
    elif [ -L "$link" ]; then
        _warn "skill:$skill" "dangling symlink — rerun ./bootstrap.sh"
    else
        _warn "skill:$skill" "missing — run ./install.sh"
    fi
done

# ── 1b. Command symlinks ──────────────────────────────────────────────────────
for cmd in harvest; do
    link="$CLAUDE_DIR/commands/$cmd.md"
    if [ -L "$link" ] && [ -e "$link" ]; then
        _pass "command:/$cmd"
    elif [ -L "$link" ]; then
        _warn "command:/$cmd" "dangling symlink — rerun ./bootstrap.sh"
    else
        _warn "command:/$cmd" "missing — run ./bootstrap.sh"
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
CORE_DIR="${CORE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
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
    _pass "hook:cost-discipline registered (legacy hand-merge — consider: ./install.sh --migrate-to-plugin && claude plugin marketplace add $CORE_DIR && claude plugin install claude-core-hooks@claude-core-local)"
else
    _warn "hook:cost-discipline" "not registered — run: claude plugin marketplace add $CORE_DIR && claude plugin install claude-core-hooks@claude-core-local"
fi

# ── 6. docs/core wiki mirror is present AND current ───────────────────────────
# Not a submodule, despite the name this check carried: claude-core declares no
# .gitmodules and .gitignore excludes /docs/core. It is a plain clone of the wiki
# remote, mounted read-only, and nothing pulls it automatically — the project
# CLAUDE.md makes that a manual last step of every wiki edit, which is exactly the
# kind of step that gets skipped. So the failure mode here is not "missing", it is
# "present and out of date", and a non-emptiness test passes that with a green
# line. Measured 2026-07-30: the mirror was two commits behind and this said PASS.
WIKI_DIR="$CORE_DIR/docs/core"
if [ ! -d "$WIKI_DIR" ] || [ -z "$(ls -A "$WIKI_DIR" 2>/dev/null)" ]; then
    _warn "wiki:docs/core" "missing or empty — run ./install.sh (needs wiki_url)"
elif ! git -C "$WIKI_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    _warn "wiki:docs/core" "present but not a git checkout — currency cannot be established"
elif ! git -C "$WIKI_DIR" rev-parse --verify -q origin/main >/dev/null 2>&1; then
    _warn "wiki:docs/core" "no origin/main ref — presence is all this check can establish"
else
    _wiki_behind="$(git -C "$WIKI_DIR" rev-list --count HEAD..origin/main 2>/dev/null)"
    case "$_wiki_behind" in (''|*[!0-9]*) _wiki_behind="" ;; esac
    # --git-path prints a path RELATIVE to the repo while this test runs from the
    # caller's cwd. On the submodule layout .git was a pointer file and git returned
    # an absolute path, so the mistake was invisible; on a plain clone or a symlinked
    # mount it returns .git/FETCH_HEAD and resolves against the wrong directory,
    # reporting a wiki fetched minutes ago as stale for a week. One layout was never
    # enough to exercise this.
    _wiki_git_dir="$(git -C "$WIKI_DIR" rev-parse --absolute-git-dir 2>/dev/null)"
    _wiki_fetch_head="${_wiki_git_dir:-/nonexistent}/FETCH_HEAD"
    if [ -z "$_wiki_behind" ]; then
        _warn "wiki:docs/core" "could not count commits behind origin/main"
    elif [ "$_wiki_behind" -gt 0 ]; then
        _warn "wiki:docs/core" "$_wiki_behind commit(s) behind origin/main — run: git -C $WIKI_DIR pull origin main"
    elif [ ! -f "$_wiki_fetch_head" ] || [ -n "$(find "$_wiki_fetch_head" -mtime +7 2>/dev/null)" ]; then
        # Agreeing with a remote-tracking ref nobody has fetched is two stale things
        # agreeing. "Could not look" must not be reported as "nothing found".
        _warn "wiki:docs/core" "matches origin/main, but that ref was last fetched over a week ago — run: git -C $WIKI_DIR fetch origin"
    else
        _pass "wiki:docs/core current with origin/main ($(git -C "$WIKI_DIR" rev-parse --short HEAD))"
    fi
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
