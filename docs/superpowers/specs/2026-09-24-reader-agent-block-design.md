# Reader-agent block — design

Date: 2026-09-24. Status: design approved in chat; revised after the adversarial review
(`2026-09-24-reader-agent-block-design-review-2026-09-24.md`) for all seventeen findings
(F1–F17).

## Problem

When the main session runs a read-only CLI that has a dedicated reader agent
(`kubectl logs`, `gh pr view`, …), the output lands in the main context at main-model
prices. The hook already detects four such CLIs in `READER_REFLEX` and answers with a
warning. That warning does not change behaviour, and the reason is measured, not guessed:

| time (UTC, 2026-09-23, session `6efebd28`) | event | hook output | reached the model? |
|---|---|---|---|
| 18:44:52 | `PreToolUse:Bash` | `emit()` warning, `STOP — dispatch gh-reader` | no |
| 18:45:12 | `PreToolUse:Bash` | `emit()` warning, third inline gh read | no |
| 17:55:00 | `PostToolUse:Read` | `emit()` warning, oversized tool result | no — a different event, not evidence for PreToolUse |

**The warning half:** n=2 `PreToolUse` warnings in one session. Both are recorded as
`attachment/hook_system_message`; neither was in the model's context, and in both cases the
gh output arrived as a normal tool result straight after. So `emit()` → `{"systemMessage": …}`
on `PreToolUse` reaches the UI and not the model. The warn tier's delivery is tracked
separately (fleet #121) and is out of scope here.

**The block half, for the emitter this design actually uses.** `emit_block` writes the legacy
top-level `{"decision":"block","reason":…}` (`hooks/cost-discipline.py:935-938`). Its existing
callers are the streak and aggregate blocks, whose reason starts `Read-discipline hard-block`.
Counted 2026-09-24 over every `*.jsonl` under `~/.claude/projects/` (8,381 files): **1,278
`tool_result` blocks with `is_error` carry that text, in 333 files**, the latest at
2026-09-24T13:58:22Z, 5 of them in session `6efebd28` itself. The reason reaches the model as
the tool error. The count is of transcript records, not unique events. Copied and resumed
transcripts are not deduplicated, so it shows that delivery happens, not how often. Method: parse every line; for each
`message.content[]` block with `type == "tool_result"` and `is_error` true, count it if its
content contains the needle above.

The publication-gate deny at 18:41:03 also reached the model as a tool error. But it comes
from a different hook with a different envelope
(`hookSpecificOutput.permissionDecision: "deny"`, `~/.claude/hooks/publication-gate.py:1103-1125`),
so it is not the evidence for `emit_block`. The legacy envelope is deprecated upstream and is
honoured today. If it stops being honoured, the streak and aggregate blocks break with it, and
the fix belongs to all three callers at once. It is not a reason to fork this one now.

## Decision

Approach A: turn the existing reader reflex from a warning into a block, and add the two
CLIs that have a reader agent but no detection. Rejected: a separate hook (a second
classifier for the same commands would drift from the first) and a `wraps:` field in agent
frontmatter (a new contract across eight files for a ninth agent that does not exist).

## Behaviour

**Blocked, from the first call:** a `PreToolUse` `Bash` call where

1. `blocks_enabled(payload)` is true — the same gate the streak and aggregate blocks use
   (`hooks/cost-discipline.py:985-1014`): kill switch off, not a sub-agent
   (`is_subagent_call`), and the main model is not Haiku. Blocking a Haiku main into a Haiku
   reader buys nothing and costs a turn; and
2. the command is a read in one of six families, and
3. that family's reader agent is installed: its name from `READER_FOR_FAMILY` (below) is in
   `reader_roster()`, which lists `~/.claude/agents/*-reader.md`.

| family | reader | read set |
|---|---|---|
| `kubectl` | `kubectl-reader` | `_KUBECTL_READ_VERBS` (`get`, `describe`, `logs`, `top`) |
| `gh` | `gh-reader` | `is_gh_read` subjects × `view`, `list`, `diff`, `checks`, `status`; plus `search code`; plus `api` only as a GET (below) |
| `pup` | `datadog-reader` | existing pup-ro detection |
| `slack-cli.sh` | `slack-reader` | existing read subcommands |
| `gcloud` | `gcloud-reader` | NEW: the final verb `list`, `describe` or `get-iam-policy` in any group (so `logging logs list`, `logging buckets list`, `logging sinks list\|describe`, `projects list\|describe`, `services list`, `auth list`, `config list`); plus `logging read`, `config get-value`, `asset search-all-resources` |
| `vault` | `vault-reader` | NEW: `status`, `secrets list`, `auth list`, `list`, `kv list` |

Where the read sets come from:

- **vault** — the operations `~/.claude/scripts/vault-dev-read.sh` actually serves (`status`,
  `auth-list`, `secrets-list`, `list`, `keys`; `vault-reader.md:78`). `vault read` and
  `vault kv get` are deliberately NOT in the set: the reader returns key names and never a
  value (`vault-reader.md:90-91`), so blocking a value read would route it to an agent that
  must refuse it. Those calls pass.
- **gcloud** — the allowed shapes in `gcloud-reader.md:15` (Hard boundaries), not the
  frontmatter description, which names none of them. `--help` is left out: it prints usage and
  touches no cloud state, so there is nothing to delegate.

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

**Kill switch, which does not pass silently:** with `CC_DISCIPLINE_BLOCK=0` (the existing
advisory-only switch) no call is blocked. A read the classifier would have blocked gets the
`READER_REFLEX` warning instead, which is today's behaviour. `READER_REFLEX` gains `gcloud` and
`vault` entries, whose text matches their block reason, so all six families fall back the same
way. That warning does not reach the model (fleet #121), so in practice the switch means
"advise only". As today, the block text does not mention the switch.

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

New `tests/test_reader_block.py`, with two layers:

- **In-process, for every case below.** Following `tests/test_read_block_tier.py:14-29` and
  `tests/test_reader_roster.py:44-60`, load the module with importlib, point `HARNESS_DIR` at a
  temp dir holding the reader files the case needs, and call `handle_pre_tool()` with a
  harness-shaped payload. Unlike the precedent, `blocks_enabled` is NOT monkeypatched: it is
  part of the behaviour under test. Only the model lookup it depends on is pinned.
- **One subprocess smoke test, new and without precedent in `tests/`.** It runs
  `python3 hooks/cost-discipline.py pre-tool` with the payload on stdin and `HOME` pointed at a
  temp dir that holds `.claude/agents/gh-reader.md`. For `gh pr list` it asserts exit 0 and
  exactly one stdout line, parsing to `{"decision":"block", …}`. It covers the argv and stdout
  path that the in-process layer cannot.

Cases:

| case | expected |
|---|---|
| main, one read per family (6) | `decision: block`; reason names that family's reader and none of the other five |
| `pup` read | reason names `datadog-reader` |
| every block case | reason does not contain `CC_DISCIPLINE_BLOCK` |
| every block case | stdout is exactly one JSON object (no reflex warning alongside) |
| Haiku main, a read per family (6) | no block |
| same call with `agent_type` set | no output |
| main, a write per family | no block |
| `gh api -X POST …`, `gh api -X DELETE …`, `gh api … -f body=x`, `gh api graphql -f query='mutation{…}'` | no block |
| `gh api repos/o/r/pulls`, `gh api graphql -f query='{ viewer { login } }'` | block |
| `git commit -m "docs: kubectl get pods"`, `echo 'kubectl logs x'`, a heredoc whose body has `kubectl get pods` | no block |
| `kubectl delete configmap top`, `kubectl rollout restart deploy/x && kubectl get pods` | no block |
| `kubectl --context=x -n ns get pods` | block |
| `cd ~/repo && gh pr list`, `GH_REPO=o/r gh pr list`, `gh -R o/r pr view 5`, `gh --repo o/r pr list`, `gh search code foo --owner o` | block |
| `vault read secret/x`, `vault kv get secret/x` | no block |
| `gcloud config get-value project`, `gcloud logging logs list`, `gcloud services list`, `gcloud projects describe p` | block |
| `gcloud --help`, `gcloud run deploy x`, `gcloud services enable x` | no block |
| `CC_DISCIPLINE_BLOCK=0` with a `gcloud` and a `vault` read | a warning, no `decision` |
| reader file absent (temp agents dir), per family (6), `pup` via `datadog-reader.md` | no block |
| `CC_DISCIPLINE_BLOCK=0` | no block |
| blocked call per family (6), then state read back | streak and aggregate unchanged |
| `vault` block | reason carries the DEV-only line |

**Three arms, three runs.** The work lands as two commits so that each one can be measured on
its own:

- **commit A**: the classifier replaces today's detectors, and it still only warns
- **commit B**: the block

The finished tests run against:

1. **`origin/main`** — the defect as it is today
2. **commit A** — the detection fixes on their own: mis-flagged commands (`gh api -X POST`,
   `git commit -m "… kubectl get pods"`, `kubectl delete configmap top`) stop warning, the missed
   forms (`cd … && gh`, `gh -R`, gcloud, vault) start warning, and every block case is still red,
   now with a warning in place of a block
3. **commit B** — everything green

Cases that pass in all three arms guard the change and are reported separately. What
`origin/main` must show for each block case, and why each reason is the right one:

| block case on `origin/main` | expected output | why |
|---|---|---|
| bare reads of `kubectl`, `gh`, `pup`, `slack-cli.sh` | a warning, no `decision` | detection exists and warns |
| `gcloud` and `vault` reads | nothing at all | no detection on main |
| `cd … && gh …`, `GH_REPO=… gh …`, `gh -R/--repo …`, `gh search code` | nothing at all | today's gh detection reads the first token only |

A case that fails on main for any other reason, such as an import error or a crash, is not
evidence. The "no block" rows show no block on every arm, because nothing blocks on main or on
commit A. So they guard commit B and demonstrate nothing about the defect. Commit A's arm is
where the detection rows are shown to change.

Real artifact: after install, one real `gh pr list` from a live main session must come back
as a tool error carrying the reason. That is the delivery proof the warn tier never had.

## Review

Hot-path code: the implementing session dispatches `feature-dev:code-reviewer`
(`model: sonnet`) once all three arms have run and before it opens the PR. It then reproduces
each finding by execution before fixing it. The PR body names the reviewer, lists the findings,
and says which were reproduced and which were refuted. A PR whose body carries no review
section is not ready to merge, and the operator, who merges, holds it to that.

## Rollout

The hook runs as the `claude-core-hooks` plugin, so a merge is not an install:
`claude plugin update claude-core-hooks@claude-core-local`, then a session restart. The
installed copy was v0.18.2 against v0.19.1 on 2026-09-24, so the update also brings in
unrelated changes already on main. Whether and when to update is the operator's decision.

## Definition of done

Fleet #120 closes only when all of these hold:

1. all three arms were run, with the results reported in the PR
2. the review section is present and every finding is reproduced or refuted
3. the PR is merged
4. the plugin is updated and the session restarted
5. the real-artifact `gh pr list` came back as a tool error carrying the reason

If the operator defers the update, #120 stays open and reads "merged, not installed". It is
never closed on 1–3 alone.

## Known costs and limits, accepted

- **Small reads cost more.** A first-call block adds one main turn plus a reader dispatch, even
  for a read that would have returned 40 bytes (`gh pr view 5 --json headRefOid -q …`). Today's
  pup text allows "a single verification query" inline (`:1166-1169`); the block removes that.
  This is the price of the operator's decision to block from the first call. It is not
  measured. The Haiku-main exemption above keeps the cheapest case, a Haiku main, from paying
  it. The kubectl credential-refresh exception in today's text (`:1150-1152`) is not affected,
  because `gcloud container clusters get-credentials` is not in the gcloud read set.
- **Callers that cannot dispatch.** A headless `claude -p`, an Agent-SDK loop or a scripted run
  without the Agent tool would be blocked with no way through. The hook has no reliable signal
  for this: whether the payload carries the tool list was not established. Such a caller runs
  with `CC_DISCIPLINE_BLOCK=0`, which is what the switch exists for. The switch is documented
  here and in the hook's docstring, never in the block text.
- **Mains that carry `agent_type`.** If the harness sets `agent_type` on a main session started
  with `--agent`, `is_subagent_call` exempts that whole population. Not measured. It fails open
  (no block, today's behaviour), not closed.

## Not in scope

- the `emit()` delivery defect for every other warning (fleet #121)
- MCP readers (`jira-reader`, `notion-reader`); this is `Bash` only
- a `wraps:` field or any change to agent files
