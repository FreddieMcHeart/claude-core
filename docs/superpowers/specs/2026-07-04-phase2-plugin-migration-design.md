# Phase-2 Claude Code Plugin Migration (claude-core) — Design

## Context

`claude-core`'s `install.sh` currently registers the `cost-discipline.py` hook into
`~/.claude/settings.json` via a hand-rolled, idempotent JSON merger
(`lib/settings_merge.py`, shipped in R2). Research against Claude Code's current docs
(2026-07-03) confirms the platform now has a native mechanism for this: a plugin can
declare `hooks/hooks.json` (schema identical to `settings.json`'s `hooks` object) and
`claude plugin install` merges it automatically — no custom merge script needed. This
makes `settings_merge.py` redundant going forward.

`downbeat` (the separately-owned relay/orchestration package, being prepared for public
PyPI release) has its own equivalent hook-wiring step (`downbeat init`) with the same
underlying problem. The two tools are owned by different sessions (this one owns
claude-core; `Claude-Cost-Optimazing-child` owns downbeat) and have already agreed: this
spec covers **claude-core only**. It is handed to `Claude-Cost-Optimazing-child` as a
reference once complete; downbeat's own plugin migration is scoped and executed
separately, by its owner, on its own timeline.

## Goals

- Replace `settings_merge.py`'s hand-merge with a native `.claude-plugin/hooks.json`.
- Migrate the real, already-adopted instance on this machine (ran `install.sh` for R2)
  without double-firing the `cost-discipline.py` hook.
- Preserve the absolute backward-compatibility invariant this repo has held throughout:
  nothing breaks for an existing user who runs the new `install.sh` unmodified.

## Non-goals

- downbeat's own plugin migration (separate spec, separate owner).
- Publishing `claude-core` itself as a public/marketplace plugin (`.claude-plugin/`
  lives inside the existing private repo; a marketplace wrapper can be layered on later
  without restructuring this work).
- Migrating any hook other than `cost-discipline.py` (no other hooks are currently
  registered by this repo's installer).

## Architecture

New directory inside the existing `claude-core` repo (no new repo):

```
claude-core/
├── .claude-plugin/
│   └── plugin.json          # {"name": "claude-core-hooks", "version": "...", "description": "..."}
├── hooks/
│   ├── hooks.json           # NEW — native plugin hook declarations
│   └── cost-discipline.py   # unchanged location; now resolved via ${CLAUDE_PLUGIN_ROOT}
├── lib/
│   └── config_loader.py     # unchanged
│   (settings_merge.py — REMOVED)
├── install.sh                # loses the settings-merge step, gains --migrate-to-plugin
├── doctor.sh                  # hook-registration check changed (see below)
├── bootstrap.sh                # loses the cost-discipline.py hook-symlink step
└── tests/
    ├── test_settings_merge.py         # REMOVED (module gone)
    └── test_migrate_to_plugin.sh      # NEW
```

`hooks/hooks.json` reproduces `settings_merge.py`'s `HOOK_EVENTS` table exactly, in
native plugin schema:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py pre-tool" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Agent|Task|Workflow",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py post-tool" }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py session-start" }
        ]
      }
    ],
    "PostCompact": [
      {
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/cost-discipline.py post-compact" }
        ]
      }
    ]
  }
}
```

Key change from the old model: the command now resolves through `${CLAUDE_PLUGIN_ROOT}`
(Claude Code's plugin-cache path), not the `$CLAUDE_DIR/hooks/cost-discipline.py`
symlink that `bootstrap.sh` currently creates. `bootstrap.sh`'s hook-symlink loop (the
`for h in cost-discipline.py; do ... done` block) is removed — the plugin system owns
that file's resolution once installed. The skill symlinks in `bootstrap.sh`
(`models-router`, `delegation-discipline`, `claude-cost-audit`) are untouched; only the
hook-symlink section goes.

## Migration mechanism — `install.sh --migrate-to-plugin`

New flag, additive to the existing flag set (`--doctor`, `--with-relay`, `--wiki-url`):

1. Read `$CLAUDE_DIR/settings.json`.
2. For each of the 4 `HOOK_EVENTS` tuples (same table as the old `settings_merge.py`,
   copied into the migration script before the module is deleted), compute the exact
   legacy command string: `f"{claude_dir}/hooks/cost-discipline.py {mode}"` (e.g.
   `/Users/x/.claude/hooks/cost-discipline.py pre-tool`).
3. Scan `settings.json`'s `hooks[event]` list; remove any hook-group entry whose
   `hooks[].command` equals that exact legacy string. Match is exact-string, not
   fuzzy — this must never remove a hook it didn't itself register, including
   `downbeat`'s `relay-inbox.py` entries or any hand-added user hook.
4. If removing an entry leaves `hooks[event]` as an empty list, delete that now-empty
   key entirely (avoid leaving stray `"PostCompact": []` clutter).
5. If nothing matched (nothing to migrate — e.g. a fresh machine that never ran the old
   installer), print `"Nothing to migrate — no legacy cost-discipline hook entries
   found."` and exit 0 without writing the file.
6. If something matched: same atomic-write pattern as the old `settings_merge.py`
   (backup to `settings.json.bak-<timestamp>` before writing, write to a `.tmp` file,
   `os.replace`). Malformed JSON → backup + abort with exit 2, identical to the old
   behavior — this failure mode is unchanged, just relocated into `install.sh`'s
   migration path.
7. On success, print:
   ```
   ✓ Removed N legacy cost-discipline hook entr(y|ies) from settings.json
   → Now run: claude plugin install <CORE_DIR>/.claude-plugin
   ```

`install.sh`'s normal (non-`--migrate-to-plugin`) run no longer touches hooks at all —
step (f) "Wire cost-discipline hook into settings.json" is deleted outright, and
`lib/settings_merge.py` is deleted from the repo (not just skipped — the whole hand-merge
path is retired, per the goal of having one source of truth). Every other install.sh
step (skill symlinks, `platform.config.toml` copy, `config_loader.py` copy, `CLAUDE.md`
symlink, wiki submodule, `--with-relay` opt-in) is unchanged.

`doctor.sh`'s check #5 ("hook:cost-discipline registered") changes from invoking
`settings_merge.py --check` to checking whether the plugin is installed and its hooks
resolve — exact check mechanism (reading Claude Code's plugin state, e.g.
`enabledPlugins`, vs. grepping `settings.json` for a `${CLAUDE_PLUGIN_ROOT}`-rooted
command) is an implementation-time decision, since the precise on-disk format of
`enabledPlugins` needs to be confirmed by direct inspection when the plan is written —
not guessed here.

## Testing

**`tests/test_migrate_to_plugin.sh`** (new, replaces `tests/test_settings_merge.py`):
- Fixture: hand-write a `settings.json` containing the exact legacy hook entries (same
  4 events, same matchers, same command format as `settings_merge.py` produced) plus one
  unrelated hook entry (simulating `downbeat`'s `relay-inbox.py` or a hand-added user
  hook) that must survive untouched.
- Run `install.sh --migrate-to-plugin` against that fixture in an isolated `$HOME` (same
  isolation pattern as `tests/smoke_install.sh`: copy the repo to a temp dir first,
  never touch the real `~/dev/claude-core`).
- Assert: all 4 legacy entries gone, the unrelated hook entry still present byte-for-byte,
  `settings.json` still valid JSON, exactly one `.bak-*` file created.
- Re-run the migration a second time (idempotency): assert "nothing to migrate", no new
  backup file.

**`tests/smoke_install.sh`** (existing, modified): the "settings has 4 events" assertion
(currently checking all 4 hook events are present after a fresh install) is removed,
since a fresh `install.sh` run no longer writes any hooks. Add a new assertion that a
fresh install does **not** write hooks into `settings.json` at all (the plugin now owns
that, and a fresh machine has no plugin installed during this CI-only smoke test —
`claude plugin install` itself is not exercised in CI; see below).

**`portability.yml` CI**: gets the new `test_migrate_to_plugin.sh` added to its run
steps alongside the existing smoke/pytest/ruff sequence. Real `claude plugin install` is
**not** run in CI — GitHub Actions runners don't have an interactive Claude Code
environment to install a plugin into. That step is validated manually, once, on the
real adopted machine.

**Manual validation (this machine)**: after the code lands, run `install.sh
--migrate-to-plugin` against the real `~/.claude/settings.json`, confirm the legacy
entries are gone and `cost-discipline.py` still fires exactly once per event (no
double-firing), then run `claude plugin install <path>/.claude-plugin` and confirm
`doctor.sh` reports the hook as registered via the new plugin-aware check.

## Rollout order

1. This spec → plan → subagent-driven or inline implementation → CI green → adopt
   on this machine (proof of concept for the whole pattern).
2. Once adopted and confirmed stable here, this spec is sent to
   `Claude-Cost-Optimazing-child` as a reference. downbeat's own plugin migration is a
   separate spec/plan cycle, owned and scoped by that session — not executed as part of
   this work.
