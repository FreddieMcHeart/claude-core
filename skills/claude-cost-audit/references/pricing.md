# Claude Model Pricing (per 1M tokens)

Current as of 2026-06-10. Update when Anthropic changes pricing.

| Model | Input | Output | Cache Creation | Cache Read |
|---|---|---|---|---|
| Fable 5 (`claude-fable-5`) _(disabled 2026-06-12 — retained for historical session pricing; 06-10/06-11 sessions ran on Fable)_ | $10.00 | $50.00 | $12.50 | $1.00 |
| Opus 4.8 (`claude-opus-4-8`) | $5.00 | $25.00 | $6.25 | $0.50 |
| Sonnet 4.6 (`claude-sonnet-4-6`) | $3.00 | $15.00 | $3.75 | $0.30 |
| Haiku 4.5 (`claude-haiku-4-5-20251001`) | $1.00 | $5.00 | $1.25 | $0.10 |
| Opus 4.7 / 4.6 / 4.5 (legacy, still appear in transcripts) | $5.00 | $25.00 | $6.25 | $0.50 |
| Sonnet 4.5 / 4 (legacy) | $3.00 | $15.00 | $3.75 | $0.30 |

Cache creation = 1.25× input; cache read = 0.10× input.

**Unknown model = LOUD ERROR, never silent $0.** If a transcript contains a model ID not in
this table, the audit worker MUST emit an `UNPRICED: <model-id>, N turns` row instead of
pricing it at zero. (2026-06-10 incident: `claude-opus-4-7` missing from this table priced a
$230.79 session as $0.00 — 15% of the audit total, invisible. Same failure class as the
fable-classifier gap in cost-discipline.py fixed the same day. `<synthetic>` model entries
are the one exception — they are non-billable placeholders, $0 is correct.)

## Notes

- Reasoning tokens (Opus `xhigh` effort) are billed as output tokens and are **never cached** — count at full output rate every turn.
- **Fable 5 + Opus 4.8 use a newer tokenizer** (~30% more tokens per same text vs Sonnet 4.6 / Haiku 4.5). Net cost per character on Fable vs Sonnet: ~4.5×. On Opus 4.8 vs Sonnet: ~2.2×.
- **Fable 5 adaptive thinking is always-on** — no `/effort` tiers; reasoning cost is included and uncacheable every turn.
- Sub-agent output tokens count at that sub-agent's model rate, not the main agent's.
- Output dominates cost in long sessions (typical ratio: ~140:1 output-to-true-input after caching).
