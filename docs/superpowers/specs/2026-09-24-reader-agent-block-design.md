# Reader-agent block — design

Date: 2026-09-24. Status: design approved in chat; revised after the adversarial review
(`2026-09-24-reader-agent-block-design-review-2026-09-24.md`) for findings F1–F5 and F9. The
other findings are still open.

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
3. that family's reader agent is installed: its name from `READER_FOR_FAMILY` (below) is in
   `reader_roster()`, which lists `~/.claude/agents/*-reader.md`.

| family | reader | read set |
|---|---|---|
| `kubectl` | `kubectl-reader` | `_KUBECTL_READ_VERBS` (`get`, `describe`, `logs`, `top`) |
| `gh` | `gh-reader` | `is_gh_read` subjects × `view`, `list`, `diff`, `checks`, `status`; plus `search code`; plus `api` only as a GET (below) |
| `pup` | `datadog-reader` | existing pup-ro detection |
| `slack-cli.sh` | `slack-reader` | existing read subcommands |
| `gcloud` | `gcloud-reader` | NEW: `list`, `describe`, `get-iam-policy`, `logging read`, `config list`, `auth list`, `asset search-all-resources` |
| `vault` | `vault-reader` | NEW: `status`, `secrets list`, `auth list`, `list`, `kv list` |

Where the read sets come from:

- **vault** — the operations `~/.claude/scripts/vault-dev-read.sh` actually serves (`status`,
  `auth-list`, `secrets-list`, `list`, `keys`; `vault-reader.md:78`). `vault read` and
  `vault kv get` are deliberately NOT in the set: the reader returns key names and never a
  value (`vault-reader.md:90-91`), so blocking a value read would route it to an agent that
  must refuse it. Those calls pass.
- **gcloud** — a subset of the allowed shapes in `gcloud-reader.md`'s Hard boundaries. The
  subset is known to be incomplete (for example `config get-value`); review finding F10, still
  open.

### Family → reader map

The reader name is looked up, never derived. `pup` is served by `datadog-reader`, so
`f"{family}-reader"` would look for a file that does not exist and pup would never block. One
constant carries the mapping, and "installed" means the mapped name is in `reader_roster()`:

```python
READER_FOR_FAMILY = {
    "kubectl": "kubectl-reader", "gh": "gh-reader", "pup": "datadog-reader",
    "slack": "slack-reader", "gcloud": "gcloud-reader", "vault": "vault-reader",
}
```

### Detection: one classifier, command position only

Today's detectors were written for a warning, where a false positive cost nothing. Under a
block they fail in both directions (review F2, F9):

- **kubectl scans the whole string** (`~:3485-3510`), so `git commit -m "… kubectl get pods …"`,
  `echo 'kubectl logs'` and `kubectl delete configmap top` all count as reads.
- **gh, pup and slack look at the first token only** (`~:3467`, `~:3520`), so
  `cd repo && gh pr list`, `GH_REPO=o/r gh pr list` and `gh -R o/r pr view 5` are all missed.

All six families therefore go through one classifier. It replaces the per-family checks
outright, including where the `CC_DISCIPLINE_BLOCK=0` fallback still warns, so the hook never
carries two classifiers for the same commands (the reason a separate hook was rejected above):

1. Split the command into simple commands on `&&`, `||`, `;`, `|`, and tokenise each with
   `shlex`. A quoted argument is one token, so text inside it is never read as a command. For a
   command containing a heredoc (`<<`), only the line holding the `<<` is classified; the body
   is data.
2. In each simple command, skip leading `VAR=value` assignments and an `env` prefix. The next
   token is the command. A segment whose command is `cd` is ignored.
3. If the command is one of the six CLIs (for `pup` and `slack-cli.sh`, the path's basename),
   skip that CLI's global flags, including the value of flags that take one (`gh -R/--repo`;
   `kubectl -n/--namespace/--context/--kubeconfig`; `gcloud --project/--account/--format`;
   `vault -address/-namespace`). The next non-flag tokens are the verb, looked up in the read
   set above. Never scan further forward for a verb.
4. **gh api is a read only as a GET.** It is a write, and passes, if it carries `-X`/`--method`
   with anything other than `GET`, or any of `-f`, `-F`, `--field`, `--raw-field`, `--input`
   (gh switches to POST when these are present). `gh api graphql` is a read only when its query
   contains no `mutation`.
5. **Block** only if at least one simple command is a read of an installed family AND no simple
   command in the whole line invokes one of the six CLIs outside its read set. Any such other
   invocation counts as a write. `kubectl rollout restart … && kubectl get pods` passes:
   splitting a mixed line into a dispatch plus an inline write is the caller's call, not the
   hook's.

**Passes silently:**

- any call from a sub-agent, including the reader itself
- writes: `gh pr create`, `gh api -X POST …`, `kubectl apply`, `vault write`, and anything
  not in the read sets
- vault value reads: `vault read`, `vault kv get` — the reader cannot return a value
- a command that only mentions a CLI in an argument, a quoted string or a heredoc body
- a family whose reader agent is not installed — blocking toward a missing agent is a dead end
- `CC_DISCIPLINE_BLOCK=0` — the existing advisory-only switch, which falls back to today's
  warning. As today, the block text does not mention it

**Block reason:** names the agent and the call, e.g.
`this is gh-reader work — Agent(subagent_type='gh-reader', model='haiku', prompt=…)`.
For `vault` it adds one line: `vault-reader is DEV-only; if this is prod it will refuse — ask
the operator instead.`

**Counters and placement:** a blocked call does not advance the read streak or the aggregate.
A counter that ratchets on refused calls is a defect this hook has already had once. Placement
decides this: `handle_pre_tool` commits `aggregate_reads`, `agent_reads` and `read_streak`
(`~:3393-3395`, inside the counting block at `~:3323-3450`) before it reaches the reflex chain
(`~:3555-3569`), and `READ_TOOLS` includes `Bash`. A block put where the warning is today
would fire after the counters had already moved. So the reader check runs in the `Bash` branch
**before the counting block**, and returns straight after `emit_block`, the way the streak
block does. Because it returns, the old `fire_reader_reflex` chain never runs for a blocked
call, and stdout carries one JSON object.

## Testing

New `tests/test_reader_block.py`, driving the real `hooks/cost-discipline.py pre-tool` with
harness-shaped payloads on stdin, following `tests/test_read_block_tier.py`:

| case | expected |
|---|---|
| main, one read per family (6) | `decision: block`, reason names the family's reader |
| `pup` read | reason names `datadog-reader` |
| same call with `agent_type` set | no output |
| main, a write per family | no block |
| `gh api -X POST …`, `gh api -X DELETE …`, `gh api … -f body=x`, `gh api graphql -f query='mutation{…}'` | no block |
| `gh api repos/o/r/pulls`, `gh api graphql -f query='{ viewer { login } }'` | block |
| `git commit -m "docs: kubectl get pods"`, `echo 'kubectl logs x'`, a heredoc whose body has `kubectl get pods` | no block |
| `kubectl delete configmap top`, `kubectl rollout restart deploy/x && kubectl get pods` | no block |
| `kubectl --context=x -n ns get pods` | block |
| `cd ~/repo && gh pr list`, `GH_REPO=o/r gh pr list`, `gh -R o/r pr view 5`, `gh --repo o/r pr list`, `gh search code foo --owner o` | block |
| `vault read secret/x`, `vault kv get secret/x` | no block |
| reader file absent (temp agents dir), per family (6), `pup` via `datadog-reader.md` | no block |
| `CC_DISCIPLINE_BLOCK=0` | no block |
| blocked call per family (6), then state read back | streak and aggregate unchanged |
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
