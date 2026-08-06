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

#### Design settled 2026-07-30: segments, not a running total

The hard part was never the reset — it was how to accumulate across `SessionStart`
without double-counting, given `/tmp` state is wiped while the ledger persists. Three
candidates, and the reason the first two lose is worth keeping:

- **Sum on write — WRONG.** `write_cost_ledger` is called on every `PostToolUse` with
  the *running total*, not a delta. Adding that to a stored total on each call
  multiplies rather than accumulates.
- **A `carried` baseline read at `SessionStart`** — works, but conflates segments into
  one number and puts the arithmetic on the hot path, where a mistake is silent.
- **Segments — chosen.** `SessionStart` **appends** a segment; `PostToolUse` updates
  only the **last** one; `totals` are summed at write time.

```
{ session_id, cwd, main_model,
  segments: [ {
    started_at,
    tool_calls_total,       # DELTA vs state["segment_base_tool_calls"], not the raw
                            #   counter — tool_calls_total is monotonic across compaction
                            #   (the router nudge indexes on it), so storing it raw would
                            #   make the sum count every pre-compaction call twice
    metered_results, tool_result_chars, aggregate_reads,
    by_tool,                # {tool_name: chars}
    dispatches,             # {subagent_type: count} — sourced from
                            #   tool_input.subagent_type at the DISPATCH_TOOLS branch in
                            #   handle_pre_tool, counted only on an ALLOWED dispatch
    result_bytes_buckets,   # {lower_edge_str: count}, log-scaled, bounded
  } ],
  totals:   {…summed from segments at write time; nothing else is a source…} }
```

The annotations above are deliberate: an earlier revision listed `dispatches` here with its
sourcing stated twelve lines below, under a paragraph a partial read would miss — so the
schema appeared to name a field that did not exist. A reader must not need a later line to
trust this block.

**Every field a segment carries must be reset at a boundary**, or the closed segment and
the newly opened one both carry it and the sum double-counts. `tool_calls_total` is the one
exception, handled by moving its per-segment base rather than zeroing the counter.

Append-only means no arithmetic can go wrong, and it answers strictly more: a
resume/compaction boundary is exactly where behaviour changes, and per-segment data
makes that visible instead of averaging it away.

**Do not suppress empty segments.** A session that started and did nothing is a real
observation; filtering it to keep the file tidy would be this entry's own vacuity trap
one layer up. Append it and record the segment count.

**Dispatch counting** sources from `tool_input.subagent_type` on the dispatcher's
`Agent` call. Count **by type**, not just a total — the question is "did this session
delegate, and downward to what?", which a bare count cannot answer.

**Sequencing constraint, and it is a hard one.** `build_cost_ledger(state)` reads its
counters *flat* off the state dict. The agent-detection work (entry below) may nest
state under `agent_id` for per-agent block scoping — and if it does, these reads find
nothing and the ledger writes **zeros**, which is *indistinguishable from the bug this
entry exists to fix*. There would be no way to tell "the gauge works and the session
was quiet" from "the gauge is reading the wrong dict". So: the gauge lands **after**
agent detection, and that work must keep session-level counters flat while nesting only
the per-agent ones. Note `aggregate_reads` is read by *both* the block tier and the
ledger; if it splits, the two halves need different names, because a name collision is
how this class of defect survives review.

**Coupling to the deferred perf finding** at the top of this file: a segments list makes
the per-`PostToolUse` write larger, which raises the priority of throttling that write
from "nice" to "do it in the same PR".

**Throttle: skip unchanged writes, NOT every Nth write.** A count-based throttle (write
when `metered_results % 5 == 0`) was implemented first and rejected on measurement: a
session with fewer than 5 metered results then holds only the zeroed segment
`SessionStart` opened, so the ledger reads **zeros for a session that did work** — the
same "indistinguishable from the bug this entry exists to fix" state the sequencing
constraint above warns about, reintroduced by the optimisation. Six existing cost-ledger
tests failed on it. Skipping writes whose reported counters are *unchanged* is lossless by
construction and still removes the redundant write on the dispatch / unmetered paths. The
signature it compares must be JSON-native — state round-trips through JSON, and a nested
tuple returns as a nested list that never compares equal, which would leave the throttle
permanently dead while still looking implemented.

### `Bash` counts toward the read streak content-blind

Reported by the parallel Skill-Builder session on 2026-07-27 and unactioned since —
recorded here so it stops living only in an inbox.

`READ_TOOLS = {"Bash", "Read", "Grep", "Glob"}` and the aggregate/streak increment
inspects `tool_name` only, never the command. So `git commit`, `git push`,
`downbeat send`, a test run and a build all count as *reads*. **A session inflates
its own read counter with its writes, and the more real work it does the faster it
trips a threshold meant to catch inline reading.**

Measured relevance: of the ~2,150 tool calls in session `4ca1e8fd`, 1,214 were
`Bash`, a large share of them commits, pushes, relay sends and test runs. Whatever
`aggregate_reads` measures, it is not reading.

**Do NOT fix this by classifying command content.** That is a classifier, it will be
wrong in both directions, and "unknown command → counts as a read" is a fresh
unknown-treated-as-permissive default in the one file that already has a wiki page
about them. The cheaper move is to drop `Bash` from the STREAK while keeping it in
the aggregate: the streak is about consecutive inline *reading*, and `Bash` is the
only member of that set which is routinely a write. Cost of that change: it gives up
catching the `Bash(cat)` / `Bash(ls)` substitution case via the streak, which the
streak was partly written for — so it wants its own decision rather than riding
along with another PR.

**SUPERSEDED, not deferred** — at the reporter's own request (2026-07-29), and the
reasoning is theirs: the recommendation above was *"the least-bad way to keep a metric
that measures the wrong thing."* Byte-gating does not need the read/write
classification at all, so the misclassification cannot occur rather than being
mitigated. If the byte counter lands, drop the drop-Bash-from-the-streak proposal
instead of leaving a live plan pointing at the old metric. Recorded explicitly
because a superseded proposal left standing reads exactly like an open one.

**It fired live on 2026-07-30, on a merge.** The streak block refused
`git checkout main && gh pr merge 35` at ten consecutive calls — a *write* operation,
refused as inline reading, in the middle of landing the very PRs that repair this file.
So the entry now has a first-hand instance and not only a reported one, and the instance
is the strongest available argument for byte-gating: no plausible read/write classifier
would have to be argued about, because bytes returned by `gh pr merge` are near zero
while bytes returned by a wide `grep` are not.

**It then fired a second time, ~40 minutes later, on the same command shape** —
`git checkout main && gh pr merge 36`, the merge of the very PR that files this
observation. Two refusals of a merge in one session promotes this from an anecdote to a
reproduction: it is not a rare coincidence of counter position, it is what happens
whenever a session lands PRs, because landing a PR is a run of `gh`/`git` calls and every
one of them is counted as a read. The measure and the behaviour it is meant to discourage
have no relationship on this path.

**Third refusal, same session, and it widens the population beyond `git`/`gh`.** The
block fired again while the session was doing nothing but *sending* — two `downbeat send`
relay messages and a final state check. `downbeat send` is a write in every sense that
matters: it delivers a message to another session, returns a 12-character id, and reads
nothing. It is counted as a read because it is spelled `Bash`.

So the misclassified population is not "merges" — it is **every write this harness
performs through a CLI**: `git`, `gh`, `downbeat`, and by extension any future tool that
is a command rather than a first-class tool. That is most of what a session does when it
is being productive, and it is exactly the set that a read-discipline counter should be
blind to.

**Fourth refusal — and the aggregate tier makes the RELAY unreachable.** The block moved
from the streak tier to the aggregate tier at 40 calls, and the call it refused was
`downbeat reply` — answering a peer session's message. The relay is a CLI, so it is
spelled `Bash`, so a session over the aggregate threshold **cannot answer its mail.** The
channel through which this harness coordinates between sessions is gated by a counter that
believes coordination is reading.

Observed, not predicted: the refusal was attempted rather than inferred, and it is free to
attempt because refused calls are no longer counted — the v0.11.1 fix behaving exactly as
designed, which is what made testing the assumption cost nothing. `Edit` and `Write` are
NOT in `READ_TOOLS`, so this note could still be written; only the commit and the reply
could not.

**A new shape in the remedy family, sharper than the two already recorded.** The aggregate
tier offers exactly two remedies — dispatch, or `/clear` — and for this session:

- **dispatch** was unavailable for a reason outside the hook (a standing instruction not
  to dispatch agents unless the user asked);
- **`/clear`** would reset the counter and restore the ability to reply — *by destroying
  the context needed to compose the reply.*

So the exit is neither closed (as in the original incident) nor inert (as in the aggregate
write case). It is **an exit that destroys the work it unblocks.** A remedy whose cost is
precisely the thing it is being taken for is worse than an inert one, because an inert
remedy wastes an attempt while this one succeeds and still loses.

This is the strongest argument on this page for the aggregate tier needing a remedy that
is neither a dispatch nor a context reset — and, separately, for the relay CLI being
exempt from read counting on its own merits, since `downbeat send`/`reply` cannot read
anything by construction.

One further observation, recorded WITHOUT a mechanism because the running copy was
0.11.2 while `main` was at 0.11.5 and this page's neighbour says not to infer a version
from behaviour: the streak was **not** reset by an intervening `TaskUpdate`. Whether the
non-read reset path covers task tools at all is a question for the code, not for this
observation.

Worth stating plainly, since it is the cost this entry is arguing about: the streak tier
currently **taxes the highest-value ten calls a session makes** — reviewing, verifying,
and landing work — at exactly the moment when interrupting is most expensive, and it does
so on the basis of a number that measured none of it.

Two side observations from the first refusal, both worth keeping:

- **Remedy (a) was unavailable for a reason outside the hook.** The message offers
  "dispatch a Haiku reader", but this session runs under a standing instruction not to
  dispatch agents unless the user asked. So the printed remedy set was, in practice, one
  item shorter than it looked. A guard cannot know the caller's standing constraints —
  which is an argument for every printed remedy being independently sufficient, not for
  the hook trying to detect them.
- **Remedy (b) is unreachable for any write that needs a read first.** "Write/edit
  something concrete" was the remedy taken, but the edit actually wanted was to *this
  file*, and `Edit` requires a prior `Read` that the block itself refuses. The way out
  was to write a NEW file — which happened to be genuinely owed work rather than
  make-work, by luck. Same family as the page
  `docs/core/brain/claude-core/the-remedy-inside-the-trap-2026-07-27` records: a remedy
  is only reachable if it needs nothing the blocked state denies.

**Third remedy shape, from the parallel session hitting the same tier independently: the
remedy set is EMPTY for the state that triggers it most.** Their framing, and it is sharper
than either of mine:

Of the three exits, **only the write preserves context.** A dispatch is a different
session's work and a `/clear` destroys the reason you were reading. So the write is the good
exit — and it is available only when a write is *already* the next thing you were going to
do. Mid-measurement it is structurally unavailable, because you are reading precisely
because you must not yet change anything: they were holding stamp fingerprints and fixture
state, and a write would have invalidated the measurement the reads were serving.

For that state the tier's three remedies reduce to: **destroys context, forbidden, or
requires you to do the thing the measurement forbids.** Not "the exit is closed" and not
"the remedy is inert" — the set is empty. And it is empty exactly for the read-heavy
investigative work that trips a read counter fastest, which is the population the tier is
aimed at.

They got out only because their next step happened to be a write (planting a fixture), so
the streak reset for free. That is luck, not a remedy.

### The statusline HUD and the guard now measure different numbers

Found 2026-07-30 while checking whether a scout's consumer list was complete. It is my own
review miss, from the PR that introduced per-agent scoping.

`~/.claude/statusline-hud-wrapper.sh:46-49` reads the working state file directly:

```sh
disc_file="/tmp/cc-discipline-${session_id}.json"
reads=$(jq -r '.aggregate_reads // 0' "$disc_file" ...)
total=$(jq -r '.tool_calls_total // 0' "$disc_file" ...)
```

…and colours `reads` yellow/red against a threshold of **15** — the aggregate warn
threshold. But since per-agent scoping landed, the warn and block tiers fire on
`_scoped["agent_reads"]`, while the HUD still displays the **flat, session-wide**
`aggregate_reads`. Those are now different counters. So the number the human watches and
the number that acts on them can disagree, in either direction: the HUD can sit green while
a scope is one call from a block, or sit red while nothing is close to firing.

**This is the same defect I required to be fixed one layer over and did not think to look
for here.** The review of the scoping PR insisted the fire log record the counter that
actually fired rather than the flat total — and then never asked what ELSE displays the flat
total. Fixing an instrument's log while leaving its dashboard on the old field is a partial
migration of exactly the kind this page keeps cataloguing.

Work: decide what the HUD should show, rather than mechanically repointing it. Candidates —
the current scope's `agent_reads` (matches what will block *you*), the max across scopes
(matches "is anything about to fire"), or both numbers. The flat field is still maintained,
so nothing is broken today; it is simply no longer the number the guard uses.

Note for whoever picks this up: the statusline is in `claude-harness`, a different repo from
the hook, with no CI and no test suite. A change there is verified by looking at a running
statusline, not by a green build.

### An instrument that records it RAN does not record that its result ARRIVED

Raised 2026-07-30 by the parallel session, from a case that no amount of firing evidence
could have explained — and it re-scopes the fire-log proposal rather than confirming it.

They established, by planting a fixture and comparing a stamp fingerprint across one
prompt, that their `UserPromptSubmit` pulse **is registered, loaded, and executing** — the
stamp carried the fleet fingerprint *including* the fixture and a run timestamp. Valid
JSON, correct `hookEventName`, a 655-char `additionalContext` naming both fixture paths,
0.07s against a 5s timeout. And **the nudge never reached their context.**

The discriminator came free from an unrelated `/relay-reply`: a relay message was sitting
in their inbox **with no banner**. So it is not that hook and not a merge-order problem
between two `UserPromptSubmit` entries — no `UserPromptSubmit` hook in that session has
materialised `additionalContext` at all since a known timestamp. The channel accepts the
payload and drops it.

**The generalisation, which applies to everything we built this week:** a stamp, a fire log
and a counter all keep working perfectly while the reader hears nothing. Every instrument in
this repo records that a check *happened*; none records that its result *arrived*. For a
check whose only product is text in someone's context, "ran" is not the outcome — "was
read" is. Same landed-vs-live split as repo-versus-installed-versus-running, one layer
further out.

So the fire-log row for an advisory wants **both** halves: the `session_id` that fired it
(did it run, and inside which session) **and** whether the payload was non-empty and handed
off (was there anything to deliver). Neither alone distinguishes healthy silence from a
severed channel — and a log recording only the first would have answered their question
with FIRED, closing the item as working while the nudge stayed invisible on every prompt.

Build the fire-log — it answers "did it run" permanently and in one query. Just do not let
it certify the wrong claim.

### Nothing compares the installed plugin version to the repository

**Fourth occurrence, 2026-07-30, and this one was PREDICTED rather than stumbled into** —
which is the only reason the next finding surfaced. Merging the ledger fix cut a release, so
the repository moved to 0.11.7 while the install sat at 0.11.5. Checking deliberately, at
the moment the drift was known to be created, is what turned a recurring accident into a
reproducible experiment.

**CORRECTED 2026-07-30, and the correction matters more than the original claim.** This
entry first said the refresh command "lied about what it did". **It did not**, and two of
the three facts offered as evidence were misread. The corrected account:

`claude plugin update claude-core-hooks@claude-core-local` reported `✔ updated from 0.11.5
to 0.11.6`. That was **true**. The local checkout was at 0.11.6 — a real release, cut from
a parallel session's merged PR — and the command installed exactly that. It reported the
version it installed.

Two pieces of "evidence" that were nothing of the kind:

- **"The installed sha256 was byte-identical to the earlier copy, so nothing was
  installed."** Wrong inference. `hooks/cost-discipline.py` is byte-identical between
  0.11.5 and 0.11.6 — that release touched `doctor.sh`, `ROADMAP.md`, `CHANGELOG.md` and
  the two version files, and nothing else. **An unchanged hash across a version bump is
  evidence that the file did not change, which is the common case — not evidence that an
  install failed.** To test whether an install took, compare against the version you expect
  to be installed, never against the previous install.
- **"It named a version one release behind the repository."** True and irrelevant to the
  command's honesty: it named the version of the source it was told to install from, which
  was my checkout, because I had not pulled.

The one piece of evidence that WAS sound: the markers from the change I was looking for
(`_segment_from_state`, `result_bytes_buckets`, `dispatches_by_type`) were absent. A marker
drawn from the specific change under test discriminates; a hash comparison against the
previous install does not.

**So the defect was entirely in my order of operations, and the entry originally blamed the
tool for it.** That is the second time in one day this page's author mischaracterised a
correctly-behaving CLI — the first was claiming `register` silently re-homes when it
refuses carefully. Both times the tool was the easier suspect than the sequence.

The mechanism, and it generalises past this CLI: **a `-local` marketplace installs from the
local checkout, not from the remote.** The command had been run before `git pull`, so
"update" meant "re-copy whatever is on your disk", and the version it announced came from
the *stale* `plugin.json` it was copying. A success message that names a version is only as
fresh as the source it read the version from — so the message cannot be evidence about the
thing it just installed.

Two rules follow, both cheap:

- **Pull before you update, always** — and treat `update` from a local source as "sync from
  working copy", not "fetch the latest".
- **Verify against the version you EXPECT, not against the previous install.** Hash the
  installed file against the repository file at the ref you meant to install, and grep the
  installed copy for a marker drawn from the specific change you are looking for. The
  command's version string is a true statement about its source, which is not the same
  question as "is the change I want now installed" — and a hash comparison against the
  *previous* install answers neither, because most releases do not touch most files.

This is the same shape as everything else on this page — **a true report answering a
different question than the one being asked** — with the twist that here the misreading was
the reader's, not the reporter's. The tool said what it did; I checked it against the wrong
baseline and then wrote up the tool. The general form is worth more than the instance: when
a command's report and your expectation disagree, the sequence you ran is a cheaper suspect
than the tool, and it is the one you can actually inspect.

Raised 2026-07-30, third recurrence of the same drift. The repository reached 0.11.5
while `installed_plugins.json` still read 0.11.2 — stale by three releases, including
the per-agent-scoping fix reviewed and merged an hour earlier.

The wiki page for this
(`docs/core/brain/claude-core/partial-staleness-reads-as-fresh-2026-07-28`) already
prescribes the fix in words: *compare the install paths on a schedule, not on suspicion
— the whole problem is that suspicion never arrives.* Nothing implements it. The
comparison has run exactly as often as a human has wondered, which is three times, each
time after the drift had already misled someone.

**Why it recurs rather than sits still:** every `fix:` merge cuts a release, so the gap
widens once per merge — fastest during the sessions actively repairing the hook, whose
authors are most confident their changes are live. On 2026-07-30 a block fired and was
read as evidence the new scoped counters worked; it came from 0.11.2's flat counter.

Proposed shape, deliberately small: in the harness-hygiene pulse, read the authoritative
version from `installed_plugins.json` and compare it to the repository's `pyproject.toml`
version. Nudge on mismatch, naming both numbers. Constraints that matter:

- **Resolve from the record that owns the answer**, never a directory listing — three
  versions coexist in the cache and the listing order is lexical, so every positional
  selector over it is wrong (`0.11.1 0.11.2 0.2.0`: the active one is in the middle).
- **"Could not look" is a distinct outcome from "in sync"** — absent manifest, absent
  repo, unparsable JSON. Log the outcome even when silent, same as the wiki-index check,
  or a check that never ran is indistinguishable from one that found nothing.
- **`claude plugin update` reports "restart required to apply"**, so there are three
  states, not two: repository, on-disk copy, and the copy the running process serves. A
  detector can honestly compare the first two; it cannot claim the third. Say which one
  it checked, in the nudge text.

### Shape a Bash command to return the ANSWER, not the material

Raised 2026-07-30 from a real call. Three separate defects in one command, which is
why it is worth an entry rather than a habit:

```
Bash(cd /Users/…/claude-core-wiki && grep -n 'ct">\|cap"><span>\|eyebrow">\|…' \
     diagrams/downbeat-roadmap.html)
```

**1. Measured waste: 58% of that output was markup.** 1414 chars returned, 600 chars
of actual information (`Group writes during a rename #56`), so **814 chars entered
context for nothing** — ~203 tokens on one call, then re-billed as a cache read every
subsequent turn until compaction. The wanted answer was a list of roadmap titles; what
arrived was `<div class="ct">…<span class="ct-ref">#56</span></div>`.

The generalisable rule: **ask the shell for the answer, not for the material to derive
the answer from.** A grep whose output you then read is a two-stage operation with
stage 2 running in the most expensive context available. Move stage 2 into the
command — `grep -o`, a `sed`/`python3 -c` transform, `--output-mode=count`,
`files_with_matches`. Same information, less residency.

**2. It should not have been `Bash` at all.** The `Grep` tool exists, is separately
permitted (no command-string matching involved), and carries `head_limit` and
`output_mode` — which is exactly the stage-2-in-the-command lever. The trunk already
forbids `Bash(ls)`/`find` as Glob substitutes and says *"Bash because it has pipes"* is
not a justification; `grep` via Bash is the same rule with a different binary.

**3. The `cd X &&` prefix defeats the hook's own detectors — verified in code.**

Stated carefully, because a *different* claim about this prefix was retracted on
2026-07-28. **Retracted:** that `cd X &&` causes permission-approval latency — never
demonstrated, and a control run without the prefix timed identically. **Confirmed by
reading the code:**

- `is_cat_as_read()` is anchored `^cat\s+` **and bails outright when the command
  contains `&&`** (the metachar guard). So `cd X && cat file` slips the cat-block
  while a bare `cat file` is blocked.
- `is_ls_find_as_glob()` is anchored `^ls\b` / `^find\b`, so the compound form slips
  that nudge too.
- And a literal-prefix allow rule (`Bash(grep:*)`) cannot match a command whose first
  token is `cd`, so the compound form is a different string to every layer that
  matches on prefixes.

The sharp part: **the hook already knows how to unwrap this.** There is a
`first == "cd" and "&&" in s` unwrap that recurses on the inner command — in a
different function. The capability exists and is not applied where it matters most.

Work: extract that unwrap and run both detectors against the inner command.
**Caution — do not let it become a command parser.** Handle exactly
`cd <path> && <single command>` and decline anything more complex (`cd A && cat x && rm
y`) rather than guessing, or this becomes the content classifier that the entry above
was superseded for avoiding. Hot-path; wants a test per detector and independent
review.

Note the three defects fail differently and that is the point: #1 is pure cost, #2 is
a tool-choice habit, #3 is a **safety** hole where the guard is silent rather than
wrong. Only #3 gets worse the better the guard gets, because every anchored detector
added to that file inherits the same blind spot.

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

### The 2026-08-04 usage report — six of its recommendations already exist, and one is unfixable in CLAUDE.md

Source: `~/.claude/usage-data/report-2026-08-04-171352.html`, covering 2026-07-10 → 2026-08-04.

**Provenance first, because the report's own headline finding about this operator is
publishing counts that review has to correct.** Every figure below is the REPORT'S claim.
None was re-derived here. Restating them as measured fact would be the finding, committed
to the file that indexes the finding. Treat them as a lead.

| Reported | Value |
|---|---:|
| Messages / sessions | 676 across 23 |
| Bash / Edit / Read / Agent / Write / Skill | 1185 / 388 / 192 / 121 / 85 / 41 |
| Parallel-session overlap | 43 events, 20 sessions, 42% of messages |
| Primary friction | buggy code 10, wrong approach 7, excessive changes 4, misunderstood 4, tool failure 4, ignored instructions 3 |

Set that against the single-session table already in this file (Bash 1214 / Read 180 /
Agent 94 / Edit 413). Two different populations — one session's own transcript versus a
23-session window — so they are not comparable as ratios, and the interesting difference
is ordinal rather than numeric: **Edit is the second-most-called tool fleet-wide and was
3% of context chars in the single-session measurement.** Call frequency and context
residency rank tools in almost opposite orders, which is the thing the earlier section
exists to say and this report independently re-exhibits without noticing.

#### The recommendation that cannot be implemented as written

The report's first suggested `CLAUDE.md` addition is *"Always respond in English"*, citing
three ignored English-only requests in one session. **This is not a rule that was missing,
and adding it will not work.** The harness carries a session-level language setting that
instructs every turn to respond in Russian, and auto-memory records the opposite standing
preference (`user_language_preference.md`). A preference expressed in chat, and a rule
written into `CLAUDE.md`, both lose to a setting re-applied on every turn.

So the remedy is a config reconciliation, not a rule — and **that belongs to the user**,
same as the missing `MAX_SUBAGENTS` cap recorded below. Worth naming because the report
diagnosed an adherence failure where the mechanism is a contradiction, and shipping its
suggested fix would produce a rule that is violated by construction on every turn. Same
shape this repo already records twice: a remedy that requires something the agent cannot
grant itself.

#### Six suggestions that already exist — which changes what the fix is

| Report suggests | Already lives in | So the gap is |
|---|---|---|
| Re-derive any count you publish; state the command | global `CLAUDE.md` → "Absence Is a Claim About a Population" | adherence, not absence |
| Verify against the real artifact, not a test double | global `CLAUDE.md` → its own section | adherence |
| Every scout carries an explicit `model:` | `models-router` → "ALWAYS pass an explicit `model:`" | adherence |
| Treat subagent output as a lead, not a fact | global `CLAUDE.md` → attribution + uncertainty-marker rule | adherence |
| Keep drafts short, conclusion first | auto-memory `feedback_slack_conciseness` — **Slack-scoped only** | genuinely narrower than the finding |
| Don't re-ask for approval already granted | the standing reads/analysis grant | adherence |

**A rule that exists and is not followed is a different defect from a rule that is
missing, and it takes a different fix.** Writing the sixth copy of "re-derive your counts"
is the cheap move that has already been made five times. The mechanical form is what does
not exist.

#### The two genuinely new items, both mechanizable

1. **No-truncated-evidence guard.** `| head` on output used as a completeness claim. The
   report cites `git status | head -4` misread as complete. This repo has its own instance
   from 2026-08-04: `find … | head -1` returned plugin `0.2.0` out of nine cached versions
   against a live `0.11.2`, and nearly reported a shipped fix as absent. **Caveat that
   must survive into the implementation:** a hook cannot see intent, and `git log | head -3`
   for orientation is legitimate. A blocker would fire constantly and be disabled within a
   day. It has to warn, and the discriminator — "will this output be reasoned over as
   complete?" — is not available to a PreToolUse hook. Design that constraint in, or do not
   build it.
2. **Worktree guard.** `pwd` confirmed before any Edit/Write in a relay child. The report
   cites an edit landing in the main checkout instead of the worktree. Mechanical, cheap,
   no intent problem — the strongest of the report's concrete suggestions.

#### What the report cannot see, recorded so it is not mistaken for coverage

- **It does not distinguish a rule that is absent from one that is present and unfollowed.**
  Every "add this to CLAUDE.md" suggestion above is stated as if the rule were missing.
- **It reads config-imposed behaviour as agent lapse.** The language case is the clear one;
  there may be others.
- Its satisfaction figures are labelled model-estimated **by the report itself**, and the
  friction counts are not.
- It has no view of what the rules cost. "Ignored instructions 3" against a global
  `CLAUDE.md` that is now well past 500 lines is a plausible symptom of volume rather than
  of discipline, and nothing in the report can separate those.

#### Actions, ranked

1. **User decision:** reconcile the response-language setting with the recorded English-only
   preference. Nothing else about language is actionable until that is settled.
2. Build the worktree guard. Smallest, no intent problem.
3. Widen the brevity rule past Slack, since that is the one suggestion whose scope is
   genuinely narrower than the observed friction.
4. Treat the no-truncated-evidence guard as a design question first — a warn-only hook, or
   nothing.
5. Do **not** add the four adherence-gap rules again as prose. If they are worth enforcing,
   they are worth mechanizing; if they are not worth mechanizing, a sixth restatement will
   not change the outcome.

### The workstream advisory copies three helpers from its siblings, and the copies keep diverging

`workstream_page_scan` re-implements, inline, three things `_wikilinks_in`,
`wiki_index_scan` and `hygiene_scan` already do: the HTML-comment strip plus fence walk,
the wikilink-target resolution through `tracked`/`stems`, and the
`subprocess.run`-with-timeout wrapper. A reviewer counted roughly four omissions per
forty-five copied lines, and each omission was a real defect fixed after the fact —
bare-stem resolution, tracked-at-HEAD resolution, fence and comment stripping, and
unterminated-fence reporting. The fourth was found on 2026-08-05, after the first three
had already been repaired.

Rule-of-three does not apply cleanly: the copies are *deliberately* slightly different
(`tracked` is `.md`-only in one and all-files in the other; `stems` is a dict in one and
a set in the other), and those intended differences are exactly what camouflaged the
unintended ones.

Deferred from PR #52 on purpose. That branch had already been rewritten twice, and
touching two working checks to serve a third that currently matches zero pages in the
configured vault trades live correctness for latent tidiness. The extraction is three
units — `_content_lines(text) -> (lines, unterminated)`, `_resolve_target(target,
tracked, stems) -> path | None`, and a module-level `_git_out(repo, *args, timeout)` —
and wants its own branch, its own tests, and its own review pass.

### `dated_claims_context`'s severity predicate logs a fired advisory at info — the same defect bfe2209 just fixed in a sibling

`dated_claims_context`'s outcome `"due"` returns a real advisory — composed from
`lines` and handed back to the caller, shown to the user exactly like the `"expired"`
and `"malformed"` outcomes are. But the severity predicate at
`hooks/cost-discipline.py:2315` is `"warn" if outcome in ("expired", "malformed") else
"info"`, so a fired `"due"` advisory is logged at `info` — the fire log records a check
that spoke as if it had stayed silent. This is a live instance of exactly what `bfe2209`
("severity must ask whether an advisory fired, not what it is called") just repaired for
`workstream_page_context` two entries up the call chain, still present in a direct
sibling of the function that commit touched.

CONFIRMED BY EXECUTION, not by reading: with a fixture claim ten days out and `log_fire`
stubbed, `dated_claims_context` returned outcome `'due'` with an advisory present, and
the end-to-end fire-log row was `('dated_claim', 'due', 'info')`.

Deferred rather than fixed on this branch: converting that predicate to the property
form — ask whether an advisory fired, not what the outcome is named — moves *every*
`"due"` firing from `info` to `warn`. That is not the refactor `bfe2209` was; it changes
what threshold users see for an upcoming (not yet missed) expiry, which is a product
decision — does "expires in N days" deserve the same attention as "already expired"? —
and wants its own review, not a rider on this branch.

The other two siblings are the cheap half of the same job, by contrast, and do NOT carry
this defect: `wiki_index_context` returns a message on exactly one outcome (`"dangling"`,
out of `"skipped"/"clean"/"dangling"`), and `plugin_version_drift_context` returns a
message on exactly one outcome (`"drifted"`, out of the `"skipped:*"/"in_sync"/"drifted"`
set). For both, `"warn" if outcome == X else "info"` and `"fired" if advisory else
"not fired"` are the same predicate under different names — verified by reading both
functions' `return` statements — so converting those two to the property form is a
genuine no-op and is the cheap half of this job. Only `dated_claims_context` has more
than one message-bearing outcome, which is what makes its predicate a real decision
instead of a rename.

### `log_fire`'s docstring undersells its own `action` values

`log_fire`'s docstring (`hooks/cost-discipline.py:1199`) states `action` ∈
`{"warn","block"}`. Eight call sites in the same file pass `"info"` (lines 2303, 2315,
2327, 2344, 2354, 2909, 2956, 3063) — every advisory-outcome logger the pulse runs, plus
the workflow-lifecycle and frontmatter-model loggers. The docstring is stale for the
function every advisory in this file reports through, not for some peripheral caller.

Deferred: a one-line docstring fix, but this branch's own constraint is that
`hooks/cost-discipline.py` does not change while three reviewers are reading it for PR
#52 — editing it mid-review invalidates their review. Fix is
`` `action` ∈ {"warn","block","info"} `` (or a plain `str` note, if the set is expected
to keep growing) in the next PR that legitimately touches this file.

### The `log_fire` test stub is copied into at least six files, in at least four incompatible shapes, and there is no `conftest.py` to hold one

Six test files stub `log_fire` independently: `tests/test_agent_scoped_counters.py`,
`tests/test_read_block_tier.py`, `tests/test_edit_loop_tier.py`,
`tests/test_harness_hygiene.py`, `tests/test_dated_claims.py`, and
`tests/test_wiki_index_integrity.py`. The stub shape has already diverged in at least
four ways across those files: no-op, `(rule)`-only, `(rule, outcome)`, and now `(rule,
outcome, severity)` — the shape this week's `bfe2209` work needed to assert the
fired-vs-logged distinction. `find . -iname conftest.py` returns nothing repo-wide
(verified by glob), so there is no shared fixture location, and every test file that
needs to intercept `log_fire` re-derives the interception from scratch.

Deferred rather than fixed here: introducing a `conftest.py` fixture touches every one
of those six test files at minimum — converting each stub call site to use it — which
is the same shared-infrastructure-mid-review problem as the helper-extraction entry
above, just on the test side rather than the hook side. Worth flagging as a trend rather
than a steady state: the divergence widened, not narrowed, this week — the `(rule,
outcome, severity)` shape is new, not legacy. Deciding this needs its own branch: design
the fixture's call signature (able to assert on `severity`, per the newest shape,
without breaking the three older call sites that only assert on `rule` or `(rule,
outcome)`) before converting the six call sites, and land it separately from any
behavior change to `log_fire` itself.


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
