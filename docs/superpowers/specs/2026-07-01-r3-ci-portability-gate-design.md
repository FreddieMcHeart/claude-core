---
type: spec
created: 2026-07-01
tags: [portability, ci, github-actions, claude-core, R3, open-source-prep]
status: approved
---

# R3 — CI Portability Gate (design)

**Goal:** A GitHub Actions workflow in claude-core that proves a fresh machine stands up the
harness from scratch and it works — the automated backstop so R2's installer doesn't silently rot.

**Architecture:** One matrix workflow (`.github/workflows/portability.yml`) that runs the existing
`tests/smoke_install.sh` (fresh isolated `$HOME` install + assertions + idempotency) plus pytest
and ruff, across ubuntu + macOS × two Python versions. No secrets: the wiki submodule is exercised
with a local throwaway `file://` git repo, not the private claude-core-wiki.

**Context:** claude-core is being prepared for a PUBLIC open-source release. All choices below keep
CI self-contained and free of personal/private references.

## Decisions (locked)
- **Host:** claude-core (installer + smoke + python live here).
- **Wiki in CI:** a throwaway `git init` repo in the runner; `WIKI_URL=file:///…` overrides smoke's
  default. Exercises the REQUIRED submodule path with zero secrets.
- **Matrix:** `os = [ubuntu-latest, macos-latest] × python = [3.11, 3.13]` (4 jobs). Free on a public
  repo; covers the portability claim on both OSes and the supported Python range endpoints.
- **Scope:** install → smoke (assertions + idempotency) → pytest → ruff. Relay/rlm round-trips are
  OUT (covered separately in the relay child session).

## Components
1. **`.github/workflows/portability.yml`** (NEW) — the matrix gate. Triggers: push + pull_request on
   `main`, plus `workflow_dispatch`. Per job: checkout (no submodules) → setup-python → make the
   throwaway file:// wiki → `WIKI_URL=file://… bash tests/smoke_install.sh` → `pip install pytest &&
   python3 -m pytest tests/ -v` → `ruff check lib tests`.
2. **`tests/smoke_install.sh`** (MODIFY) — replace the host-specific portability assertion.
3. **`tests/smoke_install.sh`** (MODIFY) — genericize the default `WIKI_URL` fallback.
4. **`ruff.toml`** (NEW, minimal) — so `ruff check` has a defined config (target py3.11, sensible
   default rule set); keeps the lint step deterministic.

## The runner-agnostic portability assertion (the crux)
The current smoke assertion `grep -q "/Users/" settings.json` is a macOS-specific proxy for "no build
machine path leaked". On a macOS runner the isolated `$HOME` is itself under `/Users/runner/…`, so the
literal grep would false-fail. Replace it with the REAL property:

> Every cost-discipline command path recorded in the isolated `settings.json` must start with the
> isolated `$CLAUDE_DIR`.

Concretely: extract each hook `command` from `settings.json` (python + json), assert each one's path
component is under `$CLAUDE_DIR`. This holds on any runner/OS and actually tests "the installer wired
paths relative to the target dir, not to some absolute build path".

## WIKI_URL genericization
`tests/smoke_install.sh` currently falls back to a personal SSH URL
(`git@github.com:FreddieMcHeart/claude-core-wiki.git`) when `WIKI_URL` is unset. For a public repo,
change the fallback to a placeholder that fails loudly with a clear message if neither `WIKI_URL` nor
a real URL is provided (e.g. `WIKI_URL="${WIKI_URL:?set WIKI_URL to a wiki repo (CI uses a file:// throwaway)}"`).
CI always sets `WIKI_URL`, so this only affects a bare local run — which should be explicit anyway.

## Owner / public non-break
- Adding a workflow file and a ruff.toml is purely additive; no runtime behavior changes.
- The smoke assertion change is behavior-preserving on the owner machine (paths there are under
  `$CLAUDE_DIR` too) and merely portable across runners.

## Testing R3 itself
- Locally: run `WIKI_URL=file:///tmp/ci-wiki bash tests/smoke_install.sh` after creating the throwaway
  repo — expect all-pass, confirming the new assertion works with a file:// wiki.
- `ruff check lib tests` and `python3 -m pytest tests/` pass locally.
- The workflow YAML validates (actionlint if available, else a careful read); real confirmation is the
  first CI run after push.

## Out of scope
- Relay register/send/reply round-trip + rlm-fanout scopes — relay child session.
- Broader public-release prep (LICENSE, README polish, full personal-reference scrub) — separate task.
