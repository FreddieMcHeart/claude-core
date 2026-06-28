---
name: delegation-discipline
description: Use BEFORE any bulk read, sub-agent dispatch, or long output emission on ANY multi-step task. Triggers at task start, before every Bash/Read/Grep/Glob streak, before every Agent dispatch, when a session crosses ~3 MB JSONL or ~50 tool calls, when about to emit >4k tokens of prose, when about to Read→Edit→Read the same file, and on phrases like "read all the files", "explore the codebase", "summarize this repo", "walk me through", "write me a writeup". Also BEFORE every Workflow tool invocation (see references/workflow-authoring.md).
last_validated: 2026-04-24
validated_against: claude-opus-4-7 (main), claude-sonnet-4-6 (sub-agent)
notes: Word caps (80 structural / 150 semantic / 200 RLM) ablated 2026-04-24 at N=2 (Kafka semantic + terraform structural); task-shape-aware caps added to references/scout-first.md. See docs/brain/claude-code-postmortem-2026-04-23.md for the empirical basis. Re-validate after any change to Anthropic sub-agent dispatch overhead or cache semantics.
---

# Delegation Discipline

<EXTREMELY-IMPORTANT>
If there is even a 1% chance this task involves bulk reads, sub-agent dispatch, or long output emission, you MUST apply this skill BEFORE any other tool call. This is not optional. Rationalizations like "just one more peek", "it's faster to read it myself", "I'll delegate after this file" are the exact patterns this skill exists to prevent — most wasted cost in our cost audits came from sessions that *felt* light-touch and racked up dozens of main-agent reads.

The only valid skip condition: the user's message is pure chat with no tool action implied ("thanks", "ok", "got it").
</EXTREMELY-IMPORTANT>

**Core principle:** Delegate every bulk-reading, bulk-querying, or bulk-writing action to a sub-agent when the threshold pays off. Main-agent tokens are the most expensive tokens in the session; sub-agent results ride in summarized. Hand-off cost is ~20k tokens fixed — below that threshold, inline. Above, delegate.

This skill is the companion to `models-router`: `models-router` picks the tier (Haiku / Sonnet / Opus); this skill picks *whether and when to delegate at all*. Apply both.

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

## Common mistakes

| Thought | Reality |
|---|---|
| "Just one more peek — it's faster than dispatching" | That's the 4th peek. The streak rule exists to break this cycle. |
| "I already read 3 files, the 4th is free" | No — the 4th is the one that pushes main-agent context past the sub-agent overhead break-even. Delegate NOW. |
| "The sub-agent won't find what I'd find" | Then your prompt is too vague. Re-prompt with specific paths and a question, don't re-read in main. |
| "I'll just edit this doc one section at a time" | Every `Edit` invalidates the file's cache. Read once, batch edits. 80 re-reads of one doc = ~$15–25 wasted (2026-04-20 cost audit). |
| "I can squeeze a bit more out of this session before /clear" | The prefix cost on every subsequent turn compounds. `/clear` at phase boundaries is always cheaper after ~50 tool calls. |
| "The user wants a long writeup in the response" | Writeup should be written to a file by a sub-agent, referenced in the response. Main emits pointers, not prose. |
| "It's a simple search, Grep is fine in main" | Check the expected result size. >50 lines → Haiku. <50 → fine. |
| "This is quick and one-off" | Quick one-offs on Opus main cost 5× Sonnet sub-agent. Delegate anyway. |

## Escalation rule

If a Haiku sub-agent returns shallow/incomplete output, escalate the model **and** refine the prompt (what was missing, what to focus on). A better model with the same vague prompt produces the same vague result. Chain caps at Sonnet for delegation work — Opus sub-agents are rarely justified for delegation (they're for cross-repo architecture synthesis, not bulk reads). If Sonnet also returns shallow output, decompose the task into smaller sub-prompts rather than escalating further.

## Sub-skill references

- `references/when-to-delegate.md` — full when/when-not table + doc-gen delegation
- `references/hard-rules.md` — streak discipline, edit-loop discipline, session-scope discipline
- `references/scout-first.md` — sub-agent prompt construction + the scout-first procedure
- `references/workflow-authoring.md` — cost-discipline scaffold for Workflow scripts (gate/prime/budget-cap/backflow). MUST be consulted before ANY Workflow tool call — curated or improvised (ultracode).

Rationale, audit numbers, and cost data: see `README.md` in the package root.

## Related skills

- `models-router` — picks the model for delegation (haiku/sonnet/opus). Invoke BEFORE every Agent dispatch.
- `llm-cost-optimizer` — generic LLM cost engineering patterns (model routing, prompt caching, output controls, budget envelopes). This skill enforces *Claude Code session* delegation discipline; `llm-cost-optimizer` is the broader pattern library — invoke it when building or reviewing AI features where the delegation principles also apply.
- `claude-cost-audit` — measures how well delegation discipline is actually being followed across past sessions.
