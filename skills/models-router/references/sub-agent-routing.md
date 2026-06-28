# Sub-Agent Model Routing

When spawning a sub-agent via the `Agent` tool, **always** pass an explicit `model:` parameter. Never rely on the default — the default is whatever the platform picks today, which may be expensive and is not under your control.

Priority: latency first, cost second, capability ceiling as the hard constraint.

## Decision table

| Task type | Model | When to use |
|---|---|---|
| File search, grep, glob, structure listing | `haiku` | Finding files, searching for patterns, listing directories, "peek at structure" |
| Single-file code generation with complete spec | `haiku` | Isolated function, clear inputs/outputs, no ambiguity |
| Format conversion, boilerplate, scaffolding | `haiku` | Mechanical transforms, JSON-to-YAML, template fill-in |
| Log / output summarization | `haiku` | Reduce a verbose tool output to a <200 word digest |
| Multi-file coordination, integration | `sonnet` | Cross-file changes, import wiring, API alignment |
| Debugging, root cause analysis | `sonnet` | Start here; escalate to `opus` only if stuck |
| Writing plans, runbooks, documentation from inputs | `sonnet` | Synthesize structured output from provided context |
| Code review, architecture review | `opus` | Needs broad codebase understanding or design judgment |
| Cross-repo analysis or coordination | `opus` | Reasoning across independent repositories simultaneously |
| RLM-style synthesis (merging scout findings) | `opus` | Cross-source contradiction resolution |

**Default when uncertain:** `sonnet`. Only use `opus` for sub-agents when deep reasoning across multiple files or repos is required.

Fable is disabled (2026-06-12); sub-agent tiers are haiku/sonnet/opus. Escalation caps at opus.

## Escalation chains

When a sub-agent returns insufficient results, escalate model **and** refine the prompt. A better model with the same bad prompt produces the same bad result.

| Situation | Action |
|---|---|
| `haiku` returns incomplete or wrong results | Re-dispatch with `sonnet` + more specific prompt (what was missing, what to focus on) |
| `sonnet` returns shallow analysis | Re-dispatch with `opus` + explicit "dig deeper" instruction + what the previous attempt got wrong |
| `opus` returns insufficient results | **Don't escalate further.** Refine the prompt or decompose the task into smaller pieces. |

## Token efficiency: scout-first dispatch

Before any `sonnet` or `opus` dispatch, apply the **scout-first pattern**:

1. **Check existing knowledge** (wiki, docs, README) for paths / context
2. If knowledge exists → pass as `Start here: <paths>` hints in the agent prompt
3. If not → dispatch a `haiku` scout first (`Find all files related to X. Report paths only, <80 words`) and use its output to prime the real dispatch
4. **Never** dispatch a `sonnet`/`opus` agent with "find X in the codebase" — that's burning $15–75/M tokens on filesystem traversal

### Agent prompt template

Every Agent prompt should include:

```
Context: <1 sentence what you're trying to accomplish and why>
Start here: <specific paths from scout or wiki>
Deliverable: <single sentence, what you want back>
Report in under <80 | 200 | 400> words.
```

Short reports force the model to synthesize — long reports are a sign the sub-agent is copy-pasting tool output into its response, which means you paid for reads-as-output at $15/M or $75/M instead of getting a summary.

## The 20k overhead threshold

Sub-agents have ~20k tokens of fixed overhead per dispatch (initialization context, tool discovery, skill availability). Below a certain task size, delegating is a net loss.

**Delegate when ALL of these hold:**
- Expected raw output >~2 KB (roughly >50 lines or >500 tokens)
- Raw output will NOT be used verbatim in the final answer or a subsequent `Edit`
- The goal is synthesis / summary, not exact text retrieval

**Always delegate:**
- Multi-file synthesis across >3 files
- Any operation where the intermediate tool output is verbose (full JSON threads, multi-page logs, schema dumps)
- Exploratory sweeps of 3+ `Grep` / `Read` calls on the same question

**Always inline (do it in the main agent):**
- Reads-for-editing (you need the exact text to construct an `Edit`'s `old_string`)
- A single targeted `Grep` returning a handful of matches
- Short configs or files under 50 lines
- Interactive iteration where seeing raw output matters

**Rule of thumb:** "Read for *editing* in main; read for *understanding* in a sub-agent."

### When you can't predict the output size

Real codebases don't tell you in advance how many matches a grep will return. A search in a 200k-line monorepo might yield 3 paths or 300. You can't apply the "50 lines / 2 KB" rule at dispatch time when the count is unknown.

**Heuristic: default to delegate when the search domain is large, even if the match count is uncertain.**

- Domain is a single small file or narrow config → inline.
- Domain is one module / folder (<5k lines, bounded) → inline if you expect <20 matches; delegate otherwise.
- Domain is a whole repo / monorepo → delegate to Haiku. The 20k overhead is cheap insurance against a verbose result flooding the main-agent prefix.

The cost asymmetry: over-delegating at scale costs ~20k cached tokens; under-delegating at scale and getting 300 lines of grep output inline costs far more, because those 300 lines then sit in main's prefix and are re-billed for every subsequent turn.

## Write boundaries (safety)

Sub-agents may read and edit within their assigned worktree or scope. They MUST NOT perform side-effect writes to external systems unless explicitly authorized by the skill they're invoking:

- No Jira / Linear / issue-tracker writes
- No GitHub PR / issue / release creation
- No `git push`
- No Slack / email / chat posts
- No writes outside their assigned directory

Parallel sub-agents cannot coordinate — each would independently create its own ticket / PR / message. The main agent centralizes all side-effect writes after aggregating sub-agent findings.

**Canonical verification phrase — prepend to EVERY `Agent` tool prompt verbatim:**

```
[WRITE BOUNDARY] Sub-agent rules: read/edit only in your assigned scope.
No ticket creation, no PR creation, no `git push`, no chat posts.
Report findings only; the main agent performs all side-effect writes.
```

## Effort as a routing axis (2026-06-12)

Effort is orthogonal to model — a separate dimension you can tune independently:

- **Session effort**: `/effort low|medium|high|xhigh` (persists for the session). `max` and `ultracode` are session-only synonyms for xhigh.
- **Per-dispatch effort**: subagent frontmatter `effort:` field overrides session effort for that one dispatch.
- **`ultrathink` in a prompt**: in-context nudge only — does NOT raise the effort level or set a floor. Pair with `/effort high|xhigh` if you actually want more reasoning budget.
- **Hooks cannot set effort**: the PreToolUse payload carries a read-only `effort.level`; there is no output field that changes it.

Suggested conventions (not mandatory — adopt post 06-17 audit):
- Reader/scout sub-agents: `effort: low` (mechanical search, no reasoning load).
- Architecture/synthesis sub-agents: `effort: high` (needs reasoning depth).
Do **not** mass-edit all agent frontmatter now — lever documented here, adoption gated on the 06-17 audit.

## Anti-patterns

- **Opus sub-agent "to be safe"** — most expensive (after Fable) single dispatch you can make. Justify every one.
- **Spawning a sub-agent for a one-line task** — 20k overhead eats the budget. Inline it.
- **Using a sub-agent to read a file you're about to edit** — you need the exact text in main for `Edit`. Read inline.
- **No model parameter passed** — falls back to platform default. Always be explicit.
