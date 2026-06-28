# Complexity Probe — score-driven tier + decomposition depth

Origin: mined from the Taskmaster `cctool` REJECT (`docs/brain/tool-evaluations-2026-06.md`).
Taskmaster scores a task's complexity before decomposing, then sizes the subtask count to the
score. We adopt the *idea* in our idiom: **one cheap complexity score drives BOTH the model
tier (question 1 of this skill) AND how finely to decompose (writing-plans / TaskCreate).**

This makes explicit the feel-based call the router already makes. It is the complexity cousin
of the `ccprobe` snippet (which probes a *condition* to avoid pessimistic routing; this probes
*complexity* to right-size effort + decomposition).

## When to score

At task start and at each plan boundary — the same trigger points as the three questions.
**Score from the prompt + cheap signals you already have. Do NOT burn an Opus turn or a deep
read just to score.** If scoring genuinely needs a fact you don't have (e.g. "how many call
sites?"), resolve only that one fact with a single Haiku scout (the `ccprobe` pattern), then
score. A probe that costs more than it saves is an anti-pattern.

## The 1–5 rubric (cheap signals)

| Score | Signals | Shape |
|---|---|---|
| **1** | 1 file, mechanical, clear spec, reversible | one-liner / config flip / rename |
| **2** | 1–2 files, light logic, single repo, tests obvious | small fix |
| **3** | 3–5 files OR one tricky file, single repo, some design | routine feature/fix |
| **4** | multi-file integration, cross-cutting, or 2 repos, real design choice | feature / refactor |
| **5** | 2+ repos OR architecture/root-cause/migration, irreversible blast radius | deep-thinking |

Lean on the signals already in `main-agent-routing.md` (routine vs deep-thinking) — the score
is just a finer-grained version of that binary.

## Score → model tier (feeds question 1)

- **1–2** → Sonnet, no escalation.
- **3** → Sonnet (high). Escalate only if output is shallow.
- **4** → Opus (default high).
- **5** → Opus, `/effort high|xhigh`.

This is the existing routing table indexed by score instead of by gut. Escalation rule still
applies: shallow output → raise model AND sharpen prompt.

## Score → decomposition depth (feeds writing-plans / TaskCreate)

- **1–2** → do NOT decompose. One task, just do it (YAGNI — a plan for a one-liner is waste).
- **3** → 2–4 bite-sized tasks.
- **4** → 4–8 tasks, consider dependency edges (`addBlockedBy`).
- **5** → full `writing-plans` pass; tasks + explicit dependency graph; likely worktree + phased.

## Carry the score in native Task metadata

When the work becomes tracked tasks, stash the score and tier in the native Task store so they
travel with the task across agents/relay and stay auditable:

```
TaskCreate(subject="...", description="...", metadata={"complexity": 4, "tier": "opus"})
```

Later turns / relay children / the 06-17-style audit can read `metadata.complexity` to check
whether routing matched the score. This is the seam that ties the probe to the native task
tracking (`TaskCreate/Update/List/Get`) — see `docs/brain/complexity-probe-routing.md`.

## Skip conditions

- Pure chat / no action → no score (same skip as the rest of this skill).
- Obvious score-1 trivial task → score it 1 in your head, skip the ceremony, just act.
- Don't let the probe become its own multi-step investigation. If you can't score it in one
  glance + at most one Haiku scout, the task is a 4–5 by definition — route Opus and decompose.
