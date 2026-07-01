#!/usr/bin/env bash
# claude-core installer — idempotent, never clobbers existing config or CLAUDE.md.
# Usage:
#   ./install.sh            — full install
#   ./install.sh --doctor   — health check only (delegates to doctor.sh)
#
# Override targets for testing:
#   HOME=/tmp/test ./install.sh         — use isolated home
#   CLAUDE_DIR=/custom ./install.sh     — use custom ~/.claude equivalent
set -euo pipefail

CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

WITH_RELAY=0
WIKI_URL_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --doctor)     exec "$CORE_DIR/doctor.sh" ;;
        --with-relay) WITH_RELAY=1 ;;
        --wiki-url)   WIKI_URL_OVERRIDE="${2:?--wiki-url needs a value}"; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── preflight ────────────────────────────────────────────────────────────────
command -v git      >/dev/null 2>&1 || { echo "FATAL: git not found" >&2; exit 1; }
command -v python3  >/dev/null 2>&1 || { echo "FATAL: python3 not found" >&2; exit 1; }
if [ "$WITH_RELAY" -eq 1 ]; then
    command -v uv >/dev/null 2>&1 || { echo "FATAL: --with-relay needs uv (https://docs.astral.sh/uv/)" >&2; exit 1; }
fi

echo "=== claude-core install ==="

# ── a. Core skill symlinks (via bootstrap.sh) ────────────────────────────────
echo "→ Linking core skills..."
CLAUDE_SKILLS="$CLAUDE_DIR/skills" "$CORE_DIR/bootstrap.sh"

# ── b. platform.config.toml — copy example only if absent ────────────────────
CONFIG="$CLAUDE_DIR/platform.config.toml"
EXAMPLE="$CORE_DIR/platform.config.toml.example"
if [ -f "$CONFIG" ]; then
    echo "✓ $CONFIG already present — leaving (no clobber)"
else
    mkdir -p "$CLAUDE_DIR"
    cp "$EXAMPLE" "$CONFIG"
    echo "✓ Copied platform.config.toml.example → $CONFIG"
    echo "  ⚠  Edit before first use: set project_root, wiki_path, jira.email"
fi

# ── c. config_loader.py — copy to ~/.claude/lib/ if absent ──────────────────
LOADER_SRC="$CORE_DIR/lib/config_loader.py"
LOADER_DST="$CLAUDE_DIR/lib/config_loader.py"
if [ -f "$LOADER_DST" ]; then
    echo "✓ $LOADER_DST already present — leaving (no clobber)"
else
    mkdir -p "$CLAUDE_DIR/lib"
    cp "$LOADER_SRC" "$LOADER_DST"
    echo "✓ Installed config_loader.py → $LOADER_DST"
fi

# ── d. ~/.claude/CLAUDE.md — symlink to generic trunk only if absent ──────────
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
TRUNK="$CORE_DIR/CLAUDE.md"
if [ -e "$CLAUDE_MD" ]; then
    echo "✓ $CLAUDE_MD already present — leaving"
    if [ ! -L "$CLAUDE_MD" ]; then
        echo "  ℹ  It is a regular file. To pull in the generic trunk, add to it:"
        echo "      @${TRUNK}"
    fi
else
    mkdir -p "$CLAUDE_DIR"
    ln -s "$TRUNK" "$CLAUDE_MD"
    echo "✓ Symlinked $CLAUDE_MD → $TRUNK"
fi

# ── e. Next steps ─────────────────────────────────────────────────────────────
echo ""
echo "=== Next steps ==="
echo "1. Edit $CONFIG"
echo "   Set: project_root, wiki_path, jira.email (at minimum)"
echo "2. Add a knowledge-base submodule:"
echo "   cd your-obsidian-vault && git submodule add <claude-core-wiki-url> docs/core"
echo "3. Enable relay (optional):"
echo "   claude-relay init     # installs relay hooks + inbox into ~/.claude"
echo ""
echo "Run ./doctor.sh to verify."
