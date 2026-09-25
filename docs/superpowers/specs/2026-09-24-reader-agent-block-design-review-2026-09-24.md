# Adversarial design review — reader-agent block

Target: `docs/superpowers/specs/2026-09-24-reader-agent-block-design.md` at `6a6cf55`.
Date: 2026-09-24. Six reviewers, one verify pass per reviewer. The target was not edited.

**Central claim as reviewed:** a PreToolUse warning (`emit()` → `systemMessage`) does not reach
the model while a block reason does. So turning `READER_REFLEX` into a first-call block for six
CLI families in main sessions will route those reads to reader agents without blocking anything
that should pass.

**Summary:** the premise holds, though the spec cites the wrong evidence for it. The design
breaks because it reuses detectors that were built for a warning. Once they gate a block, they
refuse writes, miss common read forms, and one family maps to a reader name the code does not
know. None of these defects can be seen from the spec's own test table.

## Controls

| # | Angle | Model | Control | Reached? |
|---|---|---|---|---|
| 1 | Break the central claim | opus | the "reached the model" row comes from another hook with another envelope | yes |
| 2 | Unstated assumptions | opus | one class of call gets more expensive and is not measured | yes |
| 3 | Test strategy | sonnet | the origin/main expectation is wrong for some block cases | yes (gcloud, vault) |
| 4 | Fact-check | sonnet | one evidence row is not an instance of the generalised mechanism | **no**, see note |
| 5 | Product boundary | sonnet | done depends on an operator decision not yet made | yes (plugin update and restart) |
| 6 | Internal contradictions | haiku | one "Passes silently" bullet is not silent | yes |

On reviewer 4: it named the 18:41 publication-gate row, which is a valid finding and the same
one as reviewer 1's control. But it listed the three `emit()` rows as clean. Reviewer 1 and the
verify pass showed that the 17:55:00 row is `PostToolUse:Read`. Reviewer 4's own findings (4.3,
4.4) were each verified independently and are CONFIRMED, but treat that reviewer's "found clean"
list with caution.

## Verify pass

- 31 findings were raised; after merging duplicates across reviewers, 21 were distinct.
- **14 CONFIRMED, 3 PLAUSIBLE, 4 REFUTED and dropped.**
- Wherever a finding concerned detection, the verifiers ran the real detection code (the angle
  1 and 2 verifiers used `handle_pre_tool`).
- A verifier could not see earlier verifiers' results. Duplicates were merged here, afterwards.

## Surviving findings, most severe first

### F1 — HIGH — CONFIRMED — `gh api` writes are classified as reads
- Angles 1 and 2. Code: `hooks/cost-discipline.py:3524-3529`. Spec: `:44`, `:56`.
- The check is `is_gh_read = gh_subject == "api" or (...)`, which ignores the method.
- Executed: `gh api -X POST …/comments`, `gh api -X DELETE …/refs/heads/old` and
  `gh api graphql -f query='mutation{…}'` all fire the same gh-reader reflex as `gh pr view 42`.
- Under the design, each of these is blocked from the first call and sent to gh-reader, which
  refuses writes.
- There is no way out. The block text hides the kill switch by design, and a `publish-grant
  github` window cannot help because this hook runs independently of the publication gate.
- The hook's own gh text (`:1197-1198`) already says `api -X POST/PATCH/PUT/DELETE` is allowed
  inline, so the code contradicts it today.

### F2 — HIGH — CONFIRMED — kubectl detection matches `kubectl <read-verb>` anywhere in the string
- Angles 1 and 2. Code: `:3485-3510`.
- Executed: each of these fires the kubectl-reader reflex:
  - `git commit -m "docs: explain kubectl get pods usage"`
  - `echo 'run kubectl logs later'`
  - `kubectl delete configmap top`
  - `kubectl rollout restart deploy/api && kubectl get pods`
  - `kubectl exec pod-x -- sh -c 'ps; top -b -n1'`
- As a warning this was noise. As a block it refuses commits, doc writes and kubectl writes.
- No row in the spec's test table (`:74-82`) covers a non-read command that merely mentions a
  read verb.

### F3 — HIGH — CONFIRMED — the vault verb set does not match what vault-reader can do
- Angles 2, 4 and 5. Spec `:48`.
- The spec lists `status, list, read, kv list, kv get, secrets list, auth list`.
- The script behind the reader offers exactly five operations: `status, auth-list, secrets-list,
  keys, list` (`~/.claude/scripts/vault-dev-read.sh:68-70,198,203,248`; `vault-reader.md:78`).
- `vault-reader.md:90-91` forbids `vault kv get` in any form, and the reader returns key names,
  never values.
- So a blocked `vault kv get` or `vault read` is sent to an agent that cannot serve it on any
  server. The spec also leaves out `keys`.
- The DEV-only line in the reason (`:63`) covers only the prod half of this problem.

### F4 — HIGH — CONFIRMED — counters are updated before the reader check runs
- Angle 1. Code: counters are committed at `:3393-3395`, inside `:3323-3450`. The reflex chain
  runs later, at `:3555-3569`. `READ_TOOLS` includes Bash (`:229`).
- If the block goes where the warning is today, every refused call will already have advanced
  the aggregate and the streak. That is exactly the ratchet the spec forbids at `:66`.
- The spec never says where the check goes.

### F5 — HIGH — CONFIRMED — `pup → datadog-reader` has no programmatic mapping
- Angle 3.
- `reader_roster()` (`:4021-4090`) returns bare name stems with no family key.
- `datadog-reader` appears only inside the text of `READER_REFLEX["pup"]` (`:1163-1175`) and in
  a comment at `:3462`.
- If the implementation derives the name as `f"{family}-reader"`, it looks for `pup-reader`, finds
  nothing, and pup is never blocked. The "reader absent" rule then makes that pass silently.
- A test fixture built the same naive way would stay green.

### F6 — MEDIUM — CONFIRMED — the delivery evidence comes from another hook and a mismatched row
- Angles 1 and 4. Spec `:12-23`.
- The "yes" row, 18:41:03, is `publication-gate.py` `emit_deny` (`:1103-1125`). Its envelope is
  `hookSpecificOutput.permissionDecision: "deny"` plus a `systemMessage`.
- The design uses `emit_block`, which writes the legacy top-level `{"decision":"block"}`
  (`:935-938`).
- The 17:55:00 "warning" row is `PostToolUse:Read`. Only 2 of the 3 warning rows are
  PreToolUse.
- The premise itself appears to hold, but it rests on unverified numbers. Reviewer 1 counted 791
  `is_error` tool results carrying the `emit_block` text "Read-discipline hard-block", across
  7,190 transcripts, the latest at 2026-09-24T10:22:13Z.
  - **NOT re-verified:** the verify pass searched only session `6efebd28`.
  - The spec should cite `emit_block` delivery directly, or switch to the
    `permissionDecision` envelope.

### F7 — MEDIUM — CONFIRMED — the origin/main arm expectation is wrong for gcloud and vault
- Angles 3 and 6. Spec `:47-48` vs `:84-86`.
- The spec expects all six block cases to fail on origin/main "with a warning in place of a
  block".
- gcloud and vault have no detection on main, so those two cases produce no output at all.

### F8 — MEDIUM — CONFIRMED — the kill switch is listed under "Passes silently" but is not silent
- Angle 6. Spec `:53` vs `:58-59`.
- The kill switch falls back to "today's warning", which is output.
- For gcloud and vault, which are new, there is no "today's warning" to fall back to, so what
  the switch does for them is unspecified.

### F9 — MEDIUM — CONFIRMED — gh and slack detection only look at the first token
- Angles 1 and 2. Code: gh `:3467` and `:3520-3529`; slack `~:3540`.
- Executed: none of these is detected:
  - `cd /repo && gh pr list`
  - `GH_PAGER= gh pr view 5`
  - `gh -R o/r pr view 5`
  - `gh --repo o/r pr list`
  - `GH_REPO=o/r gh pr list`
  - `gh search code foo --owner o`
- `CLAUDE.md` routes that last form, `gh search code`, to gh-reader by name.
- A blocked model can get past the block by adding a prefix. Meanwhile kubectl scans the whole
  string (F2), so the two families behave in opposite ways.
- The planned live proof, a bare `gh pr list`, cannot expose this.

### F10 — MEDIUM — CONFIRMED — the gcloud verb set is not "taken from the agent's description"
- Angle 4. Spec `:47`, `:50-51`.
- The frontmatter description never mentions `asset search-all-resources`.
- The body's list of allowed commands (`gcloud-reader.md:15`) is larger than the spec's set.
- `config get-value` is a certain gap. Whether the `logging logs|buckets|sinks`, `projects` and
  `services` forms slip through depends on how matching is implemented (PLAUSIBLE).

### F11 — MEDIUM — CONFIRMED — the named test precedent is in-process, not stdin
- Angle 4. Spec `:71-72`.
- `tests/test_read_block_tier.py:14-29` loads the module with importlib and calls
  `handle_pre_tool()` in-process, with `blocks_enabled` monkeypatched.
- No test in `tests/` drives `pre-tool` through subprocess stdin. `test_subagent_detection.py:13-18`
  uses the same in-process pattern.
- So a test written "following" the precedent will not be the black-box test the spec promises.

### F12 — MEDIUM — CONFIRMED — the live-artifact proof has no place in "done"
- Angle 5. Spec `:88-89`, `:94-99`.
- The proof needs the plugin update and a restart. That decision is still the operator's to make
  (handoff 2026-09-23, NEXT item 1).
- Nothing ties the proof to closing the task, so "merged" can pass for "done" without the block
  ever being observed.

### F13 — MEDIUM — CONFIRMED at code level — callers that cannot dispatch hit a dead end
- Angle 2. Code: `blocks_enabled` `:985-1014`. Spec `:36`.
- There are three exemptions: kill switch, sub-agent, Haiku main. None depends on whether the
  caller can use the Agent tool.
- A headless or SDK caller without Agent that runs `gh pr list` is blocked on every attempt.
- **Hedge:** how common such callers are was not measured. The same goes for whether `--agent`
  mains carry `agent_type`; if they do, the opposite happens and a whole population of mains is
  exempt.

### F14 — LOW–MEDIUM — CONFIRMED — the test table cannot fail on four plausible wrong builds
- Angle 3. Spec `:74-82`.
- (a) No row asserts that the reason omits `CC_DISCIPLINE_BLOCK`, although precedent tests exist
  at `test_read_block_tier.py:~109-119`.
- (b) "Reason names the reader" is an inclusion-only check. A generic reason listing all six
  readers passes every case.
- (c) The "counters unchanged" and "reader absent" rows are single cases, not one per family.
- (d) No row pins stdout to one JSON object. That gap is CONFIRMED. The double emission itself is
  PLAUSIBLE: the only precedent in the file returns right after `emit_block`, which would prevent
  it, and the spec does not say whether the block replaces the reflex chain.

### F15 — MEDIUM — PLAUSIBLE — small reads get more expensive, and today's one-shot exemptions disappear
- Angles 1 and 2.
- A first-call block adds one main turn plus a dispatch. That cost lands even on a read of about
  40 bytes, such as `gh pr view 5 --json headRefOid -q .headRefOid`.
- Averages from `CLAUDE.md`: about 1,777 chars returned per dispatch against about 936 per Bash
  call. These are fleet averages, not reader-specific.
- The spec's "Passes silently" list (`:53-60`) drops two exemptions today's text grants: the
  kubectl credential refresh (`:1150-1152`) and pup's single verification query (`:1166-1169`).
- The spec never says whether the Haiku-main carve-out (`:1014`) applies.
- Everything about cost here is unmeasured, which is why it is PLAUSIBLE rather than CONFIRMED.

### F16 — LOW — PLAUSIBLE — the review gate has no owner and no enforcement
- Angle 5. Spec `:91-92`.
- "Before merge" is stated, but nothing names who dispatches the review or what stops a merge
  without it.

### F17 — LOW — PLAUSIBLE — "three arms" is two runs plus a derived bucket
- Angle 6. Spec `:84-86`.
- The third "arm" is a set computed from the first two runs, not a separate run.
- `~/.claude/CLAUDE.md` defines the third arm as a run against the branch's own earlier commit.
  The spec never defines "arm".

## Refuted and dropped (4)

- **Rollout ships unrelated changes under one approval.** The spec already says this at `:98-99`.
- **`prompt=…` implies a realised prompt.** The ellipsis marks a template.
- **"Passes silently" for a missing reader vs the "no block" test row.** These describe the same
  behaviour.
- **The vault dead end could be avoided with a `VAULT_ADDR` check.** Vault is IAP-gated
  (`vault-reader.md:41-51`). No raw CLI reaches DEV or prod; access goes through a kube-context
  port-forward. A `VAULT_ADDR` check would rescue no call.

## NOT SETTLED (gathered from reviewers)

- The method behind "hook_system_message is absent from the model's context". Reviewers
  confirmed only the record types.
- Whether the legacy `{"decision":"block"}` envelope will keep being honoured. It is deprecated,
  and works today.
- Whether Workflow `agent()` steps, headless mains and `--agent` mains carry `agent_type`.
- The new gcloud and vault detectors do not exist yet, so nothing about them could be executed.
  They probably share the first-token weakness from F9.
- An anomaly nobody investigated: at 17:55:00 a `PreToolUse:Read` streak warning is recorded as
  `hook_non_blocking_error`.

**Examined and found clean:**
- the kill-switch and sub-agent exemptions in `blocks_enabled`
- `reader_roster`'s `is_file()` check and name regex
- `kubectl --context=x -n ns get pods` is detected
- gh writes outside the allowlist (`pr create`) are not flagged
- the "Not in scope" section
- all identifiers exist; there are 8 reader files; installed v0.18.2 vs repo v0.19.1
- the "reader absent" row is writable via `monkeypatch HARNESS_DIR`
  (`test_reader_roster.py:44-60`)
