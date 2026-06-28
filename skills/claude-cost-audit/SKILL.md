---
name: claude-cost-audit
description: Audit recent Claude Code session costs and identify waste patterns. Use when the user asks "audit my claude cost", "how much am I spending", "check session costs", "analyze last week's claude usage". Parses local session JSONL files; produces a markdown report with top cost drivers and waste-pattern detection.
scope: core
---

# Claude Cost Audit

## When to use

Trigger words: "audit my claude cost", "how much am I spending", "check session costs", "analyze session usage", "cost audit", "claude spend".

## Workflow

1. **Identify target sessions.** Unless the user gives a date range, default to "today + last full working day". Session JSONL files live in `~/.claude/projects/<project-slug>/*.jsonl`. Sort by mtime; pick all files modified within the range.

2. **Dispatch a Haiku worker agent.** The main agent does NOT read JSONL files (they can be 10MB+). Dispatch a Haiku sub-agent with:
   - Explicit list of file paths
   - Instructions to stream line-by-line (Python `json` module)
   - Pricing table from `references/pricing.md`
   - Waste patterns from `references/waste-patterns.md`
   - Required report format (see below)
   - "Report in under 400 words"

3. **Present the report.** Show the Haiku agent's output to the user. Offer to save to `docs/brain/claude-cost-audit-<YYYY-MM-DD>.md` if the audit contains new learnings.

## Report format

```
## Token Usage & Cost Analysis: N Sessions (<date range>)

TOTAL COST: $X.XX

### Totals Table
(tokens by category, cache ratio, duration, top tools)

### Per-Session Breakdown
(table: session | date | cost | duration | tokens | cache hit | notes)

### Top 3 Cost Drivers
(per session: user task, cost, pattern, root cause in one sentence)

### Waste Patterns Identified
(table of patterns from references/waste-patterns.md — YES/NO each + evidence)

### Recommendations
(≤3 specific actions matched to the waste patterns found)
```

## Files

Pricing and waste patterns live in `references/`. Update those when Anthropic prices change or new waste patterns are identified.

## Related

- [[brain/claude-cost-patterns]] — canonical cost-discipline rules
- [[brain/claude-code-patterns]] — conceptual Claude Code patterns
- `llm-cost-optimizer` skill — generic LLM cost engineering (model routing, prompt caching, output controls). This skill focuses on **Claude Code session waste**; llm-cost-optimizer focuses on **LLM API spend in your own applications**. Use both: audit finds the waste, optimizer pattern-matches it to fixes.
- [[brain/cost-discipline-hook-architecture]] — the hook layer that catches waste in real-time
- [[brain/cost-discipline-thread-primer-2026-05-18]] — observation window + audit baseline ($4.74/week mechanical-Bash-on-Opus)
