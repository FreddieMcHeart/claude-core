# Scout-First Dispatch

Before any Sonnet or Opus agent dispatch, apply the **scout-first pattern**. A Sonnet agent dispatched with "find X in the codebase" is the single most expensive form of waste — the agent spends 80% of its budget searching and 20% synthesizing. Scout first means a Haiku agent (or a wiki lookup) produces the paths, then the Sonnet/Opus agent consumes those paths and spends its full budget on synthesis.

## The 4-step scout-first procedure

1. **Grep `docs/` first** for the service or topic. Extract paths from wiki hits.
2. **If wiki has paths** → pass them as `Start here:` hints in the agent prompt.
3. **If no wiki paths** → dispatch a `file-finder` (Haiku) sub-agent first. Use its paths for the real agent.
4. **Never** dispatch a Sonnet / Opus agent with "find X in the codebase" as the primary task.

## Always include in agent prompts

Every `Agent` dispatch MUST include these in the prompt, in this order:

- **Specific repo + directory** — not "our codebase," but `mama/mondu/platform` or `mama/mondu/docs/platform/services/`.
- **`Start here:` file paths** — the scout's findings or the wiki's paths. If you can't produce these, you haven't scouted yet.
- **Expected deliverable in one sentence** — "Report the 3 Kafka topics this service publishes and their schemas."
- **Word cap** — match the cap to the scout shape:
  - **Structural scouts** (directory layout, deployment conventions, file topology): *"Report in under 80 words."*
  - **Semantic scouts** (concept distinctions, premise verification, multi-type taxonomies, event/message types): *"Report in under 150 words."* The extra budget is what preserves premise corrections and multi-type semantics that 80-caps silently drop.
  - **RLM (repository-level-map) scout summaries**: *"Report in under 200 words."*

  Empirical basis: 2026-04-24 N=2 ablation (semantic Kafka task vs structural terraform task). At 80 words, the semantic task lost load-bearing content (premise correction, event-type semantics, recovery inventory) while the structural task did not. See `docs/brain/claude-code-postmortem-2026-04-23.md` for the full findings.

Missing any of these → the agent will either wander or over-report. Both waste tokens.

## Write boundary (safety-critical — canonical policy lives elsewhere)

Sub-agents may read / edit only within their assigned worktree — they MUST NOT create Jira tickets, GitHub PRs / issues, run `git push`, or post to Slack. Parallel sub-agents cannot coordinate, so each would create duplicates. Main agent centralizes all side-effect writes.

**Canonical policy + full list + the `[WRITE BOUNDARY]` verification phrase you must prepend to every `Agent` tool prompt:** see project CLAUDE.md → `## Skills & Tooling` → "Sub-Agent Write Boundary" subsection.

This skill does not restate the Write Boundary to avoid drift between two copies. When dispatching any sub-agent, prepend the canonical verification phrase from CLAUDE.md verbatim.

## Worked example

Bad dispatch:
```
Dispatch: Explore agent, Sonnet
Prompt: "Find how our authentication webhooks work."
```
→ agent spends 15k tokens searching, returns a 3k-word writeup, much of it irrelevant.

Good dispatch:
```
Step 1 (main, free): grep docs/platform/services for auth → finds docs/platform/services/auth.md, docs/platform/services/webhook-gateway.md.
Step 2 (Haiku, 20k): file-finder "find webhook-related handler files in mama/mondu/platform" → returns 4 file paths.
Step 3 (Sonnet, 30k): Explore agent with prompt: "Starting from docs/platform/services/auth.md, docs/platform/services/webhook-gateway.md, and these handler files [paths], summarize the auth webhook flow end-to-end. Report in under 200 words."
```
→ agent uses all 30k on synthesis, returns a tight 180-word summary.

Cost: good dispatch is ~1.5× bad dispatch raw, but the output is actually usable. The bad dispatch often needs a re-run with sharper prompt, doubling total cost.
