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

Every layer that should have caught this had a reason not to, and they are worth
separating because they need different fixes:

- **Model inheritance.** A relay child takes the parent's model by default, so an
  Opus parent silently makes an Opus child. Nothing in the dispatch says "this
  child does log reading", which is Haiku work.
- **The reader-agent reflex is skill content**, so it only fires if the child
  loaded `delegation-discipline` — and a child primed from a relay message may
  never have.
- **The hard-block tier could not fire.** Whether it was armed depends on
  `$CLAUDE_JOB_DIR`, which is the inverted-exemption defect (see PR #23): a
  background child is exempt outright, and a foreground one shares its parent's
  counter. Neither case is the one the tier was written for.
- **Warnings are ignored 82–88%** per the 2026-06-27 audit, which is why the block
  tier exists at all — and this instance never reached it.

This is the first observed *end-to-end* instance of the waste pattern the whole
harness exists to prevent, with a human as the only functioning gate. Worth
treating as the motivating case when the agent-detection fix lands, and worth
asking whether a relay dispatch should carry an explicit model floor rather than
inheriting.

### Promote the hot-path review rule to CLAUDE.md

Candidate rule: **"Independent review before merge for hot-path code, mandatory
where there's no CI."** Flagged in a prior compaction and exercised throughout the
2026-07-17→20 session — every `hooks/`/`lib/` change got an independent review
before merge, and that review repeatedly caught real bugs (incl. two "green
because it isn't looking" instruments). Decide the wording and whether it belongs
in the generic CLAUDE.md trunk (under *Approach & Scope*) or is claude-core-scoped.

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
