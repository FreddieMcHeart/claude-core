# R3 — CI Portability Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A GitHub Actions workflow in claude-core that proves a fresh machine stands up the harness from scratch (install → doctor → smoke) across ubuntu+macOS × py3.11/3.13, using a secret-free local `file://` wiki.

**Architecture:** One matrix workflow (`.github/workflows/portability.yml`) runs the existing `tests/smoke_install.sh` + pytest + ruff. Two edits harden `smoke_install.sh` for CI (runner-agnostic portability assertion; fail-loud WIKI_URL). A minimal `ruff.toml` makes lint deterministic.

**Tech Stack:** GitHub Actions, Bash, Python 3.11+ (stdlib), pytest, ruff, git submodules (file:// in CI).

## Global Constraints

- claude-core is going PUBLIC open-source: no personal names, SSH URLs, org identifiers, or secrets in any committed file.
- Additive + no runtime behavior change: install.sh / doctor.sh / cost-discipline behavior unchanged; only the test harness + new CI/lint config change.
- CI uses NO secrets: the required wiki submodule is exercised with a throwaway `file://` git repo created in the runner; `WIKI_URL` is always set by CI.
- Matrix: `os = [ubuntu-latest, macos-latest] × python = [3.11, 3.13]` (4 jobs). Triggers: push + pull_request on `main`, plus `workflow_dispatch`.
- The portability assertion must be runner-agnostic: it must hold on a macOS runner whose isolated `$HOME` is itself under `/Users/…`. (The old `grep -q "/Users/"` check would false-fail there.)
- Python: stdlib only in test assertions. ruff target py3.11.

---

### Task 1: Harden `tests/smoke_install.sh` for CI (runner-agnostic assertion + fail-loud WIKI_URL)

**Files:**
- Modify: `~/dev/claude-core/tests/smoke_install.sh:10` (WIKI_URL fallback) and `:31` (portability assertion)

**Interfaces:**
- Consumes: nothing new. Produces: a smoke test that (a) requires `WIKI_URL` to be set (fails loud otherwise) and (b) asserts every cost-discipline hook command path in the isolated `settings.json` begins with the isolated `$CLAUDE_DIR` — true on any OS/runner.

- [ ] **Step 1: Establish the local baseline (create a file:// wiki and run the current smoke)**

```bash
cd ~/dev/claude-core
rm -rf /tmp/ci-wiki && git init -q /tmp/ci-wiki && \
  ( cd /tmp/ci-wiki && git -c user.email=ci@example.com -c user.name=ci commit -q --allow-empty -m init && \
    echo "# wiki" > README.md && git add -A && git -c user.email=ci@example.com -c user.name=ci commit -q -m readme )
WIKI_URL="file:///tmp/ci-wiki" bash tests/smoke_install.sh; echo "exit=$?"
```
Expected: `SMOKE: all pass`, `exit=0`. This confirms the file:// wiki approach works BEFORE editing. (If the current `no host /Users path` line passes here, note it — on this dev machine the isolated HOME is under `/var/folders` or `/tmp`, so it may pass by luck; the edit makes it correct on macOS runners too.)

- [ ] **Step 2: Replace the WIKI_URL fallback (line 10) with a fail-loud requirement**

Current line 10:
```bash
WIKI_URL="${WIKI_URL:-git@github.com:FreddieMcHeart/claude-core-wiki.git}"
```
Replace with:
```bash
# CI always sets WIKI_URL (a file:// throwaway). A bare local run must set it explicitly —
# no personal URL is baked in (this repo is public).
WIKI_URL="${WIKI_URL:?set WIKI_URL to a wiki repo URL (CI uses a file:// throwaway; see docs/superpowers/specs)}"
```

- [ ] **Step 3: Replace the portability assertion (line 31) with a runner-agnostic check**

Current line 31:
```bash
ck "no host /Users path in settings" '! grep -q "/Users/" "$CLAUDE_DIR/settings.json"'
```
Replace with:
```bash
ck "hook paths rooted at CLAUDE_DIR" 'CLAUDE_DIR="$CLAUDE_DIR" python3 -c "
import json, os, sys
cd = os.environ[\"CLAUDE_DIR\"]
d = json.load(open(os.path.join(cd, \"settings.json\")))
cmds = [h.get(\"command\",\"\") for grp in d.get(\"hooks\",{}).values() for g in grp for h in g.get(\"hooks\",[])]
cd_cmds = [c for c in cmds if \"cost-discipline\" in c]
sys.exit(0 if cd_cmds and all(c.split()[0].startswith(cd) for c in cd_cmds) else 1)
"'
```
This extracts every hook command, keeps the cost-discipline ones, and asserts each command's first token (the script path) begins with the isolated `$CLAUDE_DIR`. It requires at least one cost-discipline command to exist (so an empty settings can't pass vacuously), and holds regardless of where the runner puts `$HOME`.

- [ ] **Step 4: Re-run the smoke to verify both edits**

```bash
cd ~/dev/claude-core
WIKI_URL="file:///tmp/ci-wiki" bash tests/smoke_install.sh; echo "exit=$?"
```
Expected: `SMOKE: all pass`, `exit=0`, and the new line prints `PASS  hook paths rooted at CLAUDE_DIR`.

- [ ] **Step 5: Verify the fail-loud path**

```bash
cd ~/dev/claude-core
( unset WIKI_URL; bash tests/smoke_install.sh ); echo "exit=$?"
```
Expected: non-zero exit with the bash `:?` message naming `WIKI_URL` (proves no personal URL is silently used).

- [ ] **Step 6: Confirm no personal reference remains**

```bash
grep -nE 'FreddieMcHeart|git@github.com|/Users/[a-z]' ~/dev/claude-core/tests/smoke_install.sh || echo "clean — no personal refs"
```
Expected: `clean — no personal refs`.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/claude-core
git add tests/smoke_install.sh
git commit -m "[R3] smoke: runner-agnostic hook-path assertion + fail-loud WIKI_URL (public-safe)"
```

---

### Task 2: Add a minimal `ruff.toml`

**Files:**
- Create: `~/dev/claude-core/ruff.toml`

**Interfaces:**
- Produces: a deterministic `ruff check lib tests` target. Consumes nothing.

- [ ] **Step 1: Create `ruff.toml`**

```toml
# Lint config for the CI portability gate. Minimal + stable.
target-version = "py311"
line-length = 100

[lint]
# Default rule set (E, F) plus import sorting and pyupgrade — conservative, no surprises.
select = ["E", "F", "I", "UP"]

[lint.per-file-ignores]
# Test files may keep intentionally-unused imports / long lines in fixtures.
"tests/*" = ["E501"]
```

- [ ] **Step 2: Run ruff locally if available; otherwise via uvx/pipx**

```bash
cd ~/dev/claude-core
if command -v ruff >/dev/null 2>&1; then ruff check lib tests
elif command -v uvx  >/dev/null 2>&1; then uvx ruff check lib tests
else python3 -m pip install --quiet --user ruff && python3 -m ruff check lib tests; fi
echo "exit=$?"
```
Expected: exit 0. If ruff flags real issues in `lib/` (e.g. unused imports in `settings_merge.py` or `config_loader.py`), FIX them in this task (they are trivial and in-scope — the lint gate must be green). If it flags the known unused `tempfile`/`textwrap` in `tests/test_config_loader_wiki_url.py`, remove those two imports.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-core
git add ruff.toml lib tests
git commit -m "[R3] add ruff.toml (py311) + clear any lint findings"
```

---

### Task 3: Add the CI portability workflow

**Files:**
- Create: `~/dev/claude-core/.github/workflows/portability.yml`

**Interfaces:**
- Consumes: `tests/smoke_install.sh` (Task 1), `ruff.toml` (Task 2), `tests/` pytest files. Produces: the CI gate.

- [ ] **Step 1: Create the workflow**

```yaml
name: portability

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  fresh-install:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.11", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - name: Checkout (no submodules — install.sh adds the wiki at runtime)
        uses: actions/checkout@v4
        with:
          submodules: false

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Create throwaway file:// wiki (no secrets)
        run: |
          set -euo pipefail
          WIKI_DIR="${RUNNER_TEMP}/ci-wiki"
          git init -q "$WIKI_DIR"
          git -C "$WIKI_DIR" -c user.email=ci@example.com -c user.name=ci commit -q --allow-empty -m init
          echo "# ci wiki" > "$WIKI_DIR/README.md"
          git -C "$WIKI_DIR" add -A
          git -C "$WIKI_DIR" -c user.email=ci@example.com -c user.name=ci commit -q -m readme
          echo "WIKI_URL=file://$WIKI_DIR" >> "$GITHUB_ENV"

      - name: Fresh-machine smoke (install + doctor + idempotency)
        run: bash tests/smoke_install.sh

      - name: Unit tests
        run: |
          python3 -m pip install --quiet --upgrade pip pytest
          python3 -m pytest tests/ -v

      - name: Lint
        run: |
          python3 -m pip install --quiet ruff
          python3 -m ruff check lib tests
```

- [ ] **Step 2: Validate the YAML**

```bash
cd ~/dev/claude-core
python3 -c "import sys,yaml" 2>/dev/null && python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/portability.yml')); print('yaml ok')" \
  || python3 -c "print('pyyaml absent — skip; GitHub will validate on push')"
```
Expected: `yaml ok` (or the skip notice if pyyaml isn't installed). If `actionlint` is available, also run `actionlint .github/workflows/portability.yml`.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-core
git add .github/workflows/portability.yml
git commit -m "[R3] CI portability gate: fresh-install matrix (ubuntu+macos × py3.11/3.13), file:// wiki"
```

---

## Post-plan notes

- The real confirmation is the first CI run after the parent pushes claude-core. A local pass of Task 1 Step 4 + Task 2 Step 2 is the pre-push gate.
- Parent (this session) pushes claude-core after the final review; subagents commit locally only.
- The spec file `docs/superpowers/specs/2026-07-01-r3-ci-portability-gate-design.md` is currently untracked — fold it into the first commit or commit it alongside (it documents this work).
- Out of scope (relay/rlm round-trips, broader public-release scrub) per the spec.
