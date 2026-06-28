# Mixed-Mode Session Patterns

Real sessions almost never fit a single category. You start on a deep architecture question, then spend 20 minutes updating tickets and posting Slack updates. Or you begin with routine CI triage, then discover a bug that demands root-cause reasoning.

The wrong answer: pick one model at session start and suffer. Either the routine phase burns Opus dollars, or the deep phase underperforms on Sonnet.

The right answer: **pick a pattern based on how tightly the routine work is coupled to the thinking work.**

## The three patterns

| Pattern | When to use | How |
|---|---|---|
| **Sub-agent detour** (main stays as-is) | Routine work is a quick side-errand — ≤5 tool calls, no ongoing state. "Check if that ticket exists", "grab the last PR comment", "kubectl confirm pod running". | Dispatch a `sonnet` or `haiku` sub-agent with a specific task. Main context preserved. |
| **Mid-session model switch** (`/model sonnet` ↔ `/model opus`) | Routine work is a *phase* in the same task — tightly coupled, multi-turn, no deep reasoning. "Design is settled, now update 4 tickets + post to Slack". | `/model sonnet` at the phase boundary; `/model opus` when deep thinking resumes. Switch *early* — mid-session switches don't retroactively cheapen already-billed turns. |
| **`/clear` + new session** | Routine work is a *distinct task* that can stand alone — reloadable from a plan file, ticket ID, or PR link. "Design done, I'll execute the checklist fresh tomorrow". | `/clear`, re-prime from the plan / ticket / PR as cached input. Best per-turn cost; small re-priming cost. |

## Ratio heuristic

Roughly estimate routine vs deep-thinking turns in the session:

- **>50% routine** → default `/model sonnet` for the session. Escalate to `/model opus` only for the thinking stretches.
- **<20% routine** → keep main on Opus. Delegate the routine bits to Sonnet sub-agents (detour pattern).
- **20–50% middle band** → split the session at phase boundaries (`/clear` pattern).

### Applying the ratio heuristic mid-session

The heuristic above is most naturally applied at session start. If you are *already* deep into a session on Opus and only now notice a phase change (e.g. 90 minutes of design, user pivots to "close these tickets"), the ratio framing doesn't rewind the sunk cost — it's forward-looking.

Apply it to the **remaining** turns, not the whole session:

- "Will the rest of this session be >50% routine?" → `/model sonnet` at the phase boundary.
- "Is the routine phase a quick detour (≤5 tool calls)?" → dispatch a sub-agent; main stays.
- "Is the remainder a distinct phase that could reload from a plan file?" → `/clear` + new session.

The accumulated Opus prefix is already billed and unrecoverable. What matters is that **future** turns get cheaper — a mid-session switch does that immediately.

## Why not just "always one session on Opus"?

Because every turn *after* a pivot re-pays for the accumulated prefix under main-agent reasoning (never cached when reasoning effort is high).

A 30-minute deep-thinking arc followed by 20 minutes of Jira cleanup on Opus is pure waste for the Jira portion — Sonnet (or a split session) does it for ~20% of the cost.

## Why not just "always split into single-purpose sessions"?

Because each `/clear` pays re-priming cost — re-reading CLAUDE.md, wiki pages, relevant files, conversation context. Good when the routine work is genuinely decouplable; wasteful for 5-tool-call detours where context is cheap to keep warm.

## Task-transition forcing rule (hard checkpoint)

Sessions where the user supplies **multiple tasks in sequence** ("first X, then Y, then Z") have a predictable failure mode: the agent finishes task N with momentum, sees task N+1 immediately available, and launches into it on whatever model it was just running. Pressure testing shows ~70% self-reported likelihood of forgetting the model switch under these conditions.

**Hard rule:** between every pair of tasks in a multi-task session, pause for ONE turn and ask the four phase-boundary checklist questions below BEFORE any tool call on the new task. Even if the answer is "stay on the same model," make the decision explicit. Treat this as non-negotiable — the cost of a 1-sentence pause is nothing; the cost of unconsciously rolling Opus into a Jira-update phase is real.

Signal that you just crossed a task transition:
- The user's message had multiple numbered / bulleted tasks
- You just completed the deliverable for task N and task N+1 is waiting
- Your last turn produced a summary / report / root-cause statement and the natural next move is routine work (ticket, Slack, PR)

## Phase-boundary checklist

When you think you're at a phase boundary, ask:

1. **Would I be okay restarting this phase from a plan file / ticket ID?** → Yes: use `/clear`. No: stay in session.
2. **Is the next phase tool-call-heavy with clear specs?** → Yes: `/model sonnet`. No: stay on Opus.
3. **Is it just 1–5 tool calls?** → Dispatch a sub-agent detour instead of switching.
4. **Am I about to emit >4k tokens of prose?** → Delegate composition to a Haiku/Sonnet sub-agent regardless of main's model.

## Red-flag signals mid-session

Mid-session triggers to stop and consider switching:

- The JSONL file has crossed ~3 MB or the conversation crossed ~50 tool calls → consider `/clear`
- You've been running `kubectl`, `gh`, or CLI commands for 10+ turns on Opus → `/model sonnet`
- You're about to write a long doc / summary / colleague writeup → delegate composition
- The next task is "update tickets and notify the team" → `/model sonnet` or sub-agent
- The next task is "figure out why X is broken, first-pass theory failed" → `/model opus`

## Anti-patterns

- **Switching for a single turn** — the benefit is on *future* turns' prefix, not the current one. Don't micro-optimize.
- **`/clear` mid-debug** — you lose the state you're actively reasoning over. Only `/clear` at *clean* phase boundaries where the next phase reloads from a file.
- **Detour for a task that really needed 20 tool calls** — if the sub-agent dispatch balloons, it stops being a detour; you've just paid the 20k overhead for a full session's worth of work. Promote it to a proper sub-agent task with a clear spec.
- **Forgetting to switch back** — mid-session `/model sonnet` for a routine phase, but then deep-thinking resumes and nobody switched to Opus. Main now under-reasons for the rest of the session. Set a mental checkpoint: "when this phase ends, re-evaluate."
