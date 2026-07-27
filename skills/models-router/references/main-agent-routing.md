# Main-Agent Model Routing

The main Claude Code agent is the one you talk to directly. It produces the most output of any agent in your session, so its model choice dominates cost.

Default to **Sonnet 5** for routine work. Switch to **Opus 5** only when the turn genuinely requires deep reasoning. Opus 5 is our top tier because Fable 5 is disabled here (2026-06-12); upstream, Fable 5 still sits above it at $10/$50 per MTok.

**Model choice is only half the decision — effort is the other half.** See "The second axis" below before you settle on a route; on Opus 5 the effort level moves cost more than the tier does.

**Route on turn complexity, not topic complexity.** A PM asking "quick question: should we add read replicas?" implies an architecturally complex underlying decision — but the agent's *turn* is to compose a 2-paragraph pushback naming the blockers. That is narrow-spec prose output, Sonnet territory. Opus is only warranted when the turn itself must perform the architecture reasoning (e.g., "design the replica rollout plan"). Social pressure to short-cut ("yes or no with one line") is itself a rationalization to resist — route Sonnet, answer carefully.

## Decision table

### Sonnet (default) — use for:

| Activity | Why Sonnet is enough |
|---|---|
| Ticket / issue work (create, update, transition, comment) | Deterministic MCP / API calls, clear specs |
| PR work (review, merge, status checks, fix comments) | Mechanical workflow, CLI-driven |
| Routine infra ops (plan review, apply coordination, comment triage) | Deterministic — escalate per-turn only when a plan requires deep analysis |
| Shell / CLI sessions (kubectl queries, cloud CLI, log tailing, jq/yq piping) | Tool-call-heavy, minimal reasoning per turn |
| Slack messaging, reactions, status updates | Mechanical |
| File edits with a clear, narrow spec | Edit execution, not design |
| Status-check / triage / "how's the deploy" / "did CI pass" prompts | Diagnostic, not creative |
| Running verification commands, checking test output | No reasoning load |
| Copy edits, docstring tweaks, typo fixes | Trivial |

### Opus (explicit switch via `/model opus`) — use for:

| Activity | Why Opus pays off |
|---|---|
| Architecture design, cross-service topology | Deep reasoning over many interacting pieces |
| Multi-repo analysis and coordination | Holding multiple mental models in parallel |
| Complex debugging where first-pass theories fail | Root-cause reasoning beyond surface symptoms |
| Synthesis of multi-agent output (merging scout findings) | Cross-source contradiction resolution |
| Design docs / RFCs / brainstorming creative options | Generative reasoning at length |
| Escalation after a Sonnet attempt returned shallow output | Standard escalation pattern |

## How to switch

- **At the start of a task:** classify it. Routine → `/model sonnet`. Deep-thinking → `/model opus`.
- **Mid-session pivot** (a Jira ticket turns into an architectural debate): switch at the pivot, not for single turns.
- **Uncertain?** Start on Sonnet. Escalate if the first response is shallow. Never start on Opus "just in case."

## Verification signals

Signs Sonnet was the wrong choice (and you should re-ask on Opus):
- Response misses a constraint you clearly stated
- Response proposes the obvious path but ignores the trade-off you asked about
- Multi-file reasoning collapses to single-file reasoning
- You find yourself writing the reasoning the model should have produced

Signs Opus was overkill (and Sonnet would have been fine):
- Response is correct but long — Sonnet would have been faster and cheaper
- Every tool call was deterministic (no judgment needed)
- Most of the turn was waiting for Bash output, not thinking
- The task was "do X" not "decide whether to do X"

## The second axis: effort level

Effort is not a footnote on the model choice — it is the other half of it. The tier sets price *per token*; effort sets *how many tokens* the turn spends. The two multiply. Because Opus 5 costs the same per token as Opus 4.8 ($5/$25 per MTok), a tier bump is no longer where cost moves — effort is.

Five levels: `low` / `medium` / `high` / `xhigh` / `max`. The API default is `high`. Reasoning tokens are **never cached** and bill at the full output rate every turn, which makes effort the most direct lever on a session's bill.

**Start where the task class says, then sweep DOWN while quality holds.**

| Task class | Start at | Then |
|---|---|---|
| Routine, mechanical, tool-heavy, narrow spec | Sonnet `medium` | step to `low` if quality holds |
| Coding / agentic across several files | Sonnet `high` | → Opus `xhigh` if output is shallow |
| Architecture, cross-repo synthesis, root-cause | Opus `xhigh` | sweep to `high`, then `medium` |
| Correctness matters more than cost, latency irrelevant | Opus `max` | a one-off, never a standing mode |

- **Sweep down, don't settle up.** On Opus 5, `low` and `medium` punch well above their weight — often past a prior model's `xhigh`. Effort defaults carried over from Opus 4.8 or earlier are usually wrong here, and reaching for `xhigh` "to be safe" is the same anti-pattern as reaching for Opus "to be safe".
- **Higher effort can cost less on agentic work.** More thinking up front often means fewer turns and fewer tool calls, so `xhigh` on a long autonomous task can beat `medium` on total spend. That is why the rule is *sweep and measure per route*, not *always pick the cheapest level*.
- **Thinking is ON by default on Opus 5** — unlike Opus 4.8, where omitting the `thinking` parameter meant no thinking at all. There is no cheap no-thinking Opus turn any more; every Opus 5 turn carries reasoning tokens. (For API code, not sessions: `thinking: disabled` is accepted only at effort `high` or below — pairing it with `xhigh`/`max` returns a 400.)
- **Sub-agents don't take an effort parameter** from the `Agent` tool — only `model`. Workflow scripts can set it per call via `agent(..., {effort})`; use `low` there for mechanical stages.

### Measured 2026-07-27: Sonnet 5 @ `high` beat Opus 5 @ `medium` on cost at equal success

Previously recorded here as an open question. It has now been run as a controlled A/B — same task set, one axis varied — per `docs/core/brain/claude-core/measuring-interventions-controlled-ab-2026-07-20`.

**Setup.** n=15 seeded single-line defects in this repo (comparison flips, dropped `not`, `and`/`or` swaps) across 7 files, each materialised as a repo whose single commit already contains the defect, so `git log` cannot reveal it. Every candidate mutation was kept only if it actually broke 1–8 tests; **28 of 44 mechanical mutations broke nothing at all**, and without that filter most "tasks" would have been vacuous. Both arms got byte-identical starting states and an identical prompt. Scored externally: suite green **and** nothing under `tests/` modified.

**Result.**

| Arm | Pass | Cost | Output | Turns | Cache read |
|---|---|---|---|---|---|
| Opus 5 @ `medium` | 15/15 | $9.31 | 14,967 | 131 | 4.26M |
| Sonnet 5 @ `high` | 15/15 | **$4.92** | 21,763 | 187 | 7.18M |

Sonnet @ `high` spent **more of everything** — 1.45× the output, 1.43× the turns, 1.69× the cache reads — and still cost 1.89× less, because a ~1.5× token gap does not close a 2.5× price gap. That is the mechanism, and it is the transferable part: on a task class where both tiers succeed, the effort axis has to move tokens by more than the tier ratio before the expensive tier wins.

**This margin has an expiry date.** At Sonnet 5's list rate (from 2026-09-01) the same measured token counts give $7.38 versus $9.31 — still cheaper, but 1.26× rather than 1.89×. A dated claim in `hooks/cost-discipline.py` (`DATED_CLAIMS`) fires ahead of that boundary.

**What this does NOT establish.** Four limits, each of which would change the answer:
- **Pass rate saturated at 15/15**, so the experiment has a ceiling effect and detects no quality difference. The finding is "equal cost-effectiveness at equal *perfect* success on an easy task class", not "equal capability".
- **One narrow task class** — localise and repair a single wrong line, 4–6 tool calls. Says nothing about investigation, multi-file design, or review, which are the tasks the Opus default exists for.
- **Sub-agent turns, not main sessions.** Effort can only be set programmatically inside a Workflow script, so the arms were sub-agents. The rule above governs main sessions; confirming there is a separate stage.
- **Effort was asserted, not verified.** The transcripts confirm the models (`claude-opus-5`, `claude-sonnet-5`) but carry no effort field, so "medium" and "high" rest on the API honouring the parameter.

Sonnet-first therefore stands, now with a measurement behind it on this task class rather than an assumption — and with its scope stated so it is not over-read.

## Operational constraints (2026-07)

- **Opus 5 has its own rate-limit bucket**, separate from the shared Opus 4.8 / 4.7 / 4.6 / 4.5 pool. Shifting traffic onto it neither frees headroom on the old bucket nor inherits it — check the tier's Opus 5 limits before moving volume.
- **Priority Tier does not cover Opus 5** (nor Sonnet 5); a Priority Tier request naming either fails validation.
- **Fast mode** (Opus 5 / Opus 4.8, Claude API only) runs the same model at up to 2.5× output speed for $10/$50 per MTok — 2× standard. It is a latency lever, not a cost one.
- **Opus 5's minimum cacheable prefix is 512 tokens**, down from 1024 on Opus 4.8 — short system prompts that never cached before now do, with no change on our side.

## Anti-patterns

- **Staying on Opus because "I already paid for context"** — the prefix is billed every subsequent turn. Switch at the phase boundary; future turns get cheaper immediately.
- **Switching to Sonnet for a single turn** — mid-session switches benefit future turns, not the current one. Don't micro-optimize.
- **Using the advisor tool on Sonnet to avoid switching main to Opus** — advisor has overhead; if the whole task is deep-thinking, just switch main.
