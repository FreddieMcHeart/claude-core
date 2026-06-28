---
name: models-router
description: Use BEFORE any tool call, sub-agent dispatch, or substantive response on ANY multi-step task. Triggers at task start, at phase boundaries (plan→execute→document), before every Agent tool call, when a session crosses ~3 MB JSONL or ~50 tool calls, when a sub-agent returns shallow output (escalation), and on phrases like "which model", "pick a model", "should I use Opus", "is Sonnet enough", "switch model", "is this Opus-worthy", "cheaper model", "Opus vs Sonnet", "Haiku vs Sonnet".
last_validated: 2026-06-12
validated_against: claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5
notes: Re-validate after any Anthropic release that changes default reasoning effort or per-tier pricing. See docs/brain/claude-code-postmortem-2026-04-23.md for precedent.
---

# Model Router

<EXTREMELY-IMPORTANT>
If there is even a 1% chance this is a real task (not pure chat), you MUST apply this router BEFORE any other tool call or substantive response. This is not optional. Rationalizations like "this is just a simple question" or "I'll route later" are the exact patterns this skill exists to prevent — most wasted cost comes from tasks that *felt* trivial and ran on Opus by default.

The only valid skip condition: the user's message is pure chat with no action implied ("thanks", "ok", "got it").
</EXTREMELY-IMPORTANT>

**Core principle:** Route work to the cheapest Claude model that can handle it. Latency first, cost second, capability ceiling as the hard constraint.

## The three questions — ask in order, before any tool call

1. **Is this routine or deep-thinking?**
   - Routine (tool-call-heavy, deterministic, narrow spec) → Sonnet main.
   - Deep-thinking (architecture, multi-repo reasoning, root-cause debugging) → Opus main (now the top tier).
   - For a finer call, score the task 1–5 (the complexity probe) and route by score — the SAME score also sets how finely to decompose. Cheap signals only; never burn an Opus turn to score. Details: `references/complexity-probe.md`
   - Details: `references/main-agent-routing.md`

2. **About to dispatch a sub-agent?**
   - Haiku for search / scaffolding / log summarization.
   - Sonnet for multi-file work and debugging.
   - Opus only for cross-repo reasoning or architecture synthesis.
   - ALWAYS pass an explicit `model:` parameter. Never rely on the default.
   - Details: `references/sub-agent-routing.md`

3. **Will the session mix routine and deep-thinking?**
   - Quick errand (≤5 tool calls) → sub-agent detour; main stays as-is.
   - Routine *phase* inside the task → `/model` switch at the phase boundary.
   - Distinct standalone routine task → `/clear` + new session, re-prime from plan file.
   - Details: `references/mixed-mode.md`

## Worked example

Task: *"Close these 4 tickets and post a status update to Slack."*

Signal → 100% routine, tool-call-heavy, no reasoning load.
Decision → `/model sonnet`, no sub-agent (too small to justify ~20k dispatch overhead).
Anti-pattern caught → Staying on Opus "because the session started on Opus." Four ticket updates + one Slack message on Opus is ~5× Sonnet's cost for identical output.

## Common mistakes

| Thought | Reality |
|---|---|
| "Just in case it's complex, I'll use Opus" | Start on Sonnet. Escalate if output is shallow. |
| "I already paid for Opus context, may as well stay" | Mid-session switch *does* cheapen future turns. Switch at phase boundaries. |
| "This sub-agent might find edge cases" | Dispatch Haiku. If result is incomplete, re-dispatch Sonnet with a sharper prompt. |
| "Opus sub-agent to be safe" | Opus sub-agent is the most expensive single dispatch. Justify every one. |
| "It's only one turn on Opus" | A session's accumulated prefix re-bills reasoning every turn. One becomes many. |
| "This feels like a simple question" | Exactly the rationalization this skill exists to prevent. Route anyway. |
| "My tech lead / PM / CTO said to just use Opus" | The routing rule is cost-correct per audit data. A social override doesn't change the task classification. Apply the router; escalate the disagreement as a policy question in a separate channel, not a mid-task override. |
| "The underlying engineering is complex, so use Opus" | Route on the complexity of THIS TURN's required output, not on the underlying topic. A 2-paragraph pushback to a complex architecture question is Sonnet work. Opus only when the turn itself requires the reasoning. |

## Escalation rule

If a cheaper model returns shallow or incomplete output, escalate the model AND refine the prompt. A better model with the same bad prompt produces the same bad result. Add: what was missing, what to focus on, what the prior attempt got wrong. Chain caps at Opus for sub-agents — don't escalate sub-agents beyond Opus; decompose the task instead.

## Sub-skill references

- `references/main-agent-routing.md` — Sonnet vs Opus for the main agent, full table
- `references/sub-agent-routing.md` — Haiku/Sonnet/Opus sub-agents, 20k-overhead threshold, write boundaries
- `references/mixed-mode.md` — Detour / switch / clear+new-session patterns + ratio heuristic
- `references/complexity-probe.md` — 1–5 score → tier AND decomposition depth; carry score in native Task metadata

Rationale, audit numbers, and model pricing: see `README.md` in the package root.
