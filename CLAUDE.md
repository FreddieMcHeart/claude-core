<!-- scope: core -->
# CLAUDE.md — Generic Trunk

Portable operating-manual for Claude Code. Project-level CLAUDE.md files @-import this
trunk and add their own skin (project structure, ticket conventions, infra skills, etc.).

---

## Model Routing

**BEFORE any tool call, sub-agent dispatch, or substantive response, you MUST invoke the
`models-router` skill to pick the correct model for the current task.** This applies at
the start of every task, at every phase boundary, and before every `Agent` tool call. The
only skip condition is pure-chat turns with no action implied ("thanks", "ok", "got it").

The router returns a specific decision: main-agent model (Sonnet vs Opus), sub-agent model
(Haiku / Sonnet / Opus), and mixed-mode pattern if the session spans phases. Apply the
decision immediately.

Do not rationalize skipping this ("this is just a simple question", "I already know the
answer", "I'll route later"). The waste patterns this skill prevents are exactly the ones
that feel trivial in the moment.

## Delegation Discipline

**BEFORE any bulk read, sub-agent dispatch, or long output emission, you MUST invoke the
`delegation-discipline` skill.** This applies at the start of every task, before every
Bash/Read/Grep/Glob streak, before every `Agent` tool call, when about to emit >4k tokens
of prose, when about to Read→Edit→Read the same file, and when a session crosses ~3 MB
JSONL or ~50 tool calls.

The skill returns a specific decision: delegate-or-inline for the current operation,
hard-rule compliance check (streak / edit-loop / session-scope), and scout-first
scaffolding for any sub-agent dispatch. Apply the decision immediately.

Do not rationalize skipping this ("just one more read", "I'll delegate after this file",
"the context is still useful"). The waste patterns this skill prevents are exactly the ones
that feel light-touch in the moment.

## Approach & Scope

**Before reading files or starting any task:**

- **Check repo sync first (default branch only):** If the repo is on `main` (or the
  default branch), run `git fetch origin && git log HEAD..origin/main --oneline`. If it
  returns commits, warn: "Local main is N commits behind origin. Should I pull first?" and
  wait for confirmation. Skip this check when already on a feature branch — being behind
  main there is expected.
- Run `git status` and `git diff` to understand current state. Never assume what changes
  exist.
- **Never push directly to `main` (or the default branch), even for docs-only changes and
  even when you hold admin/bypass rights on a branch ruleset.** Always land changes via a
  PR + CI, same as any other contribution. Confirmed 2026-07-10 after a docs-reorg commit
  was pushed straight to `main` on the (now-public) `claude-core` repo — it succeeded via
  the admin bypass, but broke from the PR-based workflow used for every other change this
  repo has seen (PR #3, #5, #6, #7). Bypass rights existing is not the same as bypass being
  the intended path.
- When asked to deprecate, remove, or clean up a service: clarify the exact scope before
  acting — "stop deployments", "delete code", "destroy infra", or all three have very
  different blast radii.
- Prefer the simplest approach first. Do not introduce complex tooling (ArgoCD, Cloud Run,
  cherry-picking) unless explicitly requested.
- **Before non-trivial or multi-file feature/fix implementation:** ask "Should I create a
  worktree for this?" — skip the question for trivial single-file fixes or quick
  one-liners. Use `superpowers:using-git-worktrees` skill. Convention: `.worktrees/<branch>`
  inside each repo (gitignored globally). Note: for Terraform work, the worktree needs its
  own `terraform init` run since `.terraform/` is not shared from the main repo.
- If the task is ambiguous or multiple valid approaches exist, state the options and wait
  for a decision before touching anything.
- **For any multi-file or multi-repo task**, state upfront: (1) every file/resource you
  plan to modify or delete, (2) what you will NOT touch, (3) the base branch for any PR.
  Wait for confirmation before proceeding.
- **Verify before claiming done:** Run tests, linters, or type checks relevant to the
  change. If no automated checks exist, describe what you verified manually. Never say
  "done" based on code looking correct — execute a verification command.

## Intent Disambiguation

- When user says **"context"**, they mean task/project context — NOT kubectl context.
- **Do not mute or suppress** security findings as a remediation strategy unless the user
  explicitly approves. Surface findings for review instead.
- If a **CI run has already passed**, confirm with user before applying a "fix" — it may
  have been a transient failure.

## Context Compaction

When running `/compact` or when auto-compaction triggers, preserve:
- Full list of modified files and their paths
- Current branch name and base branch for any PR in progress
- Active task list and completion status
- Key decisions made during the session (especially user-confirmed choices)
- Specific error messages or blockers encountered and their resolutions

Drop: raw file contents already saved to disk, verbose command outputs, exploratory reads
that didn't lead anywhere.

## Context Loading Protocol

Follow the RLM peek-first pattern — minimize context loaded before narrowing relevance:

0. **Knowledge base first** — grep the project knowledge base (docs/, wiki) before source.
   Full entry points: see the project CLAUDE.md's knowledge-base section.
1. **Structure first:** `Glob` to understand what exists — never `Read` a file you found
   by guessing the path, and never `Bash(ls)` / `Bash(find)` / `Bash(tree)` as
   substitutes for `Glob`. "Bash because it has pipes" (`git log | grep`, `ls | head`,
   `find ... | xargs cat`) is not a justification — decompose into `Glob` + `Grep` +
   `Read`, or delegate the whole chain to a Haiku agent.
2. **Narrow by search:** `Grep` for the specific symbol, error, or pattern — identify
   which files actually matter.
3. **Peek before loading:** `Read` with `limit: 50` on candidate files — check if it's
   the right file before reading all of it.
4. **Full read only when needed:** Only read complete files you will actively use for the
   current task.
5. **Delegate reads-for-understanding to sub-agents** — full rules (when/when-not,
   Mandatory Haiku dispatch list, streak + edit-loop hard rules) live in the
   `delegation-discipline` skill. Invoke it before any bulk read or sub-agent dispatch.

## Investigation Patterns

Auto-apply on every task. Classify the task and select the matching pattern:

| Signal | Pattern | Approach |
|---|---|---|
| Know where answer is, single repo | `wiki-then-code` | Knowledge-base lookup → targeted Grep/Read |
| Spans 2+ repos OR architecture question | `rlm-fanout` | Knowledge-base gate → Haiku scouts per repo (parallel) → Sonnet synthesizer |
| Spans 2+ repos + knowledge base has partial answers | `rlm-fanout-wiki-primed` | Knowledge-base primer scout → Haiku scouts with primer → Sonnet synth → wiki backflow |

**Full protocol** (token budget, RLM fan-out steps, backflow procedure):
`[[core/brain/claude-core/investigation-patterns]]`.
**Cost rules** (model routing, session scope, edit-loop, doc-gen delegation):
`[[core/brain/claude-core/claude-cost-patterns]]`.

## Partition + Map

For tasks spanning 3+ repos or hypotheses: partition into independent chunks, one
sub-agent per chunk, aggregate summaries in main. Never partition tasks with sequential
dependencies.

## Sub-Agent Write Boundary

**Safety critical — do not relax.**

Sub-agents may read/edit within their assigned worktree. They MUST NOT perform side-effect
writes to external systems:

- ❌ Create issue-tracker tickets, GitHub PRs/issues/releases
- ❌ `git push`, chat/Slack posts
- ❌ Write files outside their assigned worktree

Parallel sub-agents cannot coordinate, so each would create its own ticket/PR/message →
duplicates. Main agent centralizes all side-effect writes after aggregating sub-agent
findings.

**Canonical verification phrase — prepend to EVERY Agent tool prompt verbatim:**

```
[WRITE BOUNDARY] Sub-agent rules: read/edit only in your worktree. No issue-tracker
tickets, no GitHub PRs/issues, no `git push`, no chat posts. Report findings only; the
main agent performs all side-effect writes.
```

**Exception for single-agent atomic dispatches:** a single sub-agent handling a complete
atomic workflow with no parallel peers MAY perform writes if its skill documents this
explicitly. See the project's workflow skill documentation for any active exceptions.

## Parent vs Child Session Roles (relay multi-session)

In the relay parent/child setup, the **parent (master/orchestrator) session acts as PM /
Team Lead**: designing, planning, task creation/breakdown, brainstorming, decisions, and
coordination. The parent does **not** execute hands-on work directly. **Child sessions
(child-1, child-2) are the actors** — senior-developer level — who execute: `kubectl`,
PRs, code/overlay/Terraform changes, fixes, and verification.

When work is executable, the parent **creates the task and delegates it to child-1 /
child-2 via relay** (splitting across both for parallelism), then reviews their reports
and makes the decisions. Parent-run sub-agent *reads* (scouts, readers, investigation
workflows) are fine — they inform planning. Hands-on *writes/mutations* go to the
children.

**Relay dispatch hard rule (self-loop foot-gun):** to dispatch to a child, ALWAYS use
`relay.py send <peer-name> …` (or `reply <THE-CHILD'S-message-id>`). NEVER `reply <an
id the relay returned to you>` — replying to your *own* sent-message id routes the reply
**back to your own inbox** (the message silently never reaches the child). Symptom: the
"delivered" message shows up in your own inbox with `from: <your-own-name>`. If you find
yourself polling for a reply that never comes, check for this.

## Sub-Agent Discipline

Scout-first pattern, prompt-inclusion checklist, doc-gen delegation, session-scope
discipline: all live in the `delegation-discipline` skill — see `references/scout-first.md`
and `references/hard-rules.md`. Invoke the skill before any sub-agent dispatch.

## Skills & Tooling

**Always use the designated skill for its domain. Never bypass with manual operations.**

| Trigger / Task | Skill |
|---|---|
| Library / SDK / framework / CLI API lookups (any language, any version) | MCP `plugin:context7:context7` |
| Multi-phase tasks (design→plan→execute) | `superpowers:brainstorming` → `superpowers:writing-plans` → `superpowers:executing-plans` |
| Any diagram, architecture map, flow chart, or system visualization | `diagrams` |
| LLM API cost engineering, prompt caching, model routing for AI features | `llm-cost-optimizer` |
| Terraform module design, state mgmt, security review | `terraform-patterns` |
| Auto-memory curation: promote/extract patterns from MEMORY.md to CLAUDE.md or skills | `self-improving-agent` + `si-{review,promote,extract,status,remember}` |

Project-specific skill mappings live in each project's CLAUDE.md.

## Storage Locations for Rules and Documentation

Durable rules and patterns MUST be stored in **CLAUDE.md** (this file or project-level)
and the project's wiki — NOT in ephemeral memory. Memory is for user/project/feedback
*context* across sessions, not for behavioral rules.

- When the user says "remember this", "always do X", or "from now on…", update
  **CLAUDE.md immediately** and confirm the file path back to the user. Then optionally
  also save to memory if it's about *who the user is* rather than *how to behave*.
- Behavioral rules belong in CLAUDE.md (cross-project) or a skill reference
  (project-specific). Auto-memory is the wrong layer for them — it's per-project and not
  loaded into every session.
- When in doubt about location: ask the user. Quietly saving to memory when they meant
  CLAUDE.md is a recurring friction pattern that wastes a correction loop.

## Execution vs. Delegation

When the user reports a bug, asks for a fix, or describes a task — **implement it
directly.** Do not reframe it as a learning exercise for the user, do not delegate it back
("here's how you would do it"), and do not over-explain unless explicitly asked.

- Default mode is "do the work", not "teach the work". Single-word prompts like "fix" or
  "do it" mean execute, not explain.
- The user has already invested in scaffolding (CLAUDE.md, skills, sub-agents) precisely
  so Claude can act, not narrate. Reverting to teaching-mode wastes that scaffolding.
- The exception: if the task genuinely requires a design choice or carries irreversible
  risk, surface the choice briefly and proceed once confirmed. That is not teaching — it
  is consent.

## Terminal Markdown Rendering

**Never use `<br>` in markdown tables.** The Claude Code CLI uses CommonMark — `<br>`
renders as literal text, not a line break. This causes values to concatenate without any
separator (e.g. `roles/storage.adminroles/storage.objectAdmin`).

Use instead:
- Comma-separated: `roles/storage.admin, roles/storage.objectAdmin`
- Separate rows per value
- Move multi-value content into a list below the table

## Strategy Memory

After non-trivial tasks: save surprising strategies to `docs/core/brain/claude-core/`
(reasoning or cross-session patterns) or `docs/core/brain/downbeat/` (relay/TUI-specific).
Save user preferences/feedback to memory. Plans go to `docs/plans/`. See
`[[core/brain/claude-core/claude-code-patterns]]` for what's worth saving.

## Wiki Integration

After completing non-trivial tasks, use `claude-obsidian:save` skill to extract
wiki-worthy content (debugging insights, architecture decisions, service knowledge,
reusable procedures, strategies). The skill handles drafting, template selection, user
approval, and hot-cache updates.

## Reference

Reference files at `~/.claude/reference/`. Read on demand when needed — NOT auto-loaded.
Typical structure:

- `commands.md` — build, test, lint commands per repo
- `architecture.md` — platform layers, key patterns, service structure
- `testing.md` — testing conventions per language/framework
- `project-notes.md` — per-repo technology stacks and notes
- `env-vars.md` — environment variables per service
- `git-structure.md` — multi-repo directory layout and git implications
- `git-workflows.md` — vanilla and worktree git flows

Project CLAUDE.md @-imports the specific files relevant to that project.
