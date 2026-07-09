# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, use GitHub's private vulnerability reporting:

1. Go to the [**Security** tab](../../security) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in as much detail as you can — steps to reproduce, affected version,
   and potential impact.

This opens a private advisory visible only to you and the maintainer, so the
issue can be discussed and fixed before public disclosure.

If GitHub's private reporting is unavailable for any reason, you may instead
contact the maintainer directly through the contact information on their
GitHub profile.

## Scope

This project is a **local, filesystem-based** Claude Code methodology bundle
— skills, a hook, and an installer script. It has no server component and
does not transmit data over the network by design. Relevant security-
sensitive areas include:

- The `cost-discipline.py` hook, registered either via a native Claude Code
  plugin (`.claude-plugin/`) or the legacy hand-merge path, which runs on
  every tool call and reads/writes small marker files under
  `${CLAUDE_PLUGIN_ROOT}` / `~/.claude/`.
- `install.sh` / `bootstrap.sh`, which symlink skills into `~/.claude/skills/`
  and (legacy path only) merge hook entries into `~/.claude/settings.json`.
- `lib/settings_merge.py` (legacy path) and `lib/migrate_to_plugin.py`, which
  read and rewrite `~/.claude/settings.json` — both back up the file before
  writing and abort on malformed JSON rather than guessing.
- Any code path that shells out or reads/writes outside the user's own
  `~/.claude/` directory or this repo's checkout.

## Supported Versions

Security fixes are made against the latest released version (tagged
`vX.Y.Z`, see [Releases](../../releases)). There is no long-term-support
branch at this stage of the project.

## Response

This is a solo-maintained open-source project. There is no guaranteed
response SLA, but reports are triaged as soon as possible after they're
received.
