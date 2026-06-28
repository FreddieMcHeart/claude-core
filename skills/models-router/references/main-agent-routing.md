# Main-Agent Model Routing

The main Claude Code agent is the one you talk to directly. It produces the most output of any agent in your session, so its model choice dominates cost.

Default to **Sonnet 4.6** for routine work. Switch to **Opus 4.8** only when the turn genuinely requires deep reasoning. Opus is now the top tier (Fable 5 disabled 2026-06-12).

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

## Tier choice within Opus: effort level

Opus has effort tiers (`low` / `medium` / `high`). Reasoning tokens at high effort are **never cached** and bill at full rate every turn.

Reserve `high` for:
- Architecture design, multi-repo reasoning, debugging a failing theory
- Writing design docs where prose quality matters

Drop to `medium` or `low` for:
- PR triage, diff review, single-file edits on Opus
- Slack / Jira composition on Opus (if you're staying on Opus for other reasons)
- Verification command runs

## Anti-patterns

- **Staying on Opus because "I already paid for context"** — the prefix is billed every subsequent turn. Switch at the phase boundary; future turns get cheaper immediately.
- **Switching to Sonnet for a single turn** — mid-session switches benefit future turns, not the current one. Don't micro-optimize.
- **Using the advisor tool on Sonnet to avoid switching main to Opus** — advisor has overhead; if the whole task is deep-thinking, just switch main.
