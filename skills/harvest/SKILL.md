---
name: harvest
description: Turn a repo's recent git activity and this session's context into ranked article-topic ideas, then write chosen ones as content seeds for later drafting. Trigger on phrases like "what should I write about", "content ideas from this", "turn this into an article", "harvest this session", "any blog post ideas here", "content seeds", "write this up for Dev.to", or right after finishing a notable piece of work in a repo that has a content-seeds convention. Also invoked directly via the `/harvest` command.
---

Turn recent work into candidate article topics. Follow the harvest → ideate → (human pick) → seed flow. Never fabricate facts, metrics, or dates.

## 0. Config (per-machine, not part of this repo)

Reads `~/.claude/harvest.config.json` if present:

```json
{
  "defaultCommits": 10,
  "seedsDir": "/absolute/path/to/your/content-seeds"
}
```

See `harvest.config.json.example` at this repo's root — copy it to
`~/.claude/harvest.config.json` and edit `seedsDir` on a fresh machine.
Without a config, `harvest.mjs` still works (defaults to 10 commits, skips
the "existing seeds" dedup section).

## 1. Harvest (deterministic, no LLM)

Run the script bundled with this skill — use the base directory this skill
was loaded from (shown when the skill loads) rather than a hardcoded path,
so this works the same on any machine:

```
node <this skill's base directory>/harvest.mjs $ARGUMENTS
```

Pass `--commits N` to override the default commit count, or `--since "2 weeks ago"` for a date window. The blob has recent commits, files changed, and existing content-seed slugs to avoid duplicating.

## 2. Ideate (one pass, guiding questions)

Using the harvest blob **and** your understanding of what was done in THIS session, answer these, then produce a **ranked** list of 3–8 candidate article topics:

- What was genuinely non-obvious or surprising here?
- What concrete problem did this solve, and for whom?
- What dead-ends or wrong turns happened? (the best article fuel)
- What is teachable to a working engineer, not a tutorial rehash?
- What has a clear before/after or a real, honest number?

Drop anything thin. Do not duplicate a topic already listed in the harvest. For each topic show: **title · one-line angle · audience · why it rates · suggested Dev.to tags** (≤4, lowercase alphanumeric, no hyphens — Dev.to tags are single words).

## 3. Ask which to keep

Present the ranked list and ask the user to pick. Do not write seeds until they choose.

## 4. Write the chosen topics as seeds

For each kept topic, write `<seedsDir>/<YYYY-MM-DD>-<slug>.md` (seedsDir from the config, today's date) with this frontmatter + body:

```yaml
---
slug: <kebab-title>
created: <YYYY-MM-DD>
source: harvest
from_session: <this repo/session>
relay_msg_id:
status: idea
target_angle: <one line: the story/lesson>
proposed_tags: [<≤4 lowercase alphanumeric>]
---

<raw material and notes the draft step will need — grounded in the harvest + session, no fabrication>
```

If `seedsDir` isn't configured, ask the user where to write the seed files instead of guessing.

## Fallback

If you cannot ideate (empty harvest and no session context), write the raw harvest blob as one seed so nothing is lost.
