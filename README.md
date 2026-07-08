# claude-core

[![CI](https://img.shields.io/github/actions/workflow/status/FreddieMcHeart/claude-core/portability.yml?branch=main&label=ci)](https://github.com/FreddieMcHeart/claude-core/actions/workflows/portability.yml)
[![Release](https://img.shields.io/github/v/release/FreddieMcHeart/claude-core)](https://github.com/FreddieMcHeart/claude-core/releases)

Portable, project-agnostic Claude Code methodology — **no Mondu data**. The reusable
"operating manual" you carry to every machine/project: cost-discipline reflexes,
delegation rules, and a native Claude Code plugin, versioned and released
automatically from conventional commits.

![claude-core demo: bootstrap.sh symlinks skills, doctor.sh reports health](./examples/bootstrap-and-doctor/demo.gif)

Want to see it before installing? [`examples/bootstrap-and-doctor/`](./examples/bootstrap-and-doctor/)
is a one-command walkthrough (this GIF is `demo.sh` from that directory, recorded
verbatim with [VHS](https://github.com/charmbracelet/vhs)).

## Install

```bash
git clone <this-repo> ~/dev/claude-core
~/dev/claude-core/bootstrap.sh        # symlinks skills into ~/.claude/skills
```

### Native Claude Code plugin (optional)

`claude-core`'s cost-discipline hook also ships as a native Claude Code plugin,
an alternative to hand-merging hook entries into `~/.claude/settings.json`:

```bash
claude plugin marketplace add ~/dev/claude-core
claude plugin install claude-core-hooks@claude-core-local
```

Already ran the old installer and hand-merged the hooks yourself? Migrate cleanly
first, then install the plugin:

```bash
~/dev/claude-core/install.sh --migrate-to-plugin
claude plugin marketplace add ~/dev/claude-core
claude plugin install claude-core-hooks@claude-core-local
```

Run `./doctor.sh` any time to check hook registration, plugin detection, and
overall health.

## Contents

- `skills/models-router` — pick the cheapest model that fits the turn.
- `skills/delegation-discipline` — when/what to delegate to sub-agents.
- `skills/claude-cost-audit` — measure session cost + waste patterns.
- `.claude-plugin/` (`claude-core-hooks`) — native Claude Code plugin packaging
  of the cost-discipline hook (`hooks/hooks.json`), an alternative to manual
  `settings.json` editing.
- `bootstrap.sh` — symlink the skills above into `~/.claude/skills` on a fresh
  machine.
- `doctor.sh` — health check: hook registration (hand-merged or plugin), plugin
  detection, wiki submodule presence.

## Releases

Versions are cut automatically by [python-semantic-release](https://python-semantic-release.readthedocs.io/)
from conventional commits (`feat:`/`fix:` trigger a release, `docs:`/`chore:`/`ci:`
do not) — see [CHANGELOG.md](./CHANGELOG.md) for the full history. Every release
creates a GitHub Release and syncs `.claude-plugin/plugin.json`'s version.

Companion: `claude-core-wiki` (methodology notes, mounted as a `docs/core`
submodule in any Obsidian vault).
