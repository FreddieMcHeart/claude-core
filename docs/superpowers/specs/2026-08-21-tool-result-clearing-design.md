# Tool-result clearing as a standing policy — design

**Goal:** make the clearing of stale tool results continuous and principled rather than
ad hoc, under a rule that cannot silently destroy evidence. The rule is
**re-derivability**, not age and not size.

**Context — and it is not the context this task was opened with.** The task was scoped on
2026-08-20 assuming the deliverable was a clearing algorithm to be implemented in
`hooks/cost-discipline.py`. Three findings, measured 2026-08-21 and cited in §1, invalidate
that framing: the harness already performs tool-output clearing natively, a hook cannot
implement the safety invariant this design requires, and the largest consumer of context is
not tool results at all. This document therefore specifies a **policy** — what may be
cleared, what must be preserved, and what is actually reachable at each hook seam — and
explicitly does **not** specify a clearing algorithm, because the seam to implement one on
does not exist.

Everything below that was carried over from the original scoping is marked as such, so a
reader can tell the surviving parts from the corrected ones.

---

## 1. Three findings that resize the problem

All three were established on 2026-08-21 by independent read-only investigation. Each is
recorded with what establishes it, because two of them contradict the task as written.

### 1.1 The harness already clears tool outputs, and the clearing is not configurable

Documented in two places on `code.claude.com`, in identical wording — `how-claude-code-works`
§ *When context fills up*, and the `glossary` entry for **Compaction**:

> Older tool outputs are cleared first, then the conversation is summarized.

- **Trigger:** context pressure — "as you approach the limit". No age, size, or turn-count
  rule is documented.
- **Configurability:** none for the clearing step itself. The only documented knob governs
  the *whole* pipeline's threshold: `/autocompact <value>`, the `--autocompact` CLI flag,
  `autoCompactWindow` in `settings.json`, and `CLAUDE_CODE_AUTO_COMPACT_WINDOW`.
- **What replaces the cleared content: not stated anywhere in the documentation.** The
  natural inference is that nothing does — silent removal. That inference is unconfirmed and
  is the single most consequential open question in this design; see §6.1.

**Consequence.** Reimplementing clearing in a hook would duplicate a mechanism that already
runs. The useful contribution is not a second clearing engine — it is a policy governing what
must survive the first one.

*A note on "microcompact":* the term appears in third-party writing describing a distinct
tool-result-only pass, and does **not** appear anywhere in Anthropic's documentation. This
design does not rely on it existing as a separately controllable mechanism, and no reader
should treat it as a supported seam.

### 1.2 A hook cannot replace a tool result — the safety invariant is unimplementable as specified

From the `hooks` documentation, confirmed across two independent fetches:

- **`PostToolUse`** — fires after execution. Available output: `additionalContext` (appended
  *after* the result, capped at 10,000 characters), `systemMessage`, `stop`. The docs state
  plainly: **"PostToolUse cannot modify or replace the tool's result content."**
- **`PreToolUse`** — fires before execution, so no result exists yet. Available output:
  `permissionDecision`, `permissionDecisionReason`, `additionalContext` (10,000-char cap),
  and `updatedInput`, which replaces the tool's **input** — its arguments, never its output.
- **The transcript is not a write surface.** `transcript_path` is handed to hooks for reading
  and is documented as "written asynchronously and may lag the in-memory conversation";
  `sessions` documentation states the JSONL entry format is internal and changes between
  versions, directing scripts to `/export` rather than to parsing or writing it. The two
  mechanisms that do alter history do not edit entries: `/rewind` truncates back to a point,
  `/compact` replaces history with a summary.

**Consequence.** The original invariant — *a cleared result is replaced by a stub naming
tool, target, byte size and restore command* — describes an edit to an already-produced
result. No hook event permits that edit, and no supported API rewrites a recorded entry. The
invariant's **intent** survives and is preserved in §4; the **mechanism** it named does not
exist and must not be specified as though it did.

### 1.3 Tool results are the third-largest bucket, and the largest one was mis-scoped

Forensic measurement over the post-compact tail of session
`7a8ca085-87c9-4194-9b52-d4cfe4660474`
(`~/.claude/projects/-Users-nazariiahapevych-mama-operations/`). The session has been
compacted **10 times**; the last `compact_boundary` sits at line 16,718 of 18,221.

Two measurements exist, taken a day apart, and both are reported because the difference is
itself part of the finding:

| block type | 2026-08-20 · 1,262-line tail · 846,831 B | 2026-08-21 · 1,503-line tail · 1,066,243 B |
|---|---|---|
| `thinking` | 95 blocks · 444,647 B · **52.5%** | 112 blocks · 514,645 B · **48.3%** |
| `tool_use` | 136 blocks · 184,992 B · 21.9% | 157 blocks · 243,881 B · 22.9% |
| `text` | 67 blocks · 84,602 B · 10.0% | 109 blocks · 161,560 B · **15.2%** |
| `tool_result` | 136 blocks · 132,590 B · **15.7%** | 157 blocks · 146,157 B · **13.7%** |

**Read the shape, not the decimals.** The two runs differ for two established reasons, not
because either is wrong: the session is live and its tail grew by 241 lines (block counts,
which are definition-independent, rose 15–18% across every type), and the later run counts
string-form `message.content` as a synthetic `text` block where the earlier run appears not
to have. The later measurement defines bytes as
`len(json.dumps(block, ensure_ascii=False).encode('utf-8'))` over the whole serialized
block; the denominator is the sum across blocks, not the 49 MB on-disk file size.

Any figure in this table is a reading of a moving system on a date. Quote it with its date
and its method or do not quote it.

**The durable conclusions, which both runs support:**

- Extended `thinking` is roughly **half** of context-shaped block content.
- `tool_result` is roughly **an eighth to a sixth** — the third-largest bucket, having been
  overtaken by `text` between the two runs.
- **A policy that clears tool results perfectly addresses about a seventh of the problem.**
  This document must not be read as a solution to context growth.

### 1.4 Correction: thinking blocks are in scope, and the task's stated reason was wrong

The task row asserts that thinking blocks "are billed as output and never cached, so the
economics differ." That is wrong for the models in use here.

Per `platform.claude.com` → *Extended thinking* § **Thinking block preservation by model**,
one group of models — **Opus 4.5+, Sonnet 5, Fable 5, Mythos 5** — keeps all prior turns'
thinking. On these, previous turns' thinking blocks "stay cached and in context," "count
toward the window, and are billed as input tokens like the rest of the conversation
history," and "cached thinking blocks count as input tokens in your usage metrics when read
from the cache."

Both sides of this harness sit in that group: the main agent runs Opus 5, and the standard
scout tier is Sonnet 5.

The task row is not wholly wrong — thinking tokens *are* billed as output **when generated**.
Its error is treating generation-time billing as the whole story. On a keep-all model the
same blocks are re-billed as **input** on every subsequent turn, which is precisely the
compounding cost that makes the largest bucket the expensive one.

*Hedge, carried deliberately:* this behaviour is documented at the platform/Messages-API
level and is **not** independently restated on `code.claude.com` for Claude Code's own
request shaping. That it applies unchanged inside Claude Code is an inference. It is a
well-supported one, and it is still an inference — do not promote it to a fact by
restatement. The older Sonnet/Opus generations and all Haiku through 4.5 sit on the other
side of the split, stripping prior thinking once a non-tool-result user message arrives; on
those, thinking is not re-billed and would need separate treatment.

---

## 2. The rule

Carried unchanged from the 2026-08-20 scoping. It is the part of the original design that
survives contact with §1 intact.

> A tool result may be cleared **iff** it can be obtained again by re-running the same
> command against a source that has not changed since.

**Clear by re-derivability, never by age or size.** Both of those are the intuitive axes and
both are wrong in the same direction: a one-line API answer can be permanently unrecoverable
while a megabyte file read is free to re-obtain. Age and size correlate with cost, not with
recoverability, and it is recoverability that decides whether clearing destroys anything.

### 2.1 The three tiers

**Tier 1 — NEVER CLEAR.**

- Anything read from a mutable or external source: `gh`, Jira, `kubectl`, Notion, Datadog,
  Slack — anything carrying a clock.
- Any **baseline** captured before a change. This is why the `terraform fmt -check` rule
  exists in CLAUDE.md: a pre-edit state cannot be re-taken, and losing it converts "my change
  is clean" into "fmt still fails", which is an accusation rather than a result.
- Test: *would re-running this command today give the same answer?* No → keep.

**Tier 2 — CLEAR FREELY.**

- File reads whose file is unchanged since the read.
- Any result superseded by a later read of the same target — keep newest per target.
- The harness already tracks the necessary state: `file-history-snapshot` and
  `file-history-delta` entries exist in the transcript. **Confirmed** 2026-08-21 by the
  forensic pass, which encountered both types among the 979 tail lines carrying no content
  blocks.

**Tier 3 — CLEAR WITH A MARKER**, everything else, older than N turns. What "marker" can mean
in practice is constrained by §1.2 and is specified in §4.

### 2.2 Two guards

- Never clear a result produced in the **current turn**.
- Never clear anything an assistant message has **referenced since** — the cited set.

---

## 3. The safety invariant, and what remains of it

**The invariant, stated as intent rather than as mechanism:**

> Clearing must never leave the model unable to distinguish *"bulky output was here"* from
> *"nothing was here."*

This is what makes the policy affordable to get wrong. A wrong clear under this invariant
costs one extra tool call. A wrong clear without it costs a wrong conclusion, drawn
confidently from a gap — the false-absence failure class that CLAUDE.md devotes an entire
section to (*"Absence Is a Claim About a Population"*). An absence the model was never going
to observe is not an absence, and a model reasoning over a silently emptied slot has no
signal that it is reasoning over one.

The original mechanism was a replacement stub:

```
[tool_result cleared - Bash: git status --short - 4.2k chars - re-run to restore]
```

Per §1.2, **nothing can write that**. `PostToolUse` cannot replace result content, and the
transcript is not writable. Two things follow, and they point in opposite directions:

1. **The native clearing in §1.1 may already be violating this invariant on every session**,
   since what it leaves behind is undocumented and plausibly nothing. If so, the harness is
   manufacturing false absences today, silently, and no policy written here changes that.
   §6.1 specifies the experiment that settles it.
2. **The invariant is still achievable — by inverting when the marker is written.** A stub
   cannot be placed *at clearing time*. It can be placed *at capture time*, before any
   clearing is possible. That is §4.2.

---

## 4. What is actually implementable, seam by seam

`hooks/cost-discipline.py` today handles `PreToolUse`, `PostToolUse`, `SessionStart`,
`PostCompact` and `UserPromptSubmit` (3,912 lines; 207,269 bytes). It contains **no**
clearing, pruning, stripping or transcript-rewriting logic — verified 2026-08-21 by reading
every one of ~140 matches for `clear|prune|strip|truncat|elide|stub|tool_result|transcript|
compact` across all 3,912 lines. Every match is advisory text emitted to the model, string
normalization during command parsing, a counter reset, or the wiki scanner's own coverage
counters.

Two existing seams are worth naming because they are already half-built:

- Lines 3610–3611 already emit advice to the model: *"Tool-result clearing is the
  lightest-touch compaction (Anthropic): /compact now, or /handoff + /clear…"*. The
  recommendation exists; no mechanism backs it.
- `handle_post_compact` (line 3764) already zeroes a **`tool_result_chars`** counter, so the
  L4 context ledger in `handle_post_tool` is already measuring tool-result volume.

### 4.1 `PreToolUse` — prevention, and the highest-leverage lever available

`updatedInput` replaces the tool's arguments before it runs. This is fully supported and
documented, with a worked example in the costs documentation that rewrites a `Bash` command.

Prevention beats cleanup here, and by a wide margin: a result that is never produced costs
nothing to produce, nothing to cache, nothing to clear, and cannot create a false absence.
Candidate rewrites — each to be specified and reviewed individually, none authorized by this
document:

- appending a bound to an unbounded listing;
- narrowing a field selection where a command supports one;
- redirecting genuinely bulk output to a file and returning the path.

**This lever is not what the task was opened about, and it is the one that most clearly
repays implementation.** It is recorded here as the recommended next design, not specified in
it — see §7.

### 4.2 `PostToolUse` — pre-place the marker at capture time

The move that survives §1.2. At the moment a **Tier 1** (non-re-derivable) result arrives,
`additionalContext` appends a compact durable record beside it: tool, target, the exact
command, and the finding in one or two lines. 10,000 characters is ample.

The bulky result may then be cleared by the native mechanism at any later point. The appended
record was written as a separate block and is not the result, so the model retains both the
knowledge that something was captured and the command to re-obtain it.

This inverts the original design: **the stub is not written on clear, it is written on
capture.** It requires no ability to modify a result, and therefore no capability the harness
does not grant.

**It rests on one unverified assumption**, and the design is not implementable until that
assumption is tested: that native clearing removes the `tool_result` block *without* removing
the `additionalContext` block appended after it. Nothing in the documentation says either
way. §6.2 specifies the experiment.

### 4.3 `UserPromptSubmit` / `SessionStart` — advisory only

Both can emit `additionalContext`. This is where a re-derivability reminder belongs, and it
is what the hook already does at lines 3610–3611. Advisory text is the weakest instrument
available: per CLAUDE.md's *"A Check That Ran Is Not a Check That Was Heard"*, a hook's
`additionalContext` reaching the model is a separate claim from the hook having fired, and
one of them is routinely false. Any advisory added here must be validated by planting an
input on which it is **required** to speak, then confirming the injected text arrives as an
`attachment` carrying `hook_additional_context` in the transcript — not as a `user` or
`assistant` line, which are artifacts of looking.

### 4.4 `PostCompact` — the wrong instrument, and a known trap

`PostCompact` does **not** accept `hookSpecificOutput.additionalContext`; its only output path
is the top-level `systemMessage`. The file's own comment at lines 3814–3819 records this, and
records it as something already paid for once:

> `NOTE: PostCompact does NOT support hookSpecificOutput.additionalContext (only PreToolUse /`
> `UserPromptSubmit / PostToolUse / PostToolBatch do). Emitting it there fails Hook JSON`
> `validation and the checkpoint silently never lands.`
> `(Fixed 2026-06-03 after observing the validation error in a live compact.)`

Two things to carry from that. The failure mode is **silent** — well-formed JSON with a
correct `hookEventName`, discarded at the boundary, with nothing distinguishing it from a
delivery that landed. And the event list in that comment is the authoritative one for this
harness: `PostToolBatch` accepts `additionalContext` too, which the documentation summary in
§1.2 does not mention. Do not attach policy to `PostCompact` expecting the model to read it.

### 4.5 Out of reach entirely

Editing an already-recorded result; rewriting a transcript entry; controlling *which* results
the native mechanism clears or in what order; observing what it leaves behind, other than
empirically.

---

## 5. Non-goals

- **A second clearing engine.** §1.1 — one already runs, and this document does not propose
  competing with it.
- **A solution to context growth.** §1.3 — tool results are roughly a seventh of the bulk.
  Any claim that this policy meaningfully reduces context is false, and stating the
  proportion plainly is part of the deliverable.
- **A thinking-block policy.** §1.4 establishes that thinking is in scope *economically* and
  is the largest bucket. It does not follow that it is addressable by the same means, and no
  seam for it has been identified. Naming it as the larger problem is this document's whole
  contribution on the subject.
- **Code.** No implementation is authorized by this document.

---

## 6. Open questions, each with the experiment that settles it

### 6.1 What does the native mechanism leave in place of a cleared tool result?

The most consequential unknown in this design. If it leaves nothing, the harness manufactures
false absences on every long session and §4.2 becomes the mitigation rather than an
enhancement. If it leaves a marker, §4.2 is redundant and should be dropped.

**Experiment.** Drive a session past the auto-compact threshold with a deliberately
identifiable bulky tool result — a unique sentinel string in the output. After clearing,
inspect the transcript for the sentinel and for whatever occupies that position. Lower
`autoCompactWindow` to reach the threshold cheaply. Report what was found *and* the
denominator: how many blocks were walked.

### 6.2 Does an appended `additionalContext` block survive clearing of the result it annotates?

§4.2 is unimplementable if the two are cleared together. Same experimental setup as 6.1, with
a `PostToolUse` hook appending a second sentinel; check which of the two survives.

### 6.3 Does anything under `tests/` exercise `hooks/cost-discipline.py`?

Reported as **not settled** by the 2026-08-21 recon, which established the lint scope but did
not open individual test files. It decides how much of §7's review burden is already carried.
Settle it by grepping `tests/` for an import of the hook, and state the denominator.

### 6.4 Should the policy be enforced at all, or only stated?

Genuinely open, and it is the operator's call rather than a technical question. CLAUDE.md
already records the asymmetry that makes it live: the read-discipline floor is a hard block
with teeth, while the over-delegation ceiling is prose and is not enforced — on a model whose
documented failure mode is the thing the prose forbids. A re-derivability policy stated as
prose in CLAUDE.md would sit on the unenforced side.

---

## 7. Implementation constraints, should any of this proceed

- **`hooks/` is hot-path and effectively unguarded by CI.** `portability.yml` triggers on
  every push and PR to `main` with no `paths:` filter, so a change under `hooks/` does run
  CI — but the lint step is literally `ruff check lib tests`, which excludes `hooks`. The
  restriction is in the workflow's arguments, not in `ruff.toml`, which excludes nothing. The
  task row's claim that "the repo has NO CI" is wrong for this repository; the *conclusion*
  it supported is right for a sharper reason — **CI is green on a path it does not lint and
  probably does not test.**
- **Independent review is therefore mandatory**, per CLAUDE.md, using
  `subagent_type: "feature-dev:code-reviewer"` with `model: sonnet`. That agent's tool list
  carries `BashOutput` **without** `Bash`: it cannot execute anything, so every finding it
  returns is a hand-trace of code it has only read. **Reproduce each finding by execution
  before acting on it.**
- **Never push directly to `main`**, docs-only changes included, admin bypass
  notwithstanding. PR + CI, per this repo's CLAUDE.md.
- **Prefer §4.1 to §4.2.** Prevention is supported, documented and carries no unverified
  assumption. §4.2 is blocked on 6.2 and must not be built before it.

---

## 8. Provenance

Written 2026-08-21. The 2026-08-20 scoping is preserved verbatim in fleet task **#42**; where
this document contradicts it, the contradiction is deliberate and §1 carries the evidence.

Three claims here are inferences rather than citations and are marked as such in place: what
the native mechanism leaves behind (§1.1), that platform-level thinking-block caching applies
unchanged inside Claude Code (§1.4), and that the transcript is append-only from a hook's
perspective (§1.2, inferred from the absence of any write API plus the behaviour of `/rewind`
and `/compact`). None should be restated without its hedge.

Related, and worth linking rather than repeating:
`docs/core/brain/claude-core/checks-that-cannot-fail-2026-07-30.md`,
`docs/core/brain/claude-core/a-gauge-with-someone-elses-label-2026-08-11.md`,
`docs/core/brain/claude-core/the-payload-the-model-never-sees-2026-08-12.md`.
