# Claude Model Pricing (per 1M tokens)

Current as of 2026-07-26. Update when Anthropic changes pricing.

| Model | Input | Output | Cache Creation | Cache Read |
|---|---|---|---|---|
| Fable 5 (`claude-fable-5`) _(disabled 2026-06-12 — retained for historical session pricing; 06-10/06-11 sessions ran on Fable)_ | $10.00 | $50.00 | $12.50 | $1.00 |
| Opus 5 (`claude-opus-5`) | $5.00 | $25.00 | $6.25 | $0.50 |
| Opus 4.8 (`claude-opus-4-8`) | $5.00 | $25.00 | $6.25 | $0.50 |
| Sonnet 5 (`claude-sonnet-5`) — turns dated **≤ 2026-08-31** (intro rate) | $2.00 | $10.00 | $2.50 | $0.20 |
| Sonnet 5 (`claude-sonnet-5`) — turns dated **≥ 2026-09-01** (list rate) | $3.00 | $15.00 | $3.75 | $0.30 |
| Sonnet 4.6 (`claude-sonnet-4-6`) | $3.00 | $15.00 | $3.75 | $0.30 |
| Haiku 4.5 (`claude-haiku-4-5-20251001`) | $1.00 | $5.00 | $1.25 | $0.10 |
| Opus 4.7 / 4.6 / 4.5 (legacy, still appear in transcripts) | $5.00 | $25.00 | $6.25 | $0.50 |
| Sonnet 4.5 / 4 (legacy) | $3.00 | $15.00 | $3.75 | $0.30 |

Cache creation = 1.25× input; cache read = 0.10× input.

**Sonnet 5 is the only dated-rate row.** Price each turn by *its own* timestamp, not by when
the audit runs: Anthropic's introductory rate ($2/$10) covers turns through 2026-08-31, the
list rate ($3/$15) applies from 2026-09-01. A single long-running session can straddle the
boundary — split it rather than picking one rate for the whole session.

**Normalise the model ID before lookup.** Transcripts carry three different spellings of the
same model: the bare alias (`claude-sonnet-5`), a provider-prefixed and date-suffixed variant
(`anthropic/claude-sonnet-5-20260630`), and a bare family name (`opus` / `sonnet` / `haiku`,
written by some sub-agent dispatches). Strip any `<provider>/` prefix and any `-YYYYMMDD`
suffix, then match on the alias; resolve a bare family name to that tier's current default
and mark the row `inferred`. All three forms occur in live transcripts — spot-checked
2026-07-26 — so a table keyed only on bare aliases misses `anthropic/claude-sonnet-5-20260630`
entirely, which is the same silent-$0 class as the incident below.

That spot-check establishes only that the forms **exist**, not how many turns they cover: it
sampled a subset of recent session files and counted field occurrences, which is not the same
as counting turns (one record can carry the field more than once). If you need the volume,
count matching records across every session file rather than reusing a figure from here.

**Unknown model = LOUD ERROR, never silent $0.** If a transcript contains a model ID not in
this table, the audit worker MUST emit an `UNPRICED: <model-id>, N turns` row instead of
pricing it at zero. (2026-06-10 incident: `claude-opus-4-7` missing from this table priced a
$230.79 session as $0.00 — 15% of the audit total, invisible. Same failure class as the
fable-classifier gap in cost-discipline.py fixed the same day. `<synthetic>` model entries
are the one exception — they are non-billable placeholders, $0 is correct.)

## Notes

- Reasoning tokens (Opus `xhigh` effort) are billed as output tokens and are **never cached** — count at full output rate every turn.
- **Fast mode is a different rate for the same model ID.** Available on Opus 5 and Opus 4.8 (Claude API only); `usage.speed == "fast"` marks those turns, so the model ID alone will not tell you. **Opus 5 fast: $10.00 / $50.00 per MTok** — 2× standard, so pricing a fast turn at the standard rate under-counts it by half. **Opus 4.8 fast is premium too, but its exact rate is unconfirmed here — emit those turns as `UNPRICED` rather than pricing them at the standard rate**, per the doctrine above. Guessing "probably also 2×" is the same silent-mispricing move, just with a plausible number attached.
- **The tokenizer split is now by generation, not by tier.** The newer tokenizer (~30% more tokens for the same text) covers Fable 5, Opus 5, Opus 4.8 / 4.7 **and Sonnet 5**; only Sonnet 4.6 and Haiku 4.5 still use the older one. Consequence: **Opus 5 vs Sonnet 5 per-character ≈ per-token** (~2.5× at Sonnet's intro rate, ~1.7× after) because both sides inflate equally. The old "Opus 4.8 vs Sonnet ~2.2× per character" figure assumed a cheap-tokenizer Sonnet and does not transfer to Sonnet 5. Cross-generation comparisons still need a correction, but it is a **range, not a constant**: the newer tokenizer runs ~1×–1.35× the older one's count depending on content and workload shape, so Opus 5 vs Haiku 4.5 lands somewhere between 5× and ~6.75× per character. Re-baseline with `count_tokens` on representative text instead of applying one multiplier — the flat ~4.5× and ~2.2× point figures this note used to carry were that same overclaim, and quoting a single number here invites the reader to skip the measurement.
- **Fable 5 adaptive thinking is always-on** — no `/effort` tiers; reasoning cost is included and uncacheable every turn. **Opus 5 also thinks by default** (unlike Opus 4.8, where omitting `thinking` meant no thinking), so an Opus 5 turn carries reasoning tokens even with no explicit effort setting.
- **Opus 5's minimum cacheable prefix is 512 tokens**, down from 1024 on Opus 4.8 — short prompts that previously billed at the full input rate now appear as cache reads. Expect the cached share of input to rise on Opus 5 sessions without any change on our side.
- Sub-agent output tokens count at that sub-agent's model rate, not the main agent's.
- **Output dominates cost in long sessions — and inverts completely for short sub-agent dispatches.** Long main sessions run ~140:1 output-to-true-input after caching. But a fan-out of 15 short sub-agents measured on 2026-07-27 (Opus 5, 4–6 tool calls each) cost $9.31 of which **output was $0.37 — 4.0%**; cache *creation* was $6.80 (73%) and cache reads $2.13 (23%). Each dispatch pays to build its own prefix and then reads it back every turn, and there is almost no generation to amortise that against. Consequence when budgeting: an output-token cap is the wrong instrument for fan-out work — it can read as 5% consumed while the real spend is already double digits. Budget fan-out on agent count × measured cost-per-agent, and take the per-agent figure from transcripts rather than from `budget.spent()`, which disagreed with them by a factor of 30 in the same run.
