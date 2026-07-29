# claude-core — open follow-ups

Durable index of open follow-ups from the cost-discipline reporting work
(session 2026-07-17→20). Nothing here is gated — pick up on request.

## claude-core

### cost-ledger report — deferred review findings #2–4

From the PR #12 `/code-review` (findings #1 and #5 were fixed then; #2–4 deferred):

- Throttle the per-call cost-ledger disk write — it currently writes on every
  `PostToolUse`.
- Extract a shared `_atomic_write_json` helper; the tmp-write + `replace` pattern
  is inline at each write site.
- Collapse to a single tail-write in `handle_post_tool` instead of writing the
  ledger twice per call.

Touches `hooks/cost-discipline.py`; a `perf`/`refactor` change with no behaviour
change. Wants the same treatment as the rest of the suite: test + independent
review before merge.

### A relay child ran bulk log reads inline on Opus 5 — the discipline did not fire

Observed 2026-07-27, reported by the user. Session `8bdf4617` handed a task to
child `8d52d413`; the child inherited the parent's model (Opus 5) and pulled and
read GCP logs **directly in its own main context**, call after call, instead of
dispatching Haiku/Sonnet readers. It kept doing so until the human asked
*"maybe it would be much better to use subagents with sonnet/haiku for collecting
and reading logs instead of the expensive main session?"* — i.e. the correction
came from the human, not from the harness.

**Root cause, established 2026-07-28 by reading the child's transcript rather than
reasoning about it.** The child made 131 Bash calls and 26 sub-agent dispatches:
12× `gh-reader`, 6× `slack-reader`, 7× `general-purpose`, 1× `Explore`. It
delegated to **every reader that existed**. All 11 inline log reads were `gcloud`
— the one tool with no reader in the fleet.

So this was never an awareness failure. It is *the remedy inside the trap* one
level up: the remedy the rule names — dispatch a reader — did not exist for the
tool where it was needed, and **a remedy that does not exist from where you are
standing fails silently.** No error, no warning; the agent does the work itself
and looks compliant doing it, because it delegated everywhere a delegate was
available. See `docs/core/brain/claude-core/the-remedy-inside-the-trap-2026-07-27`.

The original four-layer analysis was written from the report, not the transcript.
Measured, it holds up unevenly — recorded here because the correction is the more
useful artifact:

- ~~**Model inheritance.** A relay child takes the parent's model, so an Opus parent
  silently makes an Opus child.~~ **DISPROVEN 2026-07-29, on two separate counts.**
  There is no inheritance: a child launched without `--model` gets whatever
  `settings.json` currently sets as the default, which coincides with the parent's only
  because the parent is also on the default — and the human picks the model per child,
  sometimes leaving one on Opus deliberately. (Deliberately stated as a mechanism rather
  than a value: that key read `opus[1m]` when this was written on 2026-07-29 and read
  `sonnet` a few hours later. A quoted default is a dated claim; the mechanism is not.) More decisively, **the behaviour
  the layer worried about never occurred**: all 29 dispatches in the child's transcript
  carried an explicit model and every one was strictly below Opus (18× haiku, 11×
  sonnet, **zero same-tier**). The relative discipline — delegate downward from
  whatever tier you are — held throughout. No model floor is needed; building one
  would automate a decision that was never broken.
- ~~**The reader-agent reflex is skill content**, so it only fires if the child
  loaded `delegation-discipline`.~~ **DISPROVEN, twice.** The relay inbox banner
  has carried the reflex verbatim since 2026-07-01 — three weeks before the
  incident — and `delegation-discipline` appears 19 times in the child's
  transcript. Worse for the original theory: `when-to-delegate.md` already listed
  `gcloud … list` by name as always-delegate. The rule was present, specific, and
  loaded.
- **The hard-block tier could not fire — confirmed, still gated.** `Read-discipline`
  appears 0 times in the transcript, consistent with the inverted `$CLAUDE_JOB_DIR`
  exemption (PR #23). Unblocking needs the sub-agent-detection measurement.
- **Warnings are ignored 82–88%** per the 2026-06-27 audit, which is why the block
  tier exists at all — and this instance never reached it.

**Fixed 2026-07-28:** `gcloud-reader` added to the fleet (read-only, Haiku, rosters
derived rather than recited), plus a `when-to-delegate.md` subsection making the
no-reader case explicit — fall back to a generic scout, never to main, and report
the gap. Six read-only `gcloud logging`/`config`/`services` allowlist entries were
added with explicit user approval, since a reader that prompts on every call
recreates the incentive to read inline.

**Still open:** the model floor on relay dispatch, and the hard-block tier, which
waits on agent detection. This remains the first observed *end-to-end* instance of
the waste pattern the harness exists to prevent, with a human as the only
functioning gate — and the lesson that survives is about coverage: a discipline is
only as good as the reach of the remedy it names.

### Tool-output residency, not dispatch count, is the dominant pollution channel

Measured 2026-07-29 against one session's own transcript (`4ca1e8fd`, ~2,150 tool calls):

| Tool | Calls | Chars | Avg | Share |
|---|---:|---:|---:|---:|
| Bash | 1214 | 1,136,873 | 936 | 49% |
| Read | 180 | 806,838 | 4,482 | 35% |
| Agent | 94 | 167,041 | 1,777 | 7% |
| Edit | 413 | 76,031 | 184 | 3% |

**2.31M chars ≈ 577k tokens** passed through one session's context, each re-billed as a
cache read on every subsequent turn until compaction dropped it. Three consequences, two
of which contradict what the skills currently say:

1. **A dispatch puts ~2.5× less into context than a direct `Read`** — 1,777 vs 4,482
   chars per call, because it returns a conclusion instead of the source. 94 dispatches
   caused 7% of the pollution; 180 reads caused 35%. This is the measured argument for
   delegation and `delegation-discipline` does not carry it.
2. **The waste is the long tail, not the big dumps.** The ten largest single results are
   **14%** of the total. "Trim large outputs" is therefore nearly useless here; only
   "make fewer calls" moves the number. The skill's per-call size rule (`Grep expected
   to return >50 lines — narrow first`) targets the wrong shape.
3. **The delegation break-even is mis-stated everywhere we state it.** It is not
   dispatch-cost vs read-cost; it is dispatch cost vs read cost *plus that read's
   residency across every remaining turn*. An inline dump early in a long session costs
   far more than the same dump late — a dimension no current threshold represents.

Work: rewrite the thresholds in `delegation-discipline/references/hard-rules.md` and
`when-to-delegate.md` around aggregate output volume, and carry the 2.5× figure. Gated on
the gauge below — picking a byte threshold from a single session is the mistake this repo
keeps cataloguing.

### The cost ledger is a near-empty gauge — fix it before setting any threshold

`~/.claude/cost-ledger/` holds **442 session files. Only 31 have a non-zero counter.**
93% recorded nothing at all. Three separate causes, and each needs its own fix:

- **It resets at every `SessionStart`.** The ledger is written from `/tmp` state, which is
  wiped on session start, so it measures the segment since the last start rather than the
  session. With resume and reload being routine this systematically under-reports: session
  `4ca1e8fd`'s ledger reads `tool_calls_total: 12 / tool_result_chars: 8208` for a session
  whose transcript holds ~2,150 calls and 2.31M chars.
- **Most files predate the ledger entirely** — they were written by sessions served by the
  plugin frozen at 0.2.0. See `partial-staleness-reads-as-fresh-2026-07-28`.
- **Dispatches are not tracked at all.** There is no such field; an aggregation that
  reports "zero dispatches" is reading a key that was never written. (Caught here as an
  instrument error before it became a finding.)

The field `cache_reread_usd_per_turn_est` already estimates exactly the residency cost
described above — computed from an input understated by orders of magnitude.

**This is the highest-leverage item on the page.** We currently have neither a working
gate (the block tier was exempted for background mains) nor a working gauge, which is why
the rules *feel* weak: there has been nothing to tell us either way. The only trustworthy
number we have — "warnings ignored 82–88%" — comes from the fire log, a different
instrument.

### Gate on bytes, not on call count

The hard-block tier counts *calls* (streak 4, aggregate 15) while the hook already
maintains `tool_result_chars` and `tool_result_chars_by_tool` and only reports them. The
counter that matches the cost is present and ungated.

Also make the warning attribute: *"Bash has put 1.1M chars into this session, 49% of your
context"* is actionable in a way *"15 reads"* is not — the per-tool breakdown is already
in state.

Sequenced after the gauge, and wants the hot-path review treatment.

### A reader's model pin is a default, not a floor

Every reader agent is pinned `model: haiku`, but a dispatcher passing `model:` overrides
it — and **3 of 29 dispatches did**, escalating `gcloud-reader` and `gh-reader` to Sonnet.
Sometimes justified (a complex log filter), but *"all readers are pinned haiku"* and *"all
reader dispatches ran haiku"* are different claims and they diverged in practice. Decide
whether the pin should be advisory (status quo, documented) or enforced, and if enforced,
where — the Agent tool takes the caller's word.

### Agent detection — the last live layer of the relay-child incident

`blocks_enabled()` exempts "agent" sessions; `detect_session_mode()` keys on
`$CLAUDE_JOB_DIR`, the *background-job* signal. So it exempts background mains and blocks
foreground sub-agents — inverted on both sides. Two probes ruled out the Bash-visible
environment. A payload-shape probe now runs behind `/tmp/cc-payload-probe.path`
(claude-core `7df0988`, `chore(hook)`), and a foreground-main baseline is captured; the
missing arm is a foreground sub-agent from the same session. Delete the probe block and
`tests/test_payload_probe.py` in the same PR as the fix.

Concrete cost of the defect, measured: session `4ca1e8fd` made ~2,150 tool calls with the
block tier silently off, because `$CLAUDE_JOB_DIR` is set for a background main.

### Promote the hot-path review rule to CLAUDE.md

**DONE 2026-07-28.** Landed in `~/.claude/CLAUDE.md` as its own section, deliberately
adjacent to *Verify Against the Real Artifact* — the two are siblings: one is about
what you exercised, the other about who else looked. Scope question resolved in
favour of the user-level file rather than this repo's trunk, on the same reasoning
already recorded there for the Cloud Auth rule: a rule must load **before** the
decision it governs, and a trunk that only loads inside this repo does not.

Wording settled on the sharper trigger — not "where there's no CI" but **wherever CI
does not actually exercise the changed path**, since a green suite and a lint gate
scoped to `lib/` + `tests/` say nothing about `hooks/`. It carries its own evidence:
the block tier held three live defects at once through a green suite because that
path had never had a single test, and two review rounds on one branch here produced
commits titled "repair five defects found by review" and "correct four defects found
by self-review" — nine real defects on a branch its author believed finished.

First application, the same day: the 0.2.0 → 0.11.1 plugin upgrade was reviewed
before being applied rather than after.

### What today's measurements say about ccm-lite

Worth pulling on, because the two systems reached the same result from opposite ends.

ccm-lite's thesis is **bounded reasoning context** — route → expand → **distill** → reason
→ verify — and its clean structural win in the 2026-07-20 eval was exactly that: ~half the
final reasoning-call context. The agent-session measurement above is the same mechanism in
a different substrate. A Claude session's context *is* the reasoning window; tool output is
the retrieved corpus; a sub-agent is a distill step. Measured: a dispatch returns 2.5× less
per call than a raw read. Neither result was derived from the other.

The sharper parallel is where both were forced to qualify it. ccm-lite's retry-verified run
**reversed** the answer-quality reading — the raw-dump arm scored highest on `answer_hit`,
because a strict-substring metric structurally favours verbatim text and distillation
paraphrases. The reader agents hit the identical wall from practice, which is why
`kubectl-reader` and `gcloud-reader` both carry an explicit rule: summarize for
orientation, but return logs **VERBATIM** when debugging a failure, because a paraphrased
stack trace is worthless to whoever has to grep it.

So both systems independently arrived at: **distillation must be conditional on the
question, not a property of the pipeline.** ccm-lite learned it from a metric artifact; the
fleet learned it from a bad on-call experience. That convergence is the interesting part,
and it suggests a concrete question for ccm-lite — does its distill stage know what kind of
question it is answering, or does it distill uniformly? If uniformly, the agent side has a
ready-made answer for it. Full context:
`docs/core/brain/claude-core/measuring-interventions-controlled-ab-2026-07-20`.

## Tracked elsewhere (pointers, not duplicated here)

- **ccm-lite eval rigor** — claim-linked provenance (separate span- from
  source-level), Agent SDK retry (~16% "max turns" error rate), score
  `answer_hit` over reasoned-only to de-confound errors, grow the golden set
  17→20+. → `~/dev/ccm-lite/eval/results/2026-07-20-first-live-eval.md`
- **Adopt new Claude Code levers** — `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` /
  `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` caps, OTel `tool_source` as a cleaner
  cost-audit feed than JSONL scraping, and a `dir/**` permission-rule audit.
  → auto-memory `reference_claude_code_features_for_cost_discipline_2026-07`
- **Fair session before/after metric** — the confounded path was shelved for the
  controlled ccm-lite eval; a valid version needs same-population denominators plus
  compaction normalization (per-session means don't control for task mix or
  compaction frequency). → PR #17 review thread.
