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

- **Model inheritance — still open.** A relay child takes the parent's model, so
  an Opus parent silently makes an Opus child. Real, but secondary: on Haiku those
  reads would have been cheaper, and they still should not have been inline. Worth
  asking whether a relay dispatch should carry an explicit model floor rather than
  inheriting.
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
