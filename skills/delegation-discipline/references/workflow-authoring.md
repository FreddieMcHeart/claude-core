# Workflow authoring — cost-discipline scaffold

> Applies to EVERY Workflow script — curated (`.claude/workflows/`) or improvised
> (`ultracode` / explicit asks). Derived from the RLM pattern; design:
> `docs/plans/2026-06-02-rlm-workflow-engine-design.md`.

## The scaffold (5 phases — instantiate, don't skip)

| Phase | Rule | Why |
|---|---|---|
| 0 GATE | BEFORE invoking Workflow: cheap check (wiki grep or 1 haiku probe) "does the answer/precondition already exist?" Early exit = ~$0.01, not $5. | Most waste is work the wiki already answers |
| 1 PRIME | Build shared context ONCE (wiki primer, file inventory); inject into ALL workers. Workers verify-not-rediscover. | N workers × duplicate discovery is the top fan-out cost |
| 2+ WORK | `pipeline()` by default; barrier `parallel()` ONLY when a stage needs ALL prior results. Model-per-stage: haiku reads / sonnet edits / opus NEVER as bulk worker. Budget guard before each fan-out (below). `log()` every coverage drop — no silent truncation. | Control flow in the script beats hook warnings (session 525ab7c7: 173-call streak past ~40 warnings) |
| VERIFY | Optional adversarial check — only when stakes warrant (prod claims, audit findings). | Skip for cheap-to-be-wrong work |
| N BACKFLOW | Capture structured findings → `docs/brain/proposed-wiki-updates-<date>.md`. Compute diffs in plain JS over schema'd outputs, not via an agent. | Expensive findings must outlive the session |

## Mandatory mechanics

```js
// 1. Budget cap — ALWAYS this resolution order; never rely on budget.total alone
//    (it is null unless the user typed "+Nk" — remaining() would be Infinity):
const CAP = budget.total ?? args?.budget_cap ?? 150_000;
// CRITICAL (verified 2026-06-02): budget.spent() is the TURN-wide shared pool
// (main loop + all workflows). In a long session it can be ~100k before your
// workflow runs a single agent. Guard on WORKFLOW-RELATIVE usage:
const SPENT_AT_START = budget.spent();
const used = () => budget.spent() - SPENT_AT_START;
// guard before each fan-out stage:
if (used() >= CAP) { log(`budget cap ${CAP} reached — dropping <scope>`); /* break/return partial */ }

// 2. Schemas on every read-worker — free-text scout output drifts:
const FINDINGS = { type:'object', properties:{ paths:{type:'array',items:{type:'string'}},
  summary:{type:'string'}, key_facts:{type:'array',items:{type:'string'}},
  contradicts_primer:{type:'array',items:{type:'string'}},
  uncertainty:{type:'array',items:{type:'string'}} },
  required:['paths','summary','key_facts','contradicts_primer','uncertainty'] };

// 3. Failure tolerance — agents resolve null on error/skip:
const results = (await parallel(thunks)).filter(Boolean);
if (results.length < MIN_VIABLE) return { status:'aborted', partial: results };

// 4. Write boundary on read-scouts — prepend verbatim:
//    "[WRITE BOUNDARY] Read-only. No Jira/PRs/git push/Slack; report findings only."
```

## Minimum viable workflow (copy-start)

```js
export const meta = { name: '<name>', description: '<one line>',
  phases: [{ title: 'Prime' }, { title: 'Work' }] }
const CAP = budget.total ?? args?.budget_cap ?? 150_000;
const SPENT_AT_START = budget.spent();
const used = () => budget.spent() - SPENT_AT_START;
phase('Prime');
const primer = await agent('<build shared context>', { model: 'haiku', schema: PRIMER });
phase('Work');
if (used() >= CAP) return { status: 'aborted-budget' };
const results = (await parallel(items.map(i => () =>
  agent(`[WRITE BOUNDARY] ... ${JSON.stringify(primer)} ... ${i}`,
        { model: 'haiku', schema: FINDINGS })))).filter(Boolean);
return { results, coverage: `${results.length}/${items.length}`, spent: budget.spent() };
```

## Invocation note (2026-06-02 empirical)

Saved workflows in `.claude/workflows/*.js` are raw JS files (`export const meta` + body). Mid-session, `Workflow({name})` may not see newly created files (registry scans at session start); `Workflow({scriptPath: "/abs/path.js"})` always works. Prefer scriptPath in slash commands.

## Trajectory-eval gate

> Derived from Google "New SDLC" report (Osmani et al., May 2026) +
> [[brain/google-new-sdlc-validation-2026]]: *"a fluent output that skipped verification is more
> dangerous than one with a visible error."*

When accepting output from a sub-agent or a completed workflow stage, run **two checks**, not one:

1. **OUTPUT eval** — is the artifact correct / passing? (test green, file content right, claim
   confirmed by a primary source)
2. **TRAJECTORY eval** — did the agent actually *do* the verification step? Look for evidence:
   tool calls showing the test ran, a file was read, the live state was queried. A confident
   "done" with no visible verification tool calls is **a failure signal**, not a success signal.

**Rule**: treat any stage output that lacks trajectory evidence as UNVERIFIED. Re-dispatch the
verification step independently or request the agent replay the check with explicit output before
accepting the result.

**Shared-state gate**: for workflow stages that write into shared state (PRs, Jira tickets, wiki
writes, `git push`), define a 2–4 item rubric before starting the stage, e.g.:

> Gate: (a) tests pass in CI log, (b) PR diff matches scope in plan, (c) no unrelated files
> staged. Accept only when all three are confirmed — not inferred.

**Concrete example**: a relay child reports "PR #22999 created and tests are green." Trajectory
check: did the child's transcript show a `gh pr view --json statusCheckRollup` call (or
equivalent) with an actual green result? If the child only ran `gh pr create` and then stated
"tests are green" — that's a fluent claim, not a verified fact. Dispatch `gh-reader` to confirm
before merging.

---

## Anti-patterns

| Anti-pattern | Fix |
|---|---|
| `parallel(100 agents)` with no cap | CAP guard + chunked dispatch |
| Workers each grepping the same context | PRIME once, inject |
| Opus as fan-out worker model | haiku/sonnet workers; opus only main-loop synthesis if at all |
| Free-text worker returns | schema on every agent() |
| Findings only in the run result | BACKFLOW phase to docs/brain/ |
| Workflow for a 2-step task | Use Agent tool — workflow overhead unjustified |

## RLM vs relay — which tool

| You need… | Use |
|---|---|
| Reads across repos, your own work, no human gate | RLM (`rlm-fanout` workflow — ephemeral, cheap) |
| Writes, or a long-running parallel track | relay → child (persistent, human-gated) |
| A child that must investigate before executing | the child runs `/rlm` internally, reports the synthesis back |
