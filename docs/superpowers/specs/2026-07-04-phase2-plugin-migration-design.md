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
- Add an optional, non-hard-dependency integration point with `downbeat`: claude-core and
  downbeat must each install and run fully standalone (no `dependencies` entry in either
  `plugin.json`), but `doctor.sh`'s existing relay-awareness check (today: `command -v
  downbeat` + grep for `relay-inbox.py` in settings.json — the one place claude-core
  already conditionally reacts to downbeat's presence) should use the cleaner native
  plugin-detection mechanism instead of shelling to the downbeat binary directly.

  Correction (2026-07-06, caught before planning): an earlier draft of this goal also
  proposed gating `cost-discipline.py`'s `FORCE_LOAD_RULES` banner content on downbeat
  detection. Direct inspection of the live file found no downbeat/relay-specific content
  there to gate — `grep -rn "downbeat\|relay-inbox\|claude-relay" hooks/ skills/` returns
  zero matches in this repo. What that earlier draft was actually thinking of is
  `rlm-fanout` (`~/.claude/workflows/rlm-fanout.js`, driven by the `/rlm` and
  `/investigate` commands) — a self-contained Workflow-engine cross-repo investigation
  tool that has no dependency on downbeat and must not be gated on it; it works the same
  whether or not downbeat is installed. That part of the goal is dropped entirely.

## Non-goals

- downbeat's own plugin migration (separate spec, separate owner).
- Publishing `claude-core` itself as a public/marketplace plugin (`.claude-plugin/`
  lives inside the existing private repo; a marketplace wrapper can be layered on later
  without restructuring this work).
- Migrating any hook other than `cost-discipline.py` (no other hooks are currently
  registered by this repo's installer).
- Declaring a `dependencies` relationship between the two plugins in either
  `plugin.json` — explicitly rejected in favor of optional runtime detection (see
  "Composability with downbeat" below); each plugin must remain independently
  installable with zero knowledge of the other at install time.

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
    ├── test_migrate_to_plugin.sh      # NEW
    └── test_doctor_downbeat_detection.sh  # NEW
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
`settings_merge.py --check` (deleted) to: PASS if **either** `claude plugin list --json`
shows a plugin named `claude-core-hooks` enabled, **or** `settings.json` still contains
one of the 4 legacy hand-merged `cost-discipline.py` command entries (the exact strings
`settings_merge.py` used to write). This dual condition exists for the same reason as
the check #7 fix below: a user who has this code merged but hasn't yet run
`--migrate-to-plugin` + `claude plugin install` still has working hooks via the old
mechanism, and check #5 must not falsely WARN during that legitimate transition window.
Only WARN if neither condition holds (hooks genuinely missing either way).

Direct inspection during planning (`claude plugin list --json`, verified live on this
machine) confirms the exact format needed: each entry is `{"id": "<name>@<marketplace>",
"enabled": true|false, ...}` — detection matches on the `id` string's segment before
`@` equalling the target plugin name, with `enabled == true`.

## Composability with downbeat (optional integration, no hard dependency)

claude-core and downbeat are, and must remain, fully independent plugins — either
installs and works alone. Neither declares the other in `plugin.json`'s `dependencies`
(that field is a hard requirement in Claude Code's plugin system: it auto-pulls the
dependency in and blocks disabling a plugin something else depends on — wrong semantics
for "cooperate if present, don't require"). Research confirmed the clean native
discovery path instead: **`claude plugin list --json`**, which lists installed/enabled
plugins by name and is explicitly the documented machine-readable way to check plugin
state (there is no env var exposing this to hook commands directly).

The only real integration point today is `doctor.sh` check #7 ("relay hooks"), and
`doctor.sh` is a manual, infrequent, human-invoked health check — not a hot path like
`PreToolUse`/`PostToolUse`. That means no caching or `SessionStart` involvement is
needed; the check can call `claude plugin list --json` directly, once, each time
`doctor.sh` itself is run.

1. `doctor.sh` check #7 keeps today's `command -v downbeat` check (verified live on
   this machine: `downbeat` is currently a PyPI-installed CLI binary — `command -v
   downbeat` succeeds — but no Claude Code plugin named `downbeat` exists yet; child-1
   confirmed downbeat's own plugin work hasn't started). Dropping the CLI check in
   favor of the plugin check alone would silently break detection on every real
   downbeat install that exists today, since none of them are plugins yet.
2. Add plugin-detection as an **additional OR-condition**, not a replacement: check #7
   fires if `command -v downbeat` succeeds **OR** `claude plugin list --json` shows a
   plugin whose name (before the `@marketplace` suffix) is `downbeat` and `enabled` is
   true. This keeps today's real detection working and adds forward compatibility for
   whenever downbeat ships its own plugin, without needing another change later.
3. If `claude plugin list` isn't available at all (e.g. an unusually old Claude Code
   version without the plugin system) or errors, that side of the OR is simply treated
   as false — falls back to the `command -v downbeat` result alone, exactly as it
   behaves today. Never fail `doctor.sh` itself over this.
3. This stays genuinely optional in both directions: no `dependencies` entry in either
   plugin's manifest, no code in `cost-discipline.py`'s hot-path hook modes references
   downbeat at all.

## Testing

**`tests/test_doctor_downbeat_detection.sh`** (new): fake a `claude` binary on `PATH`
inside the isolated test `$HOME` that echoes a canned `plugin list --json` payload (one
variant with `downbeat` enabled, one without); run `doctor.sh` against each and assert
check #7 fires/skips accordingly. Also test the fallback path: no `claude` binary on
`PATH` at all → check #7 is skipped, `doctor.sh` still exits based only on its other
checks (never fails outright over a missing `claude` binary).

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
