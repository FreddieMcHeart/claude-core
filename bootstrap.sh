#!/usr/bin/env bash
# Recreate ~/.claude/skills symlinks into this claude-core clone (fresh-machine setup).
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_SKILLS="${CLAUDE_SKILLS:-$HOME/.claude/skills}"
mkdir -p "$CLAUDE_SKILLS"
for s in models-router delegation-discipline claude-cost-audit; do
  target="$CORE_DIR/skills/$s"; link="$CLAUDE_SKILLS/$s"
  [ -e "$target" ] || { echo "missing $target"; exit 1; }
  [ -L "$link" ] && rm "$link"
  [ -e "$link" ] && { echo "refusing to overwrite real dir $link"; exit 1; }
  ln -s "$target" "$link"; echo "linked $s"
done
echo "done. Wiki: clone claude-core-wiki, then in your vault: git submodule update --init core"
