# Hard Rules

Four discipline rules that force a stop-and-reconsider when thresholds are crossed. These exist because the cost-audit baseline showed they were repeatedly violated in "feels-harmless" situations.

## Aggregate read discipline

**Rule:** Independent of any consecutive streak, if your **session total of inline read-only tool calls** (`Bash` / `Read` / `Grep` / `Glob`) in main exceeds **15**, **stop**. The next reading task must be dispatched to a Haiku scout. Reset the counter on `/clear` or after each successful `Agent` dispatch that absorbs reading work.

**Why:** Stutter-step blind spot in the consecutive-streak rule. Empirical case (2026-04-30 cost audit, session `05503837-42e4-4f4f-af58-8146f0256e79`): 39 mechanical inline calls distributed across 13 separate clusters of 2–3 calls each. Every cluster individually complied with the streak-of-4 rule, but cumulative cost was **$39 vs ~$8** had a single Haiku scout absorbed the mechanical work. Rotating between `Bash` → `Read` → `Edit` resets the consecutive counter every 3 turns; the aggregate counter catches what the consecutive rule misses.

**How to track:** Mental tally per session, reset on `/clear`. A harness hook counting main-agent read tool calls is the durable fix — see [[brain/claude-cost-audit-2026-04-30]] for the case study that motivated this rule.

## Streak discipline

**Rule:** If you have made **4 consecutive read-only tool calls** (`Bash` / `Read` / `Grep` / `Glob`) in main without an `Agent` call in between, **stop**. The next action must either:
- (a) write or edit something concrete, or
- (b) dispatch a Haiku agent to take over the reading.

**No exceptions** for "one more quick peek." "One more" is the rationalization that gets you to 10.

**Why:** Each read-only call adds to the main-agent prefix on every subsequent turn. Past 4–5 reads, you're paying main-agent rates for what Haiku would do in a single dispatch and summary.

**Relationship to aggregate rule:** This rule catches *bursts*; the aggregate rule catches *spread-out drift*. Both fire independently — tripping either one is sufficient to force a delegation review.

## Edit-loop discipline

**Rule:** When iterating on a single file (plan docs, runbooks, wiki pages, long source files), **never `Read → Edit → Read → Edit`** the same file in a continuous loop.

Instead:
1. `Read` once.
2. Collect **all** pending changes in conversation as a structured list.
3. Apply as a single multi-`Edit` pass or one `Write`.

**Why:** Every `Edit` invalidates that file's cache entry. The next `Read` pays full input price. This was the single biggest waste pattern in the 2026-04-20 cost audit — 80 re-reads of one plan doc = ~$15–25 wasted in one arc. Over a session, this can exceed all other waste combined.

**Exception:** iterative user-review loops where the user approves each edit. The review is the value; accept the cost.

## Session scope discipline

**Rule:** When a session crosses ~3 MB JSONL or ~50 tool calls, or transitions between phases (plan → execute → document), `/clear` and re-prime from the plan file.

**Why:** Every continuing turn pays for the growing prefix on main-agent reasoning. A fresh session with the plan file as cached input is far cheaper. Session sprawl was the second-biggest cost driver in audit data.

**How to re-prime:** `/clear`, then in the first message reference the plan file path (e.g., `Read docs/plans/2026-04-24-foo.md and continue from Task 5`). Claude re-enters context from the durable file, not from the stale conversation prefix.

## How to check a session against these rules

- **Aggregate check:** count total inline `Bash` / `Read` / `Grep` / `Glob` calls in main since last `/clear` or `Agent` dispatch. ≥15 → next read goes to a Haiku scout, no exceptions.
- **Streak check:** look at your last 5 tool calls. If 4+ are read-only, you're on thin ice.
- **Edit-loop check:** grep your session for repeat-reads of the same path. >3 reads of the same file = warning.
- **Session scope check:** look at JSONL size (`ls -lh ~/.claude/projects/*/<session-id>.jsonl`) and tool-call count. Above the thresholds → /clear.

## Red flags — STOP and apply the rule

- "These reads are spread out, no single streak is bad" (aggregate — exactly the stutter-step blind spot)
- "Just one more file" (streak)
- "I'll just re-read to check" (edit-loop)
- "We're making good progress, no need to /clear" (session scope)
- "The context's still useful" (session scope — usually wrong after phase transition)

All of these mean: you're about to violate the rule. Stop. Apply the discipline.
