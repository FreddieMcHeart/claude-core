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

# c2. settings_merge.py — copy to ~/.claude/lib/ if absent
MERGE_SRC="$CORE_DIR/lib/settings_merge.py"
MERGE_DST="$CLAUDE_DIR/lib/settings_merge.py"
if [ -f "$MERGE_DST" ]; then
    echo "✓ $MERGE_DST already present — leaving"
else
    mkdir -p "$CLAUDE_DIR/lib"
    cp "$MERGE_SRC" "$MERGE_DST"
    echo "✓ Installed settings_merge.py → $MERGE_DST"
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

# ── e. Wiki submodule (REQUIRED) ─────────────────────────────────────────────
WIKI_URL="$WIKI_URL_OVERRIDE"
if [ -z "$WIKI_URL" ] && [ -f "$CLAUDE_DIR/lib/config_loader.py" ]; then
    WIKI_URL="$(python3 "$CLAUDE_DIR/lib/config_loader.py" wiki_url 2>/dev/null || true)"
fi
if [ -d "$CORE_DIR/docs/core" ] && git -C "$CORE_DIR" submodule status docs/core >/dev/null 2>&1; then
    echo "✓ docs/core submodule already present — leaving"
elif [ -z "$WIKI_URL" ]; then
    echo "FATAL: wiki_url not set. Pass --wiki-url <ssh-url> or set wiki_url in $CLAUDE_DIR/platform.config.toml" >&2
    exit 1
else
    echo "→ Adding wiki submodule at docs/core ($WIKI_URL)..."
    git -C "$CORE_DIR" submodule add -f "$WIKI_URL" docs/core \
        || { echo "FATAL: failed to add wiki submodule (auth to $WIKI_URL?)" >&2; exit 1; }
    git -C "$CORE_DIR" submodule update --init docs/core \
        || { echo "FATAL: failed to init wiki submodule" >&2; exit 1; }
    echo "✓ Wiki mounted at $CORE_DIR/docs/core"
fi

# ── f. Wire cost-discipline hook into settings.json (idempotent) ─────────────
echo "→ Merging cost-discipline hook into settings.json..."
python3 "$CORE_DIR/lib/settings_merge.py" \
    --settings "$CLAUDE_DIR/settings.json" \
    --claude-dir "$CLAUDE_DIR" \
    || { echo "FATAL: settings.json merge failed (malformed JSON?)" >&2; exit 1; }

# ── g. Relay (opt-in) ─────────────────────────────────────────────────────────
if [ "$WITH_RELAY" -eq 1 ]; then
    echo "→ Installing claude-relay..."
    uv tool install claude-relay || { echo "FATAL: uv tool install claude-relay failed" >&2; exit 1; }
    claude-relay init || { echo "FATAL: claude-relay init failed" >&2; exit 1; }
    echo "✓ Relay installed and initialized"
else
    echo "ℹ  Relay skipped (pass --with-relay to enable multi-session)"
fi

# ── h. Verify ─────────────────────────────────────────────────────────────────
echo ""
echo "=== verifying (doctor) ==="
exec "$CORE_DIR/doctor.sh"
