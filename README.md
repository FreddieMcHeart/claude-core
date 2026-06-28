# claude-core

Portable, project-agnostic Claude Code methodology — **no Mondu data**. The reusable
"operating manual" you carry to every machine/project.

## Contents
- `skills/models-router` — pick the cheapest model that fits the turn.
- `skills/delegation-discipline` — when/what to delegate to sub-agents.
- `skills/claude-cost-audit` — measure session cost + waste patterns.
- `bootstrap.sh` — symlink these skills into `~/.claude/skills` on a fresh machine.

## Setup on a new machine
```bash
git clone <this-repo> ~/dev/claude-core
~/dev/claude-core/bootstrap.sh        # symlinks skills into ~/.claude/skills
```
Companion: `claude-core-wiki` (methodology notes, mounted as a `docs/core` submodule in any Obsidian vault).
