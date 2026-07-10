#!/usr/bin/env bash
# Recreate ~/.claude/skills (and commands) symlinks into this claude-core clone
# (fresh-machine setup).
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_SKILLS="${CLAUDE_SKILLS:-$HOME/.claude/skills}"
# Derived from CLAUDE_SKILLS (not $HOME directly) so a caller overriding
# CLAUDE_SKILLS/CLAUDE_DIR (e.g. install.sh's --claude-dir, or the smoke test's
# isolated $HOME) gets commands/ as a sibling of skills/ instead of drifting
# back to the real ~/.claude. Still overridable directly via CLAUDE_COMMANDS.
CLAUDE_COMMANDS="${CLAUDE_COMMANDS:-$(dirname "$CLAUDE_SKILLS")/commands}"

mkdir -p "$CLAUDE_SKILLS"
for s in models-router delegation-discipline claude-cost-audit harvest; do
  target="$CORE_DIR/skills/$s"; link="$CLAUDE_SKILLS/$s"
  [ -e "$target" ] || { echo "missing $target"; exit 1; }
  [ -L "$link" ] && rm "$link"
  [ -e "$link" ] && { echo "refusing to overwrite real dir $link"; exit 1; }
  ln -s "$target" "$link"; echo "linked skill $s"
done

mkdir -p "$CLAUDE_COMMANDS"
for c in harvest; do
  target="$CORE_DIR/commands/$c.md"; link="$CLAUDE_COMMANDS/$c.md"
  [ -e "$target" ] || { echo "missing $target"; exit 1; }
  [ -L "$link" ] && rm "$link"
  [ -e "$link" ] && { echo "refusing to overwrite real file $link"; exit 1; }
  ln -s "$target" "$link"; echo "linked command /$c"
done

echo "done. Next: (1) run 'claude plugin marketplace add <this-repo> && claude plugin install claude-core-hooks@claude-core-local' to register cost-discipline hooks; (2) add claude-core-wiki as a docs/core submodule; (3) run 'downbeat init' for relay; (4) copy harvest.config.json.example to ~/.claude/harvest.config.json and set seedsDir for the harvest skill."
