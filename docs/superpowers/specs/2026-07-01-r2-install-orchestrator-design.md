---
type: spec
created: 2026-07-01
tags: [portability, installer, claude-core, R2]
status: approved
---

# R2 — `claude-platform install` Orchestrator (design)

**Goal:** One command stands up the full Claude Code harness on a fresh machine by
composing the already-idempotent pieces (claude-core skills+hook+config, core-wiki
methodology, optional relay). No new network entrypoint — `claude-core/install.sh`
*becomes* the orchestrator: `git clone claude-core && ./install.sh`.

**Architecture:** `install.sh` stays a bash **composer** that shells out to existing
idempotent units. Model-A (versioned repos + symlinks + submodule), not a generator.

**Invariant (absolute):** every added step is guarded and additive; re-running on the
owner's machine is a pure no-op, byte-for-byte.

## Non-goals (YAGNI)
- No `--adapter` mechanism. Mondu is retired; no adapter exists. Add when a real one appears.
- No `curl | bash` entrypoint. Manual `git clone + ./install.sh` (audited, two steps).
- Full CI portability matrix is **R3**, not R2. R2 ships only the local smoke tests below.

## Install flow (extended `install.sh`)
| # | Step | Status |
|---|------|--------|
| 1 | Preflight: require `git`, `python3`; require `uv` only if `--with-relay` | NEW |
| 2 | `bootstrap.sh` — symlink 3 skills + `cost-discipline.py` hook | exists |
| 3 | config + `config_loader.py` + `CLAUDE.md` copy/symlink (guarded) | exists |
| 4 | **Wiki submodule (REQUIRED):** `git submodule add "$WIKI_URL" docs/core` (skip if present); failure = hard abort | NEW |
| 5 | **cost-discipline hook -> settings.json SAFE-merge** via `lib/settings_merge.py` | NEW |
| 6 | **`--with-relay` (opt-in):** `uv tool install claude-relay && claude-relay init` (relay merges its own hooks) | NEW |
| 7 | Final `doctor.sh` run as a gate (currently only via `--doctor`) | NEW |

## Components touched
- **`install.sh`** — +preflight, +wiki-submodule (required), +settings_merge call, +`--with-relay` branch, +`--wiki-url` flag, +final doctor gate. All new steps guarded. ~+60 lines.
- **`lib/settings_merge.py`** — NEW. The only piece of real new logic (contract below).
- **`doctor.sh`** — +3 checks: cost-discipline hook registered in settings.json; `docs/core` submodule resolves (hard, since required); if relay present, relay hooks registered.
- **`platform.config.toml.example` + `config_loader.py`** — +`wiki_url` key (default = owner SSH URL).
- **`.gitmodules`** — created by `submodule add`; URL supplied at runtime, NOT hardcoded to the owner's repo.

## `settings_merge.py` contract (the crux)
Self-contained (no relay dependency, since relay is opt-in). Borrows the *pattern* of
relay's proven merge, not the package.
- **Input:** settings.json path (default `~/.claude/settings.json`) + hook spec: 4 events
  (`PreToolUse`, `PostToolUse`, `SessionStart`, `PostCompact`) -> `cost-discipline.py` command.
- Load JSON (`{}` if absent). **Malformed JSON -> abort + timestamped backup + non-zero exit.** Never clobber.
- For each event: ensure a matcher entry carrying the cost-discipline command exists;
  **skip if already registered** (idempotent). All foreign hooks preserved.
- **Atomic write:** temp file -> timestamped `.bak-<ts>` of original -> rename.
- **Backup only when a write actually happens** (no churn when nothing changes).

## Config addition
`wiki_url` (top-level): SSH URL of the methodology wiki repo.
- Default = owner SSH URL (`git@github.com:FreddieMcHeart/claude-core-wiki.git`) -> owner-non-break.
- Override via `--wiki-url` flag or config key -> a colleague points at their own wiki.
- SSH chosen because the colleague already cloned claude-core over SSH (same key, zero extra auth).

## Owner-non-break (byte-for-byte)
On the owner's machine: skill/hook symlinks exist (bootstrap no-clobber), config/loader/CLAUDE.md
present (skip), `docs/core` submodule present (skip), hook already in settings.json (merge =
no-op, no write, no backup), doctor green. Re-run = pure no-op.

## Error handling
- Missing `git`/`python3` -> hard-fail preflight with a clear message.
- `--with-relay` but no `uv` -> that step fails only; core install already complete.
- Malformed settings.json -> `settings_merge.py` aborts + backs up; rest of install already done.
- **Wiki submodule add fails (no auth to private wiki) -> HARD ABORT** (wiki is required per decision).

## Testing (R2-local; full matrix is R3)
- Fresh isolated `$HOME` -> install -> doctor green.
- Idempotency: run twice -> second run all-skip, settings.json not rewritten.
- Malformed settings.json fixture -> merge aborts cleanly, backup created, exit != 0.
- Owner-non-break: dry-run against a copy of real `~/.claude` -> diff-clean.

## Decisions log
- Q1 entrypoint: **git clone + ./install.sh** (extend existing, no network entrypoint).
- Adapter: **dropped (YAGNI)**.
- Relay: **opt-in `--with-relay`**.
- Wiki submodule: **required** (hard-fail on error), mounted at `docs/core` inside the claude-core clone.
- settings-merge: **self-contained `lib/settings_merge.py`**.
- Wiki URL: **SSH, from config `wiki_url`**, default owner SSH, `--wiki-url` override; NOT hardcoded in `.gitmodules`.
