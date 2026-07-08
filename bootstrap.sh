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
echo "done. Next: (1) run 'claude plugin marketplace add <this-repo> && claude plugin install claude-core-hooks@claude-core-local' to register cost-discipline hooks; (2) add claude-core-wiki as a docs/core submodule; (3) run 'downbeat init' for relay."
