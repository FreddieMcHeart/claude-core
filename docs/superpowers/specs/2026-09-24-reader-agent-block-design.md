# Reader-agent block — design

Date: 2026-09-24. Status: design approved in chat, spec awaiting review.

## Problem

When the main session runs a read-only CLI that has a dedicated reader agent
(`kubectl logs`, `gh pr view`, …), the output lands in the main context at main-model
prices. The hook already detects four such CLIs in `READER_REFLEX` and answers with a
warning. That warning does not change behaviour, and the reason is measured, not guessed:

| time (UTC, 2026-09-23, session `6efebd28`) | hook output | reached the model? |
|---|---|---|
| 17:55:00 | `emit()` warning, oversized tool result | no |
| 18:44:52 | `emit()` warning, `STOP — dispatch gh-reader` | no |
| 18:45:12 | `emit()` warning, third inline gh read | no |
| 18:41:03 | publication-gate `deny` | yes, as the tool error |

All four are recorded in the transcript as `attachment/hook_system_message`; only the deny
was in the model's context. n=3 warnings in one session, checked against the model's own
context. So `emit()` → `{"systemMessage": …}` on `PreToolUse` reaches the UI and not the
model, while a block's reason does reach it. The warn tier's delivery is tracked separately
(fleet #121) and is out of scope here.

## Decision

Approach A: turn the existing reader reflex from a warning into a block, and add the two
CLIs that have a reader agent but no detection. Rejected: a separate hook (a second
classifier for the same commands would drift from the first) and a `wraps:` field in agent
frontmatter (a new contract across eight files for a ninth agent that does not exist).

## Behaviour

**Blocked, from the first call:** a `PreToolUse` `Bash` call where

1. `is_subagent_call(payload)` is false (main session), and
2. the command is a read in one of six families, and
3. that family's reader agent is installed (`reader_roster()` finds its file under
   `~/.claude/agents/`).

| family | reader | read detection |
|---|---|---|
| `kubectl` | `kubectl-reader` | existing `_KUBECTL_READ_VERBS` path |
| `gh` | `gh-reader` | existing `is_gh_read` subject/action allowlist |
| `pup` | `datadog-reader` | existing pup-ro detection |
| `slack-cli.sh` | `slack-reader` | existing read-subcommand detection |
| `gcloud` | `gcloud-reader` | NEW: `list`, `describe`, `get-iam-policy`, `logging read`, `config list`, `auth list`, `asset search-all-resources` |
| `vault` | `vault-reader` | NEW: `status`, `list`, `read`, `kv list`, `kv get`, `secrets list`, `auth list` |

The new verb sets are taken from each agent's own description, so the hook blocks exactly
what the agent can take over.

**Passes silently:**

- any call from a sub-agent, including the reader itself
- writes: `gh pr create`, `kubectl apply`, `vault write`, and anything not in the read sets
- a family whose reader agent is not installed — blocking toward a missing agent is a dead end
- `CC_DISCIPLINE_BLOCK=0` — the existing advisory-only switch, which falls back to today's
  warning. As today, the block text does not mention it

**Block reason:** names the agent and the call, e.g.
`this is gh-reader work — Agent(subagent_type='gh-reader', model='haiku', prompt=…)`.
For `vault` it adds one line: `vault-reader is DEV-only; if this is prod it will refuse — ask
the operator instead.`

**Counters:** a blocked call does not advance the read streak or the aggregate. A counter
that ratchets on refused calls is a defect this hook has already had once.

## Testing

New `tests/test_reader_block.py`, driving the real `hooks/cost-discipline.py pre-tool` with
harness-shaped payloads on stdin, following `tests/test_read_block_tier.py`:

| case | expected |
|---|---|
| main, one read per family (6) | `decision: block`, reason names the family's reader |
| same call with `agent_type` set | no output |
| main, a write per family | no block |
| reader file absent (temp agents dir) | no block |
| `CC_DISCIPLINE_BLOCK=0` | no block |
| blocked call, then state read back | streak and aggregate unchanged |
| `vault` block | reason carries the DEV-only line |

Three arms: the finished tests against `origin/main` (expect the six block cases red for
the right reason, a warning in place of a block), against the branch, and cases that pass in
both reported separately.

Real artifact: after install, one real `gh pr list` from a live main session must come back
as a tool error carrying the reason. That is the delivery proof the warn tier never had.

Review: hot-path code, so `feature-dev:code-reviewer` (`model: sonnet`) before merge; its
findings are reproduced by execution before any fix.

## Rollout

The hook runs as the `claude-core-hooks` plugin, so a merge is not an install:
`claude plugin update claude-core-hooks@claude-core-local`, then a session restart. The
installed copy was v0.18.2 against v0.19.1 on 2026-09-24, so the update also brings in
unrelated changes already on main.

## Not in scope

- the `emit()` delivery defect for every other warning (fleet #121)
- MCP readers (`jira-reader`, `notion-reader`); this is `Bash` only
- a `wraps:` field or any change to agent files
