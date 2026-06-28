# When to Delegate

The core delegation heuristic: **sub-agents have ~20k tokens of fixed overhead**. Below that break-even, inline is cheaper. Above it, delegation is cheaper. This file tells you which side of the break-even each operation sits on.

## The delegation decision

Delegate a read, query, or long-output operation to a sub-agent when **ALL** of these hold:

- (a) Expected raw output is > ~2 KB (roughly >50 lines / >500 tokens)
- (b) Raw output won't be used verbatim in the final answer or a subsequent `Edit`
- (c) The goal is synthesis / summary, not exact text retrieval

Ask the sub-agent for a **< 200 word summary** unless the task genuinely requires more.

## Always delegate — no judgment call needed

These are universally verbose and universally summary-worthy. Never run them in main:

- `slack-cli.sh replies` — thread JSON is always verbose
- Exploratory sweeps of 3+ `Grep` / `Read` calls on the same question — batch to a single Haiku agent
- Multi-file synthesis across >3 files — mental-model construction, always delegate
- Any codebase structure discovery: `ls`, `find`, `tree`, `wc -l`, or equivalent shell pipelines — delegate or use `Glob`
- Any `Grep` expected to return >50 lines — narrow first, or delegate
- Sequential diagnostic queries (`bq query`, `gcloud ... list`, `aws ... describe`, `kubectl get ... -o yaml`) where the goal is a summary, not raw rows — delegate and ask for a summary table

## Always inline — delegation would be wasteful

These are below the break-even, or need raw text in main:

- Reads-for-editing — you need exact text for `Edit`'s `old_string`. Inline.
- Single targeted `Grep` returning a handful of matches. Inline.
- Short configs / files (<50 lines). Inline.
- Interactive iteration where seeing raw output matters. Inline.
- **Symbol-shaped lookup** (where is X defined / who calls X / what implements X) in Go/Ruby/Python/TS/Terraform → use the native LSP tool (`goToDefinition` / `findReferences` / `incomingCalls`) BEFORE dispatching any scout or running a Grep+Read chain — a ~100-token exact result beats a ~20k-token Haiku dispatch and a multi-file grep. Fall back to Grep only when no server is configured for the file type.
  Calibration (measured 2026-06-11, 6 languages, real Mondu files):
  - **The saving scales with the question, not the file**: single-file overview 2–6×; cross-file reference tracing ~26× (grep cascade ≈ 11k tok vs findReferences ≈ 430 tok); whole-module orientation is impossible→possible (risk-engine = ~25M tok, no context fits it).
  - **Skip LSP for files under ~1KB** — just Read them; the symbol tree can be BIGGER than the file (measured on a 539B workflow YAML: 0.6×, LSP lost).
  - **Skip LSP for declaration-dense files** (config structs, env specs) — when nearly every line IS a declaration, the symbol list ≈ half the file (spec.go: only 2.1×); Read may serve better since you likely need the tags/values anyway.
  - **Compound pattern is the optimum for "understand one function"**: `documentSymbol` tree (~600 tok) → `Read` with offset/limit on just the target method (~400 tok) ≈ 3× cheaper than the full-file Read AND you hold the actual code, not just names.

## When unsure — inline it

Cost of over-delegation (~20k wasted tokens + 5s latency) is higher than cost of a few hundred tokens in main. Rule of thumb: **"read for editing in main; read for understanding in a sub-agent."**

## Doc-gen delegation — hard rule

When about to emit >4k output tokens of prose in main, **stop**. Delegate composition to a Haiku or Sonnet sub-agent with a `Write` target path. Main emits a pointer to the written file, not the prose itself.

Why: output tokens are uncached, expensive, and reasoning-tokens on a high-effort main model are priced at the main-model rate. A 5k-token writeup on Opus main costs ~5× the same writeup on Sonnet sub-agent. And sub-agents emit `Write` calls that land durably on disk — the prose survives a `/clear`.

## Escalation when a Haiku sub-agent returns shallow output

Don't re-dispatch the same prompt to Sonnet and hope. Refine:
1. What was missing from the previous output?
2. What's the specific question the summary must answer?
3. What paths / signals should the agent focus on?

A better model with the same vague prompt produces the same vague result.
