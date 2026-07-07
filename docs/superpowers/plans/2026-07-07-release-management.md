# Release Management + README Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire automated, conventional-commit-driven release management (GitHub Releases + CHANGELOG, no PyPI artifact) into `claude-core`, and rewrite `README.md` to the polish level shipped for `downbeat`.

**Architecture:** `python-semantic-release` reads a new minimal `pyproject.toml` (identity-only `[project]` table, no build-system — nothing here is an installable package), computes the next version from conventional commits, patches `.claude-plugin/plugin.json`'s version via a `build_command` hook (`lib/bump_plugin_version.py`), generates `CHANGELOG.md`, and creates a GitHub Release. A new `.github/workflows/release.yml` runs this after `portability.yml` goes green on `main`, using the default `GITHUB_TOKEN` (verified live: branch rulesets are unavailable on this private free-tier repo, so the `github-actions[bot]`-vs-ruleset problem hit on `downbeat` cannot occur here yet).

**Tech Stack:** `python-semantic-release` (pip-installed in CI, not uv — this repo has no uv/pyproject-based dependency management beyond the new minimal file this plan adds), Python 3.11+ standard library (`json`, `pathlib`) for the version-bump script, `pytest` for tests, VHS for the README terminal demo (confirmed installed at `/opt/homebrew/bin/vhs` on this machine).

## Global Constraints

- No PyPI/npm publish step anywhere — `python-semantic-release publish` only uploads to the GitHub Release (VCS release), never a package index. Do not add PyPI credentials or a `twine`/`build` step.
- The new `pyproject.toml` contains **only** `[project]` (name + version, nothing else — no dependencies, no build-system table, no `[tool.poetry]` or similar) and `[tool.semantic_release]` sections. It must not make this repo pip-installable.
- Anchor tag `v0.1.0` must be created on the current `main` HEAD (commit `e2c2920`) **before** any semantic-release config is added, so old non-conventional `[R2]`/`[R3]` commits are never considered by version calculation.
- `release.yml` uses the default `GITHUB_TOKEN`, not a PAT. Add an inline comment: `# Uses default GITHUB_TOKEN — branch rulesets are unavailable on this private free-tier repo (verified via gh api, both return 403). Revisit with a PAT (RELEASE_TOKEN) if/when this repo goes public and a ruleset with required status checks is added on main.`
- CI commands in any new workflow must use plain `pip`/`python3 -m pip`, never `uv` — this repo has no uv-managed dependency file (confirmed by inspecting the existing `portability.yml`, which uses `python3 -m pip install --quiet --upgrade pip pytest` / `python3 -m ruff check lib tests`).
- New Python modules live in `lib/`, matching the existing convention (`lib/migrate_to_plugin.py`, `lib/config_loader.py`) — not a new `scripts/` directory (a deviation from the design doc's file path, made here for consistency with the established codebase pattern; `lib/` is already covered by CI's `ruff check lib tests` and pytest discovery).
- Out of scope, do not touch: making the repo public, LICENSE/CONTRIBUTING.md/CODE_OF_CONDUCT.md/SECURITY.md, name-squatting checks, `RELEASE_TOKEN` PAT wiring.
- Test import pattern for new `lib/` modules: dynamic loading via `importlib.util.spec_from_file_location`, exactly as `tests/test_migrate_to_plugin.py` already does — do not add `lib/__init__.py` or change how modules are imported project-wide.

---

### Task 1: Anchor version history + minimal `pyproject.toml` + semantic-release config

**Files:**
- Create: `pyproject.toml` (repo root — does not exist today)
- Test: manual verification via `semantic-release version --print` (no automated test — this task has no code logic to unit-test, only configuration; the CLI dry-run output is the verification)

**Interfaces:**
- Consumes: nothing from prior tasks (first task).
- Produces: the `[tool.semantic_release]` config block that Task 2's `build_command` value plugs into; the `version_toml` target (`pyproject.toml:project.version`) that becomes the single version source of truth downstream tasks assume exists.

- [ ] **Step 1: Create the anchor tag on current `main` HEAD**

```bash
cd ~/dev/claude-core
git fetch origin main
git rev-parse origin/main   # confirm this prints e2c2920... (the Phase-2 merge commit)
git tag v0.1.0 e2c2920
git push origin v0.1.0
```

Expected: `git rev-parse origin/main` prints a hash starting `e2c2920`. If it does not (main has moved since this plan was written), tag whatever `origin/main`'s current HEAD is instead — the anchor point is "current HEAD at the time this task runs," not literally the string `e2c2920`.

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "claude-core"
version = "0.1.0"

[tool.semantic_release]
version_toml = ["pyproject.toml:project.version"]
build_command = "python3 lib/bump_plugin_version.py"
major_on_zero = false
tag_format = "v{version}"
commit_parser = "angular"

[tool.semantic_release.changelog]
changelog_file = "CHANGELOG.md"

[tool.semantic_release.branches.main]
match = "main"

[tool.semantic_release.remote]
type = "github"

[tool.semantic_release.publish]
upload_to_vcs_release = true
```

- [ ] **Step 3: Install python-semantic-release locally and verify version calculation**

```bash
pip install --quiet python-semantic-release
git log v0.1.0..HEAD --oneline   # should show only the feat:/fix:/docs:/ci: commits from Phase 2
semantic-release version --print
```

Expected: `semantic-release version --print` prints a version greater than `0.1.0` (e.g. `0.2.0`, since the commits since the `v0.1.0` tag include `feat:` commits from the Phase-2 plugin migration), NOT an error, and NOT a version influenced by the old `[R2]`/`[R3]` history (verify by confirming the printed version is not something absurd like `1.0.0` or `2.0.0` from a misdetected breaking change in old history).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "ci: add python-semantic-release config (GitHub Releases only, no PyPI)"
```

---

### Task 2: `lib/bump_plugin_version.py` — sync `.claude-plugin/plugin.json`'s version

**Files:**
- Create: `lib/bump_plugin_version.py`
- Test: `tests/test_bump_plugin_version.py`

**Interfaces:**
- Consumes: the `NEW_VERSION` environment variable, set by `python-semantic-release`'s `build_command` invocation (confirmed via python-semantic-release docs: `build_command` receives `NEW_VERSION`, `PACKAGE_NAME`, and CI-specific env vars).
- Produces: `bump_version(manifest_path: Path, new_version: str) -> None` — the function Task 1's `build_command` config (`python3 lib/bump_plugin_version.py`) invokes indirectly via this script's `main()`. Later tasks do not depend on this function directly, but the CLI contract (`NEW_VERSION` env var in, `.claude-plugin/plugin.json`'s `version` key patched in place, all other keys and key order preserved) is what Task 3's `release.yml` relies on running correctly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bump_plugin_version.py`:

```python
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "bump_plugin_version.py"
spec = importlib.util.spec_from_file_location("bump_plugin_version", MOD)
bpv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bpv)


def _write_manifest(tmp_path, data):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


def test_bumps_only_version_field(tmp_path):
    original = {
        "name": "claude-core-hooks",
        "version": "0.1.0",
        "description": "Cost-discipline hook for Claude Code sessions.",
    }
    manifest_path = _write_manifest(tmp_path, original)

    bpv.bump_version(manifest_path, "0.2.0")

    result = json.loads(manifest_path.read_text())
    assert result["version"] == "0.2.0"
    assert result["name"] == "claude-core-hooks"
    assert result["description"] == "Cost-discipline hook for Claude Code sessions."
    assert list(result.keys()) == list(original.keys())


def test_preserves_rest_of_file_byte_for_byte_except_version(tmp_path):
    original = {
        "name": "claude-core-hooks",
        "version": "0.1.0",
        "description": "Cost-discipline hook for Claude Code sessions.",
    }
    manifest_path = _write_manifest(tmp_path, original)
    before_text = manifest_path.read_text()

    bpv.bump_version(manifest_path, "1.2.3")

    after_text = manifest_path.read_text()
    before_no_version_line = "\n".join(
        line for line in before_text.splitlines() if '"version"' not in line
    )
    after_no_version_line = "\n".join(
        line for line in after_text.splitlines() if '"version"' not in line
    )
    assert before_no_version_line == after_no_version_line


def test_main_reads_new_version_env_var(tmp_path, monkeypatch):
    original = {"name": "claude-core-hooks", "version": "0.1.0", "description": "x"}
    manifest_path = _write_manifest(tmp_path, original)
    monkeypatch.setenv("NEW_VERSION", "9.9.9")
    monkeypatch.setattr(bpv, "PLUGIN_MANIFEST", manifest_path)

    exit_code = bpv.main()

    assert exit_code == 0
    result = json.loads(manifest_path.read_text())
    assert result["version"] == "9.9.9"


def test_main_fails_without_new_version_env_var(tmp_path, monkeypatch):
    original = {"name": "claude-core-hooks", "version": "0.1.0", "description": "x"}
    manifest_path = _write_manifest(tmp_path, original)
    monkeypatch.delenv("NEW_VERSION", raising=False)
    monkeypatch.setattr(bpv, "PLUGIN_MANIFEST", manifest_path)

    exit_code = bpv.main()

    assert exit_code == 1
    result = json.loads(manifest_path.read_text())
    assert result["version"] == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_bump_plugin_version.py -v`
Expected: FAIL — `lib/bump_plugin_version.py` does not exist yet (collection error / `FileNotFoundError` from `spec_from_file_location`).

- [ ] **Step 3: Write the implementation**

Create `lib/bump_plugin_version.py`:

```python
#!/usr/bin/env python3
"""Patch .claude-plugin/plugin.json's version field from semantic-release's NEW_VERSION."""
import json
import os
import sys
from pathlib import Path

PLUGIN_MANIFEST = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"


def bump_version(manifest_path: Path, new_version: str) -> None:
    data = json.loads(manifest_path.read_text())
    data["version"] = new_version
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")


def main() -> int:
    new_version = os.environ.get("NEW_VERSION")
    if not new_version:
        print("bump_plugin_version.py: NEW_VERSION env var not set", file=sys.stderr)
        return 1
    bump_version(PLUGIN_MANIFEST, new_version)
    print(f"bump_plugin_version.py: set version to {new_version} in {PLUGIN_MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_bump_plugin_version.py -v`
Expected: `5 passed`

- [ ] **Step 5: Lint**

Run: `python3 -m pip install --quiet ruff && python3 -m ruff check lib tests`
Expected: no errors (the new file uses only stdlib `json`/`os`/`sys`/`pathlib`, consistent with `ruff.toml`'s `select = ["E", "F", "I", "UP"]`).

- [ ] **Step 6: Commit**

```bash
git add lib/bump_plugin_version.py tests/test_bump_plugin_version.py
git commit -m "feat: sync .claude-plugin/plugin.json version via semantic-release build_command"
```

---

### Task 3: `.github/workflows/release.yml`

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: Task 1's `pyproject.toml` config (specifically `build_command = "python3 lib/bump_plugin_version.py"`, which this workflow triggers indirectly by running `semantic-release version`), Task 2's `lib/bump_plugin_version.py` (executed by semantic-release, not called directly by this workflow).
- Produces: nothing further tasks depend on programmatically — this is the terminal automation piece. Task 4 (README) references this workflow's badge URL.

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/release.yml`:

```yaml
name: release

on:
  workflow_run:
    workflows: ["portability"]
    types: [completed]

# Uses default GITHUB_TOKEN — branch rulesets are unavailable on this private
# free-tier repo (verified via gh api: both /rulesets and /branches/main/protection
# return 403 "Upgrade to GitHub Pro or make this repository public"). Revisit with
# a PAT (RELEASE_TOKEN secret) if/when this repo goes public and a ruleset with
# required status checks is added on main — see downbeat's release.yml for the
# pattern this repo hit there.
permissions:
  contents: write

jobs:
  release:
    if: >
      github.event.workflow_run.conclusion == 'success' &&
      github.event.workflow_run.head_branch == 'main'
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install python-semantic-release
        run: python3 -m pip install --quiet --upgrade pip python-semantic-release

      - name: Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          semantic-release version
          semantic-release publish
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python3 -c "import yaml, sys; yaml.safe_load(open('.github/workflows/release.yml'))" 2>&1 || python3 -m pip install --quiet pyyaml && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"`
Expected: no output (valid YAML), no exception raised.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add release.yml — semantic-release GitHub Releases after portability.yml goes green"
```

---

### Task 4: README rewrite with real captured demo

**Files:**
- Create: `examples/bootstrap-and-doctor/demo.sh`
- Create: `examples/bootstrap-and-doctor/demo.tape`
- Create: `examples/bootstrap-and-doctor/demo.gif` (generated by VHS, not hand-written)
- Modify: `README.md` (full rewrite, currently 15 lines)

**Interfaces:**
- Consumes: Task 3's `release.yml` (for the release badge URL), the already-shipped `--migrate-to-plugin` flag on `install.sh` (from the Phase-2 plugin migration, already merged to `main` in PR #1) for the install-section plugin subsection.
- Produces: nothing further tasks depend on — this is the last task in the plan.

- [ ] **Step 1: Write the demo script**

Create `examples/bootstrap-and-doctor/demo.sh` (isolates both `bootstrap.sh` and `doctor.sh` from the real machine's `~/.claude` via env var overrides, so recording this demo never mutates real state):

```bash
#!/usr/bin/env bash
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DEMO_HOME="$(mktemp -d)"
export CLAUDE_SKILLS="$DEMO_HOME/.claude/skills"
export CLAUDE_DIR="$DEMO_HOME/.claude"
mkdir -p "$CLAUDE_DIR"

"$CORE_DIR/bootstrap.sh"
"$CORE_DIR/doctor.sh" || true   # doctor.sh exits 1 on WARN — expected in this isolated demo dir

rm -rf "$DEMO_HOME"
```

- [ ] **Step 2: Run the demo script directly to confirm it works and see real output**

Run: `chmod +x examples/bootstrap-and-doctor/demo.sh && ./examples/bootstrap-and-doctor/demo.sh`
Expected: prints `linked models-router` / `linked delegation-discipline` / `linked claude-cost-audit` / the bootstrap "done. Next: ..." line, then doctor.sh's `PASS`/`WARN` lines. Exit code may be non-zero because of the `|| true` (doctor.sh will legitimately WARN in a freshly isolated dir with no plugin installed and no downbeat) — this is expected and correct, not a bug to fix.

- [ ] **Step 3: Record the VHS tape**

Create `examples/bootstrap-and-doctor/demo.tape`:

```
Output examples/bootstrap-and-doctor/demo.gif
Set FontSize 16
Set Width 900
Set Height 500
Set Theme "Dracula"

Type "./examples/bootstrap-and-doctor/demo.sh"
Enter
Sleep 3s
```

Run: `cd ~/dev/claude-core && vhs examples/bootstrap-and-doctor/demo.tape`
Expected: `examples/bootstrap-and-doctor/demo.gif` is created (a real recording of the actual script output — do not hand-edit or fabricate the GIF's content).

- [ ] **Step 4: Rewrite `README.md`**

```markdown
# claude-core

[![CI](https://img.shields.io/github/actions/workflow/status/FreddieMcHeart/claude-core/portability.yml?branch=main&label=ci)](https://github.com/FreddieMcHeart/claude-core/actions/workflows/portability.yml)
[![Release](https://img.shields.io/github/v/release/FreddieMcHeart/claude-core)](https://github.com/FreddieMcHeart/claude-core/releases)

Portable, project-agnostic Claude Code methodology — **no Mondu data**. The reusable
"operating manual" you carry to every machine/project: cost-discipline reflexes,
delegation rules, and a native Claude Code plugin, versioned and released
automatically from conventional commits.

![claude-core demo: bootstrap.sh symlinks skills, doctor.sh reports health](./examples/bootstrap-and-doctor/demo.gif)

Want to see it before installing? [`examples/bootstrap-and-doctor/`](./examples/bootstrap-and-doctor/)
is a one-command walkthrough (this GIF is `demo.sh` from that directory, recorded
verbatim with [VHS](https://github.com/charmbracelet/vhs)).

## Install

```bash
git clone <this-repo> ~/dev/claude-core
~/dev/claude-core/bootstrap.sh        # symlinks skills into ~/.claude/skills
```

### Native Claude Code plugin (optional)

`claude-core`'s cost-discipline hook also ships as a native Claude Code plugin,
an alternative to hand-merging hook entries into `~/.claude/settings.json`:

```bash
claude plugin install ~/dev/claude-core/.claude-plugin
```

Already ran the old installer and hand-merged the hooks yourself? Migrate cleanly
first, then install the plugin:

```bash
~/dev/claude-core/install.sh --migrate-to-plugin
claude plugin install ~/dev/claude-core/.claude-plugin
```

Run `./doctor.sh` any time to check hook registration, plugin detection, and
overall health.

## Contents

- `skills/models-router` — pick the cheapest model that fits the turn.
- `skills/delegation-discipline` — when/what to delegate to sub-agents.
- `skills/claude-cost-audit` — measure session cost + waste patterns.
- `.claude-plugin/` (`claude-core-hooks`) — native Claude Code plugin packaging
  of the cost-discipline hook (`hooks/hooks.json`), an alternative to manual
  `settings.json` editing.
- `bootstrap.sh` — symlink the skills above into `~/.claude/skills` on a fresh
  machine.
- `doctor.sh` — health check: hook registration (hand-merged or plugin), plugin
  detection, wiki submodule presence.

## Releases

Versions are cut automatically by [python-semantic-release](https://python-semantic-release.readthedocs.io/)
from conventional commits (`feat:`/`fix:` trigger a release, `docs:`/`chore:`/`ci:`
do not) — see [CHANGELOG.md](./CHANGELOG.md) for the full history. Every release
creates a GitHub Release and syncs `.claude-plugin/plugin.json`'s version.

Companion: `claude-core-wiki` (methodology notes, mounted as a `docs/core`
submodule in any Obsidian vault).
```

Note: `CHANGELOG.md` does not exist yet at this point in the plan — it will be created by the first real run of `release.yml` (Task 3) after this task's commit lands on `main` and a `feat`/`fix` commit follows. The README link is written now in anticipation of that; this is the same ordering `downbeat` used (README referenced files the release pipeline created on its first real run).

- [ ] **Step 5: Commit**

```bash
git add README.md examples/bootstrap-and-doctor/
git commit -m "docs: rewrite README with real demo, plugin install path, release process"
```

---

## Post-plan verification (not a task — do after all 4 tasks land on `main`)

After this plan's commits are on `main` and `portability.yml` runs green:

1. Confirm `release.yml` actually fires (check the Actions tab — `workflow_run` triggers are sometimes delayed or require the workflow file to already exist on the default branch before the trigger is recognized).
2. Read the real job log (not just "didn't fail") for the first live run, per the OSS playbook's verification gate: confirm `CHANGELOG.md` was generated with sensible content, `.claude-plugin/plugin.json`'s version actually changed, the GitHub Release body reads correctly, and no PyPI-related step was attempted.
3. If no `feat`/`fix` commit exists after `v0.1.0` at that point (only `docs`/`ci` commits from this plan), `semantic-release version` will correctly report "no release necessary" — that's expected, not a bug. A real release will only cut once a `feat:`/`fix:` commit lands after this plan's commits.
