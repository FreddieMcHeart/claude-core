---
name: delegation-discipline
description: Use BEFORE any bulk read, sub-agent dispatch, or long output emission on ANY multi-step task. Triggers at task start, before every Bash/Read/Grep/Glob streak, before every Agent dispatch, when a session crosses ~3 MB JSONL or ~50 tool calls, when about to emit >4k tokens of prose, when about to Read→Edit→Read the same file, and on phrases like "read all the files", "explore the codebase", "summarize this repo", "walk me through", "write me a writeup". Also BEFORE every Workflow tool invocation (see references/workflow-authoring.md); BEFORE any compaction, handoff, or end-of-session knowledge-base save; the moment a required step's input becomes complete while the foreground carries on without it; and whenever a dispatch is still in flight and you are tempted to do its work inline.
last_validated: 2026-04-24
validated_against: claude-opus-4-7 (main), claude-sonnet-4-6 (sub-agent)
notes: Word caps (80 structural / 150 semantic / 200 RLM) ablated 2026-04-24 at N=2 (Kafka semantic + terraform structural); task-shape-aware caps added to references/scout-first.md. See docs/brain/claude-code-postmortem-2026-04-23.md for the empirical basis. Re-validate after any change to Anthropic sub-agent dispatch overhead or cache semantics.
scope: core
---

# Delegation Discipline

<EXTREMELY-IMPORTANT>
If there is even a 1% chance this task involves bulk reads, sub-agent dispatch, or long output emission, you MUST apply this skill BEFORE any other tool call. This is not optional. Rationalizations like "just one more peek", "it's faster to read it myself", "I'll delegate after this file" are the exact patterns this skill exists to prevent — most wasted cost in our cost audits came from sessions that *felt* light-touch and racked up dozens of main-agent reads.

The only valid skip condition: the user's message is pure chat with no tool action implied ("thanks", "ok", "got it").
</EXTREMELY-IMPORTANT>

**Core principle:** Delegate every bulk-reading, bulk-querying, or bulk-writing action to a sub-agent when the threshold pays off. Main-agent tokens are the most expensive tokens in the session; sub-agent results ride in summarized. Hand-off cost is ~20k tokens fixed — below that threshold, inline. Above, delegate.

This skill is the companion to `models-router`: `models-router` picks the tier **and the effort level** (Haiku / Sonnet / Opus × `low`…`max`); this skill picks *whether and when to delegate at all*. Apply both.

## The three questions — ask in order, before any tool call

1. **Am I about to perform a bulk read / query / long-output operation?**
   - Structure discovery (`ls`, `find`, `tree`, `wc -l`) → use `Glob` or delegate to Haiku. Never run in main.
   - Reading 3+ files in a row to build a mental model → Haiku sub-agent, ask for <200-word summary.
   - Grep expected to return >50 lines → narrow the pattern first, or delegate.
   - Verbose tool output (Slack thread JSON, `bq` results, `gcloud/aws/kubectl` list output) where the goal is *summary* → delegate.
   - About to emit >4k output tokens of prose in main → delegate composition to Haiku/Sonnet sub-agent with `Write` target path.
   - Details: `references/when-to-delegate.md`

2. **Have I hit a discipline threshold?**
   - 4 consecutive read-only tool calls (`Bash` / `Read` / `Grep` / `Glob`) in main without an `Agent` call in between → STOP. Next action must either write/edit something concrete, or dispatch a Haiku agent to take over reading.
   - About to `Read → Edit → Read → Edit` the same file → STOP. Read once, collect all pending edits in conversation as a structured list, apply as one multi-`Edit` pass or one `Write`.
   - Session has crossed ~3 MB JSONL or ~50 tool calls, or is transitioning between phases (plan → execute → document) → `/clear` and re-prime from the plan file.
   - Details: `references/hard-rules.md`

3. **Am I about to dispatch a sub-agent?**
   - Scout first: grep `docs/` for paths → pass as `Start here:` hints; if no wiki paths, dispatch a `file-finder` (Haiku) *first*, then the real agent with the paths it found.
   - Never dispatch a Sonnet/Opus agent with "find X in the codebase." That's Haiku work.
   - Always include in the prompt: specific repo + directory, `Start here:` file paths, expected deliverable in one sentence, word cap (80 for structural scouts, 150 for semantic scouts, 200 for RLM scouts — see `references/scout-first.md` for when each applies).
   - Details: `references/scout-first.md`

## Worked example

Task: *"Walk me through how our auth flow works across the platform."*

- Question 1 (bulk read): yes — this needs reading across 2+ repos / 5+ files. Delegate.
- Question 2 (discipline): not yet at threshold, but will hit it fast if reading in main.
- Question 3 (dispatch): yes. Scout-first: grep `docs/platform/services/*auth*.md` → pass to a Sonnet Explore agent as `Start here:` paths, cap at 80 words summary per repo.

**Anti-pattern caught:** Main agent reads 8 auth files sequentially, emits a 5k-word writeup, blows the session cost budget on a routine explanation. This skill forces the delegation at Question 1.

## The other failure mode: over-delegation on an Opus 5 main

Everything above guards *under*-delegation, which was the dominant waste pattern through Opus 4.8 — that model under-reached for sub-agents and had to be pushed. **Opus 5 reaches for them readily**, so on an Opus 5 main the failure mode inverts and this skill needs a ceiling as well as a floor.

A sub-agent costs more than its own tokens: it re-establishes context, re-explores ground the caller already covered, reports back, and then the caller reads the report. Delegate when that overhead is clearly repaid — not by reflex.

**Do NOT delegate:**
- Work you could finish yourself in a handful of tool calls — a few reads, a couple of edits, one narrow search.
- Review or verification of your own work. That belongs in the main loop; a verifier sub-agent is usually redundant on Opus 5, which self-verifies.
- One modest job sliced across several parallel agents. Parallel fan-out is for genuinely independent tracks, not for splitting a single task.

**Do delegate:** genuinely independent, parallelisable tracks; wide multi-file or multi-repo investigation; anything whose verbose output would otherwise flood main's prefix.

**When you do delegate:** brief precisely the first time (avoid dispatch → wait → re-brief), commit to the result (never re-derive a sub-agent's findings yourself), keep spawn counts low, and send independent dispatches in one message so they run concurrently.

### This does NOT relax the streak rule

The 4-consecutive-read rule and this ceiling answer **different questions**. Reading one as licence to break the other is the trap:

| Rule | Question it answers | Axis |
|---|---|---|
| 4-read streak → delegate or write | *Should this next read happen in main at all?* | whether |
| Over-delegation ceiling (above) | *How many agents for this task, and for what?* | how many |

"I could finish this in a handful of tool calls" is a reason to keep the *work* in main — and simultaneously a reason the streak rule still prices those calls, because inline reads on an expensive main are exactly what it exists to catch. The ceiling governs fan-out width; it never raises the streak floor.

**When both fire, take the streak rule's other branch — but only the streak tier
accepts it.** Say you need five reads to land one edit. At read 4 the streak rule
demands *delegate or write something concrete*, and the ceiling says don't
delegate this. So write: make the edit you have been gathering context for, then
continue reading. If you conclude that both rules block you at this tier, you
have mistaken the ceiling for a licence to keep reading.

**That escape does not exist at the session tier, and this file claimed it did.**
The enforcing hook has two independent blocks with different reset semantics, and
a write clears only one of them:

| tier | fires at | reset by write? | reset by dispatch? | reset by `/clear`? |
|---|---|---|---|---|
| streak (`STREAK_BLOCK_THRESHOLD`) | 10 consecutive inline reads | **yes** | yes | yes |
| session aggregate (`AGGREGATE_BLOCK_THRESHOLD`) | 40 inline reads in the session | **no** | yes | yes |

The hook says so in the message it prints when the aggregate tier fires: *"A
write does NOT reset the session aggregate — only a dispatch or /clear does."* So
"write something concrete" — the remedy this section named — is **inert against
the aggregate block**. Take it and the next read is refused again, with the
counter unchanged.

Before the 2026-07-31 standing grant that made read-and-analysis dispatch
freely available, this composed into a genuine dead end: aggregate blocking,
write inert, dispatch read as forbidden by the ceiling above, and only `/clear`
left — which is destructive, not a remedy. Zero non-destructive actions, not one.
**The grant is what restores the exit, so the ceiling must never be read as
forbidding a dispatch that a block has just demanded.** The ceiling governs how
WIDE you fan out on work you chose to delegate; it has nothing to say about a
single scout dispatched to clear a block.

*Why this correction is in the file rather than in a commit message:* the old
sentence was a confident universal — "always ... never zero" — asserted over two
mechanisms after checking one. A rule that names an escape the enforcing code
does not honour is worse than a rule with no escape, because it sends the reader
to the one door that is painted on.

## Five rules for WHEN and WHETHER, once the how-much question is settled

Everything above answers *how much*. These answer *when to dispatch*, *what is
pre-authorised*, and *what to do while a dispatch is already in flight*. Each is
a rule plus the measurement that produced it.

### 1. Read-and-analysis dispatch is pre-authorised. Writes are not.

A sub-agent whose product is OUTPUT rather than MUTATION — search, inspection,
measurement, summarisation — needs no per-dispatch permission. An EDIT-capable
dispatch is an ordinary delegation decision: permitted, not pre-authorised, and
it clears the ceiling above on its own merits.

**Scope this by ACTION, never by model tier.** Written first as "cheap models for
reads", which fixed one instance and left the class: a judgment-heavy read still
needed a permission round-trip, so the remedy cost more than the reading it was
meant to save. **A cost-saving rule whose remedy requires permission is a rule
that will not be used.** Check any new guard against that — does its remedy need
something the agent cannot grant itself?

**This is not licence to wrap everything.** Measured over one session's whole
transcript (~2,150 tool calls; the table is in this repo's `ROADMAP.md`, under
the 2026-07-29 measurement): a dispatch returns **~1,777 chars on average — more
than an average `Bash` call at ~936**. Wrapping one cheap command in a scout is a
net loss. Break-even is per *material*, not per call: a scout pays when the
volume it must examine greatly exceeds the answer it returns, and loses when it
wraps a one-line command.

**So the discriminator is "how much would I have to look at to answer this?"** —
not "is this a read?" High ratio of material-examined to answer-returned →
dispatch; low ratio → inline. The same output also costs more the earlier it
lands in a long session, because it is re-billed as a cache read every
subsequent turn.

**Scout prompts: demand the citation separately from the claim.** Structure
(path, line, the literal quoted line) is cheap to verify; interpretation is where
fabrication lands — observed twice in one day from two different scouts, each
returning a correct file list with an invented reading. Give the scout an
explicit place to write "not settled by the commands I ran": a scout with nowhere
to record uncertainty will record certainty. Where you already know one expected
item, withhold it as a completeness control.

### 2. A long analysis goes to a scout at its START, never when a block forces it

When the work is recognisably a long analysis — a corpus sweep, a code survey
across several modules, a knowledge-base read, a "what does this system currently
do" question — dispatch BEFORE the first inline read, not after the tenth.

**The failure this names is TIMING, and it is invisible because every other rule
was satisfied.** Measured on a session that read inline until the hard block
fired at exactly 40, then dispatched — the remedy the block exists to force.
Nothing was over-read by the ceiling's standard; nothing was fanned out too wide.
The dispatch simply happened at the worst moment: after the context had already
been paid for. **The scout was the right instrument the whole time and was
reached for as a penalty.**

This does not loosen the break-even in rule 1, and reading it that way is the
obvious error. Wrapping a one-line command in a scout is still a net loss. What
this adds is *when* to apply that test — at the moment you recognise the shape of
the work, not partway through it. **A hard block is not a trigger; it is evidence
the trigger was missed.** If one fires, say so plainly in the report: an
over-read nobody counts is an over-read nobody can stop.

### 3. A required step whose input is ready goes to the background NOW

Scope: every other rule here is scoped to VOLUME. This one is scoped to LATENCY.

**Trigger — three conditions, all required:** the step is required anyway; its
input is complete now; the foreground does not need its output to continue. A
code review, a long verification run, a corpus replay, a CI wait. Your report
needs the review; the review does not need your report.

**Why the other rules cannot catch it:** the floor asks *have you read too much
inline*, the ceiling asks *have you fanned out too wide*. Both measure HOW MUCH;
neither has a slot for WHEN. A session can satisfy both while a ready, mandatory
step sits idle for an hour — measured on a session that left its mandatory code
review until the very end, having had a reviewable diff for most of the session.

**This does not argue with the ceiling.** Same single dispatch, moved earlier: no
extra agents, no wider fan-out, no additional cost. A proposal that ADDS
dispatches rather than re-timing one is a ceiling question and is not authorised
here.

**It deliberately does NOT say "parallelise independent work."** Rejected
example: writing tests concurrently with the implementation they cover. Under
TDD the test drives the implementation; running them together means the test
drives nothing, and both come out of one mental model in one sitting, agreeing by
construction. Being well-specified upfront is exactly the condition that makes
them agree for the wrong reason. Independence — a different agent that has not
seen the implementation — is what buys anything there, not concurrency.

### 4. A dispatch and doing it yourself are ALTERNATIVES, not a hedge

Once a scout is dispatched, that reading is DELEGATED. Do not then do the same
reading inline "while waiting". Either wait for the reply, or decide the scout is
no longer needed and say so — never run both branches to completion.

**Measured instance:** a scout was dispatched to read one table row from an index
file. It was slow (457s); the work was needed, so the row was read inline and
edited. The scout then returned a correct, complete, verbatim answer describing
the file **as it was before the edit** — 118,919 tokens for a result already
superseded by the dispatcher's own work.

**No rule above can catch it.** The floor, the ceiling and the latency rule are
all satisfied — one scout, dispatched immediately, well inside every count. The
waste is that the same work was performed twice, and nothing measures duplication
between a dispatch and its dispatcher.

**Same defect as "do not edit files while a measurement is in flight", from the
other side.** A dispatch puts a claim on a subject; until it returns, that
subject is neither yours to read nor to edit.

**The impatience is legitimate; the hedge is not.** Make the choice once, out
loud: **wait**, and say you are waiting — cheapest when the reply feeds the next
step; or **take it back**, saying so *before* touching the subject ("the scout is
slower than the work, I am reading it inline and will discard its reply"), and
then actually discard it.

**The tempting middle to avoid:** treating a returning scout as corroboration of
work already done. It corroborates nothing — it read an earlier version, so
agreement is expected and disagreement is ambiguous between drift and error. **A
measurement taken before your change is not a check on your change.** When a
superseded reply arrives, say in one line that it was superseded and what it
cost.

### 5. The pre-compaction knowledge-base check is ALWAYS dispatched

Before any compaction, handoff, or end-of-session save, the reconnaissance pass —
what is genuinely new, which existing page this session advanced or invalidated,
what the store's conventions are — goes to a sub-agent. Every time.

**It is rule 1's volume argument at the WORST POSSIBLE MOMENT.** The check sweeps
an entire vault — on the order of a hundred files — to produce about a page of
answer, landing exactly when the prefix is largest and about to be re-billed into
a summary. Measured on one such day, cache-read was **96% of every token spent**
in both rate-limit windows. That makes it the most expensive AND most mechanical
read in the workflow — the worst thing to keep for yourself.

**The brief must demand three things the obvious version omits:**

- **The DENOMINATOR.** A recursive search can silently cover a fraction of the
  corpus — an ignore rule, a symlink, a path-anchoring difference. Have the scout
  report files covered, cross-checked against an independent enumeration. Without
  that, "no existing page covers this" is not a result.
- **BOTH DIRECTIONS.** NEW (durable insight not yet on disk) and STALE (a page
  this session advanced, corrected or invalidated). A check that only hunts for
  new pages leaves outdated claims standing, and a stale claim is worse than an
  absent one.
- **THE STORE'S GIT STATE, before anything is written.** On one run this stopped
  a bad write: the scout reported the vault behind its remote AND ahead with
  unpushed local work, the local commit sharing a title with one of the missing
  remote commits at a different sha — duplicate work, another session plausibly
  mid-edit. **The save step is allowed to come back BLOCKED; reporting it blocked
  is a correct outcome, not a failure to finish.**

**How to apply:** dispatch as the FIRST action of the step, before writing any
summary text. If blocked, record what is OWED and where each finding belongs, so
the next session writes those pages instead of rediscovering them.

## Common mistakes

| Thought | Reality |
|---|---|
| "Opus 5 handles delegation well, so delegate freely" | Opus 5 over-delegates by default. Each agent re-establishes context and reports back, then you re-read the report. Cap the fan-out; see the section above. |
| "I'll have a sub-agent double-check this" | Opus 5 self-verifies. A verifier sub-agent is usually redundant — keep verification in the main loop. |
| "Just one more peek — it's faster than dispatching" | That's the 4th peek. The streak rule exists to break this cycle. |
| "I already read 3 files, the 4th is free" | No — the 4th is the one that pushes main-agent context past the sub-agent overhead break-even. Delegate NOW. |
| "The sub-agent won't find what I'd find" | Then your prompt is too vague. Re-prompt with specific paths and a question, don't re-read in main. |
| "I'll just edit this doc one section at a time" | Every `Edit` invalidates the file's cache. Read once, batch edits. 80 re-reads of one doc = ~$15–25 wasted (2026-04-20 cost audit). |
| "I can squeeze a bit more out of this session before /clear" | The prefix cost on every subsequent turn compounds. `/clear` at phase boundaries is always cheaper after ~50 tool calls. |
| "The user wants a long writeup in the response" | Writeup should be written to a file by a sub-agent, referenced in the response. Main emits pointers, not prose. |
| "It's a simple search, Grep is fine in main" | Check the expected result size. >50 lines → Haiku. <50 → fine. |
| "This is quick and one-off" | Quick one-offs on Opus main cost ~1.7–2.5× a Sonnet sub-agent and 5× a Haiku one — before counting the reasoning tokens an Opus turn spends and a scout doesn't. Delegate anyway. |

## Escalation rule

If a Haiku sub-agent returns shallow/incomplete output, escalate the model **and** refine the prompt (what was missing, what to focus on). A better model with the same vague prompt produces the same vague result. Chain caps at Sonnet for delegation work — Opus sub-agents are rarely justified for delegation (they're for cross-repo architecture synthesis, not bulk reads). If Sonnet also returns shallow output, decompose the task into smaller sub-prompts rather than escalating further.

## Sub-skill references

- `references/when-to-delegate.md` — full when/when-not table + doc-gen delegation
- `references/hard-rules.md` — streak discipline, edit-loop discipline, session-scope discipline
- `references/scout-first.md` — sub-agent prompt construction + the scout-first procedure
- `references/workflow-authoring.md` — cost-discipline scaffold for Workflow scripts (gate/prime/budget-cap/backflow). MUST be consulted before ANY Workflow tool call — curated or improvised (ultracode).

Rationale, audit numbers, and cost data: the measurements live in the wiki, not in this
package — `docs/core/brain/claude-core/fan-out-cost-is-prefix-not-output-2026-07-27` (why
fan-out spend is ~73% prefix rather than output) and
`docs/core/brain/claude-core/measuring-interventions-controlled-ab-2026-07-20` (the
controlled A/B behind the routing numbers, including why a sub-agent screen is biased
toward the cheaper tier).

This sentence previously pointed at a `README.md` in the package root. That file **never
existed** — `git log --diff-filter=ADR` over the path returns nothing across the whole
history. Its twin in `models-router/SKILL.md` was fixed first and this one was reported
clean by mistake: the two sentences are worded differently (`cost data` here, `model
pricing` there), so a grep for one misses the other, and a `tail -4` misses this one
because it sits mid-file. Two near-identical defects with non-identical text is why the
second survived the fix for the first.

## Related skills

- `models-router` — picks the model for delegation (haiku/sonnet/opus). Invoke BEFORE every Agent dispatch.
- `llm-cost-optimizer` — generic LLM cost engineering patterns (model routing, prompt caching, output controls, budget envelopes). This skill enforces *Claude Code session* delegation discipline; `llm-cost-optimizer` is the broader pattern library — invoke it when building or reviewing AI features where the delegation principles also apply.
- `claude-cost-audit` — measures how well delegation discipline is actually being followed across past sessions.
