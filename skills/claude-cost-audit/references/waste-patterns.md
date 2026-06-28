# Waste Patterns

Canonical waste patterns to detect during a cost audit. Each pattern lists the signal, the diagnosis, and the canonical fix (linked to `brain/claude-cost-patterns.md`).

## 1. Edit-loop cache thrashing

**Signal:** Same file Read >10 times across an arc, interleaved with Edit calls on the same file.
**Diagnosis:** Each Edit invalidates the file's cache entry; the next Read pays full input price.
**Fix:** Batch-edit pattern (Read once, collect all changes, apply as single multi-Edit).

## 2. Inline doc generation on Opus

**Signal:** Assistant turn with >4000 output tokens composing prose (docs, summaries, long writeups).
**Diagnosis:** Output at $75/M on Opus is uniquely expensive; composition is mechanical work.
**Fix:** Delegate composition to Haiku/Sonnet sub-agent with Write target.

## 3. Session sprawl (phase bleed)

**Signal:** JSONL file crosses 3MB or 50 tool calls; session covers multiple phases (plan → execute → document) continuously.
**Diagnosis:** Every subsequent turn pays for the growing prefix under `xhigh` reasoning (uncached).
**Fix:** `/clear` at phase boundaries; re-prime from plan file.

## 4. Model mismatch (Opus for routine work)

**Signal:** Session is dominated by Jira/GH/Atlantis/Bash tool calls but runs on Opus 4.7 main agent.
**Diagnosis:** Opus output at $75/M vs Sonnet $15/M — 5× overcharge for mechanical work.
**Fix:** `/model sonnet` for routine sessions; Opus reserved for deep-thinking.

## 5. Sub-agent model default drift

**Signal:** Sub-agent dispatches with no explicit `model:` annotation, or "unknown" in usage data.
**Diagnosis:** Defaults to Sonnet/Opus when Haiku would suffice.
**Fix:** Always pass `model: haiku` for scout/file-search sub-agents.

## 6. Re-derivation of captured context

**Signal:** Same analysis performed twice in one session (e.g., "what are the risks of X?" asked two different ways).
**Diagnosis:** Context was never written to a plan file; main agent re-reasons from scratch.
**Fix:** Use `writing-plans` skill; the plan file becomes cached input for later arcs.
