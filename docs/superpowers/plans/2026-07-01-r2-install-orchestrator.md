# R2 — `claude-platform install` Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `claude-core/install.sh` into a one-command orchestrator that stands up the full harness on a fresh machine (skills+hook symlinks, wiki submodule, cost-discipline hook wired into settings.json, optional relay), while remaining a byte-for-byte no-op when re-run on the owner's machine.

**Architecture:** `install.sh` stays a Bash composer that shells out to already-idempotent units (bootstrap.sh, git submodule, a new `lib/settings_merge.py`, `claude-relay init`, doctor.sh). The one piece of new real logic is `settings_merge.py`, a self-contained idempotent settings.json hook-merger. No new network entrypoint; no `--adapter`.

**Tech Stack:** Bash (install.sh, doctor.sh), Python 3.11+ stdlib only (settings_merge.py — json, os, sys, time, argparse, pathlib), pytest for the merger's tests, git submodules, uv (only when `--with-relay`).

## Global Constraints

- Python: stdlib only, target 3.11+ (`tomllib` already used by config_loader). No third-party runtime deps in settings_merge.py.
- Invariant (absolute): every step is additive and guarded; re-running on the owner's machine performs **zero writes** and leaves every file byte-identical.
- settings_merge.py MUST reproduce the EXACT cost-discipline command strings already present in the owner's live `~/.claude/settings.json` (only the home/CLAUDE_DIR path portion is parameterized). If the format differs, a re-run would append duplicates and reformat — that is a correctness failure.
- Malformed settings.json → timestamped backup + non-zero exit + original untouched (never clobber).
- Atomic writes only (temp file in same dir + `os.replace`); a timestamped `.bak-<ts>` is made ONLY when a write actually happens.
- Wiki submodule is REQUIRED: failure to add it aborts install (hard-fail), it is not a warning.
- claude-core must stay generic/owner-agnostic: no personal URLs hardcoded in tracked files. The wiki URL is supplied at runtime (flag or config), never committed to `.gitmodules`.
- Do NOT add `Co-Authored-By` to commits. Commit subjects use a short `[R2]` tag, not the branch name.

---

### Task 1: Add `wiki_url` to the config layer

**Files:**
- Modify: `~/dev/claude-core/lib/config_loader.py` (the `_DEFAULTS` dict)
- Modify: `~/dev/claude-core/platform.config.toml.example`
- Test: `~/dev/claude-core/tests/test_config_loader_wiki_url.py` (Create)

**Interfaces:**
- Produces: config key `wiki_url` (top-level string), readable via `python3 config_loader.py wiki_url` and `config['wiki_url']`. Default when absent: `""` (empty string — install.sh treats empty as "must be supplied").

- [ ] **Step 1: Write the failing test**

```python
# ~/dev/claude-core/tests/test_config_loader_wiki_url.py
import subprocess, sys, os, tempfile, textwrap
from pathlib import Path

LOADER = str(Path(__file__).resolve().parents[1] / "lib" / "config_loader.py")

def _run(key, home):
    env = {**os.environ, "HOME": home}
    return subprocess.run([sys.executable, LOADER, key], capture_output=True, text=True, env=env)

def test_wiki_url_default_is_empty_when_no_config(tmp_path):
    # No ~/.claude/platform.config.toml present → falls back to default.
    r = _run("wiki_url", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""

def test_wiki_url_read_from_config(tmp_path):
    cfg = tmp_path / ".claude" / "platform.config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('wiki_url = "git@github.com:me/wiki.git"\n')
    r = _run("wiki_url", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "git@github.com:me/wiki.git"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/claude-core && python3 -m pytest tests/test_config_loader_wiki_url.py -v`
Expected: FAIL — `config_loader.py wiki_url` currently prints `error: unknown key 'wiki_url'` and exits 1.

- [ ] **Step 3: Add `wiki_url` to `_DEFAULTS`**

In `lib/config_loader.py`, add one line to the top-level entries of the `_DEFAULTS` dict (next to `wiki_path`):

```python
    "wiki_url":       "",
```

(Place it right after the `"wiki_path": ...` line. Empty default = "not configured".)

- [ ] **Step 4: Document it in the example**

In `platform.config.toml.example`, add under the top block (after the `wiki_path` line):

```toml
wiki_url       = "git@github.com:<you>/claude-core-wiki.git"   # SSH; mounted as docs/core submodule by install.sh
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/dev/claude-core && python3 -m pytest tests/test_config_loader_wiki_url.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/claude-core
git add lib/config_loader.py platform.config.toml.example tests/test_config_loader_wiki_url.py
git commit -m "[R2] config: add wiki_url key (default empty)"
```

---

### Task 2: `lib/settings_merge.py` — idempotent cost-discipline hook merger

**Files:**
- Create: `~/dev/claude-core/lib/settings_merge.py`
- Test: `~/dev/claude-core/tests/test_settings_merge.py` (Create)

**Interfaces:**
- Produces: CLI `python3 lib/settings_merge.py --settings PATH --claude-dir DIR [--check]` and importable `merge(settings_path: Path, claude_dir: str) -> str` returning one of `"created"|"updated"|"unchanged"`; raises `SystemExit(2)` on malformed JSON. `--check` mode writes nothing: exit 0 if all 4 events already registered, exit 1 if a merge would be needed.
- Consumes: the cost-discipline hook at `<claude_dir>/hooks/cost-discipline.py`.

- [ ] **Step 1: Derive the canonical command format from the owner's live settings.json (CRITICAL — do this before writing code)**

Run: `python3 -c "import json; d=json.load(open('$HOME/.claude/settings.json')); print(json.dumps(d.get('hooks',{}), indent=2))" | grep -A2 -i cost-discipline`

Record the EXACT four command strings and the exact group shape (whether `matcher` is present per event, and whether the command uses an absolute path, a `~`, or a leading `python3 `). The constants below must reproduce that shape with only the home/CLAUDE_DIR portion swapped for the `--claude-dir` argument. If the observed mode-arg spellings differ from the defaults below (`pre-tool` / `post-tool` / `session-start` / `post-compact`), also confirm them against the argv dispatch at the bottom of `~/dev/claude-core/hooks/cost-discipline.py` and use the real spellings.

- [ ] **Step 2: Write the failing tests**

```python
# ~/dev/claude-core/tests/test_settings_merge.py
import json, sys, subprocess, importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "settings_merge.py"
spec = importlib.util.spec_from_file_location("settings_merge", MOD)
sm = importlib.util.module_from_spec(spec); spec.loader.exec_module(sm)

CD = "/home/u/.claude"   # a fixed claude-dir for deterministic command strings

def _load(p): return json.loads(Path(p).read_text())

def test_merge_into_absent_creates_all_four(tmp_path):
    s = tmp_path / "settings.json"
    assert sm.merge(s, CD) == "created"
    hooks = _load(s)["hooks"]
    for ev in ("PreToolUse", "PostToolUse", "SessionStart", "PostCompact"):
        assert ev in hooks and hooks[ev], f"{ev} missing"

def test_idempotent_second_run_unchanged_no_backup(tmp_path):
    s = tmp_path / "settings.json"
    sm.merge(s, CD)
    before = s.read_text()
    assert sm.merge(s, CD) == "unchanged"
    assert s.read_text() == before                       # byte-identical
    assert list(tmp_path.glob("settings.json.bak-*")) == []   # no backup on no-op

def test_preserves_foreign_hooks_and_keys(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "/other/thing.sh"}]}]}
    }))
    assert sm.merge(s, CD) == "updated"
    d = _load(s)
    assert d["model"] == "opus"
    cmds = [h["command"] for g in d["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "/other/thing.sh" in cmds                     # foreign hook kept
    assert any("cost-discipline.py" in c for c in cmds)  # ours added

def test_skip_already_registered(tmp_path):
    s = tmp_path / "settings.json"
    sm.merge(s, CD)
    d = _load(s); n = len(d["hooks"]["PreToolUse"])
    assert sm.merge(s, CD) == "unchanged"
    assert len(_load(s)["hooks"]["PreToolUse"]) == n     # no duplicate

def test_malformed_aborts_backs_up_and_leaves_original(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{ this is not json ")
    orig = s.read_text()
    try:
        sm.merge(s, CD); assert False, "should have raised SystemExit"
    except SystemExit as e:
        assert e.code == 2
    assert s.read_text() == orig                         # untouched
    assert list(tmp_path.glob("settings.json.malformed-*"))  # backup made

def test_check_mode_cli(tmp_path):
    s = tmp_path / "settings.json"
    # --check on absent file: a merge WOULD be needed → exit 1
    r = subprocess.run([sys.executable, str(MOD), "--settings", str(s),
                        "--claude-dir", CD, "--check"])
    assert r.returncode == 1
    sm.merge(s, CD)
    # after merge, --check → exit 0 (nothing to do)
    r = subprocess.run([sys.executable, str(MOD), "--settings", str(s),
                        "--claude-dir", CD, "--check"])
    assert r.returncode == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/dev/claude-core && python3 -m pytest tests/test_settings_merge.py -v`
Expected: FAIL / collection error — `settings_merge.py` does not exist yet.

- [ ] **Step 4: Write the implementation**

```python
# ~/dev/claude-core/lib/settings_merge.py
#!/usr/bin/env python3
"""settings_merge.py — idempotently merge the cost-discipline hook into settings.json.

Self-contained (no relay dependency). Registers the cost-discipline.py command under four
hook events, skipping any already present. Malformed settings.json -> timestamped backup +
exit 2, original untouched. Atomic write; a .bak-<ts> is made only when a write happens.

Usage:
    python3 settings_merge.py --settings ~/.claude/settings.json --claude-dir ~/.claude
    python3 settings_merge.py --settings ... --claude-dir ... --check   # dry-run, exit 0/1
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# (event, matcher, mode-arg). matcher "" => group has no "matcher" key.
# NOTE: confirm these against the owner's live settings.json (Task 2 Step 1) before shipping.
HOOK_EVENTS = [
    ("PreToolUse",   "*", "pre-tool"),
    ("PostToolUse",  "*", "post-tool"),
    ("SessionStart", "",  "session-start"),
    ("PostCompact",  "",  "post-compact"),
]


def hook_command(claude_dir: str, mode: str) -> str:
    # Must match the owner's live command format exactly (Task 2 Step 1).
    return f"{claude_dir}/hooks/cost-discipline.py {mode}"


def _group(matcher: str, command: str) -> dict:
    grp = {"hooks": [{"type": "command", "command": command}]}
    return {"matcher": matcher, **grp} if matcher else grp


def _already_registered(event_list: list, command: str) -> bool:
    for grp in event_list:
        for h in grp.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def _would_change(data: dict, claude_dir: str) -> bool:
    hooks = data.get("hooks", {})
    for event, _matcher, mode in HOOK_EVENTS:
        if not _already_registered(hooks.get(event, []), hook_command(claude_dir, mode)):
            return True
    return False


def _read(settings_path: Path):
    """Return (data, existed). Malformed -> backup + SystemExit(2)."""
    if not settings_path.exists():
        return {}, False
    try:
        return json.loads(settings_path.read_text()), True
    except json.JSONDecodeError:
        bak = settings_path.with_name(f"{settings_path.name}.malformed-{int(time.time())}")
        bak.write_bytes(settings_path.read_bytes())
        print(f"ERROR: {settings_path} is not valid JSON. Backed up to {bak}. "
              f"Aborting without modifying it.", file=sys.stderr)
        raise SystemExit(2)


def merge(settings_path: Path, claude_dir: str) -> str:
    settings_path = Path(settings_path)
    data, existed = _read(settings_path)

    hooks = data.setdefault("hooks", {})
    changed = False
    for event, matcher, mode in HOOK_EVENTS:
        cmd = hook_command(claude_dir, mode)
        lst = hooks.setdefault(event, [])
        if not _already_registered(lst, cmd):
            lst.append(_group(matcher, cmd))
            changed = True

    if not changed:
        return "unchanged"

    if existed:
        bak = settings_path.with_name(f"{settings_path.name}.bak-{int(time.time())}")
        bak.write_bytes(settings_path.read_bytes())
    tmp = settings_path.with_name(f"{settings_path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, settings_path)
    return "created" if not existed else "updated"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"))
    ap.add_argument("--check", action="store_true",
                    help="dry-run: exit 0 if fully registered, 1 if a merge is needed")
    args = ap.parse_args(argv)
    settings_path = Path(args.settings)

    if args.check:
        data, _ = _read(settings_path)   # malformed still exits 2
        return 1 if _would_change(data, args.claude_dir) else 0

    result = merge(settings_path, args.claude_dir)
    print(f"settings_merge: {result} ({settings_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/dev/claude-core && python3 -m pytest tests/test_settings_merge.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Owner-non-break check against the REAL live settings.json**

Run:
```bash
cp ~/.claude/settings.json /tmp/settings.owner.json
python3 ~/dev/claude-core/lib/settings_merge.py --settings /tmp/settings.owner.json --claude-dir "$HOME/.claude"
```
Expected: prints `settings_merge: unchanged (/tmp/settings.owner.json)` and NO `.bak-*` file appears next to it. If it prints `updated`, the command format in `hook_command()` does not match the owner's live format — fix `HOOK_EVENTS`/`hook_command` per Step 1 and repeat until it reports `unchanged`.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/claude-core
chmod +x lib/settings_merge.py
git add lib/settings_merge.py tests/test_settings_merge.py
git commit -m "[R2] add self-contained settings.json hook merger (idempotent, atomic, backup-on-write)"
```

---

### Task 3: install.sh — argument parsing + preflight

**Files:**
- Modify: `~/dev/claude-core/install.sh:10-18` (after `set -euo pipefail` / CLAUDE_DIR; replace the single `--doctor` check with a flag loop + preflight)

**Interfaces:**
- Produces: shell vars `WITH_RELAY` (0/1), `WIKI_URL_OVERRIDE` (string, may be empty). `--doctor` behavior preserved. Preflight aborts with exit 1 if `git` or `python3` missing, or if `uv` missing when `--with-relay` given.

- [ ] **Step 1: Replace lines 12-18 with arg parsing + preflight**

Current (lines 12-18):
```bash
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

# --doctor flag: delegate and exit
if [ "${1:-}" = "--doctor" ]; then
    exec "$CORE_DIR/doctor.sh"
fi
```

Replace with:
```bash
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

WITH_RELAY=0
WIKI_URL_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --doctor)     exec "$CORE_DIR/doctor.sh" ;;
        --with-relay) WITH_RELAY=1 ;;
        --wiki-url)   WIKI_URL_OVERRIDE="${2:?--wiki-url needs a value}"; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
    shift
done

# ── preflight ────────────────────────────────────────────────────────────────
command -v git      >/dev/null 2>&1 || { echo "FATAL: git not found" >&2; exit 1; }
command -v python3  >/dev/null 2>&1 || { echo "FATAL: python3 not found" >&2; exit 1; }
if [ "$WITH_RELAY" -eq 1 ]; then
    command -v uv >/dev/null 2>&1 || { echo "FATAL: --with-relay needs uv (https://docs.astral.sh/uv/)" >&2; exit 1; }
fi
```

- [ ] **Step 2: Verify --doctor still works and preflight passes**

Run: `cd ~/dev/claude-core && bash install.sh --doctor`
Expected: doctor output (7 PASS on the owner machine), unchanged from before.

Run: `cd ~/dev/claude-core && bash -n install.sh && echo "syntax ok"`
Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-core
git add install.sh
git commit -m "[R2] install.sh: flag parsing (--with-relay/--wiki-url) + preflight"
```

---

### Task 4: install.sh — required wiki submodule step

**Files:**
- Modify: `~/dev/claude-core/install.sh` (new section after the CLAUDE.md step `d`, before "Next steps")

**Interfaces:**
- Consumes: `WIKI_URL_OVERRIDE` (Task 3), config `wiki_url` (Task 1).
- Produces: `docs/core` git submodule inside `$CORE_DIR`. Hard-abort (exit 1) if the URL is unresolved or the submodule add fails.

- [ ] **Step 1: Insert the submodule section (after line ~62, before the "Next steps" echo block)**

```bash
# ── e. Wiki submodule (REQUIRED) ─────────────────────────────────────────────
WIKI_URL="$WIKI_URL_OVERRIDE"
if [ -z "$WIKI_URL" ] && [ -f "$CLAUDE_DIR/lib/config_loader.py" ]; then
    WIKI_URL="$(python3 "$CLAUDE_DIR/lib/config_loader.py" wiki_url 2>/dev/null || true)"
fi
if [ -d "$CORE_DIR/docs/core" ] && git -C "$CORE_DIR" submodule status docs/core >/dev/null 2>&1; then
    echo "✓ docs/core submodule already present — leaving"
elif [ -z "$WIKI_URL" ]; then
    echo "FATAL: wiki_url not set. Pass --wiki-url <ssh-url> or set wiki_url in $CLAUDE_DIR/platform.config.toml" >&2
    exit 1
else
    echo "→ Adding wiki submodule at docs/core ($WIKI_URL)..."
    git -C "$CORE_DIR" submodule add "$WIKI_URL" docs/core \
        || { echo "FATAL: failed to add wiki submodule (auth to $WIKI_URL?)" >&2; exit 1; }
    git -C "$CORE_DIR" submodule update --init docs/core \
        || { echo "FATAL: failed to init wiki submodule" >&2; exit 1; }
    echo "✓ Wiki mounted at $CORE_DIR/docs/core"
fi
```

- [ ] **Step 2: Test the "already present" and "missing url" branches in an isolated clone**

```bash
# missing-url hard-abort:
tmp=$(mktemp -d); cp -R ~/dev/claude-core/. "$tmp/core"; rm -rf "$tmp/core/docs/core"
HOME="$tmp/home" CLAUDE_DIR="$tmp/home/.claude" bash "$tmp/core/install.sh" ; echo "exit=$?"
```
Expected: aborts with `FATAL: wiki_url not set...` and `exit=1` (because the isolated home has no wiki_url and no --wiki-url).

```bash
# happy path with an explicit url (use the real wiki so it resolves):
HOME="$tmp/home2" CLAUDE_DIR="$tmp/home2/.claude" bash "$tmp/core/install.sh" --wiki-url git@github.com:FreddieMcHeart/claude-core-wiki.git ; echo "exit=$?"
```
Expected: `✓ Wiki mounted at .../docs/core`, `exit=0`, and `ls "$tmp/core/docs/core"` shows the wiki files. (Then `git -C "$tmp/core" submodule deinit -f docs/core` to clean the temp clone.)

- [ ] **Step 3: Keep the personal wiki URL out of tracked files**

`git submodule add` writes the URL into `.gitmodules`. To keep claude-core generic (no owner-specific URL committed), ignore both the submodule metadata and its checkout in the core repo — each user's install.sh recreates the submodule locally with their own `wiki_url`:

```bash
cd ~/dev/claude-core
grep -qxF '/.gitmodules' .gitignore 2>/dev/null || printf '/.gitmodules\n/docs/core\n' >> .gitignore
```

- [ ] **Step 4: Commit**

```bash
cd ~/dev/claude-core
git add install.sh .gitignore
git commit -m "[R2] install.sh: required docs/core wiki submodule (url from flag/config, hard-fail)"
```

---

### Task 5: install.sh — wire the cost-discipline hook into settings.json

**Files:**
- Modify: `~/dev/claude-core/install.sh` (new section after the wiki submodule step)

**Interfaces:**
- Consumes: `lib/settings_merge.py` (Task 2), which must also be reachable at `$CLAUDE_DIR/lib/settings_merge.py` on the target — install copies it there like config_loader.py.

- [ ] **Step 1: Extend step `c` so settings_merge.py is installed alongside config_loader.py**

After the existing config_loader copy block (install.sh lines 38-47), add:

```bash
# c2. settings_merge.py — copy to ~/.claude/lib/ if absent
MERGE_SRC="$CORE_DIR/lib/settings_merge.py"
MERGE_DST="$CLAUDE_DIR/lib/settings_merge.py"
if [ -f "$MERGE_DST" ]; then
    echo "✓ $MERGE_DST already present — leaving"
else
    mkdir -p "$CLAUDE_DIR/lib"
    cp "$MERGE_SRC" "$MERGE_DST"
    echo "✓ Installed settings_merge.py → $MERGE_DST"
fi
```

- [ ] **Step 2: Add the merge call (after the wiki submodule section)**

```bash
# ── f. Wire cost-discipline hook into settings.json (idempotent) ─────────────
echo "→ Merging cost-discipline hook into settings.json..."
python3 "$CORE_DIR/lib/settings_merge.py" \
    --settings "$CLAUDE_DIR/settings.json" \
    --claude-dir "$CLAUDE_DIR" \
    || { echo "FATAL: settings.json merge failed (malformed JSON?)" >&2; exit 1; }
```

- [ ] **Step 3: Test on an isolated home (created + idempotent)**

```bash
tmp=$(mktemp -d)
HOME="$tmp" CLAUDE_DIR="$tmp/.claude" bash ~/dev/claude-core/install.sh --wiki-url git@github.com:FreddieMcHeart/claude-core-wiki.git
python3 -c "import json; h=json.load(open('$tmp/.claude/settings.json'))['hooks']; print(sorted(h))"
```
Expected: prints `['PostCompact', 'PostToolUse', 'PreToolUse', 'SessionStart']`.
Re-run the same install line → second run prints `settings_merge: unchanged` and `diff` of settings.json before/after is empty.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/claude-core
git add install.sh
git commit -m "[R2] install.sh: install settings_merge.py + wire cost-discipline hook"
```

---

### Task 6: install.sh — `--with-relay` branch + final doctor gate

**Files:**
- Modify: `~/dev/claude-core/install.sh` (relay branch + replace the tail "Next steps" echo with a doctor gate)

**Interfaces:**
- Consumes: `WITH_RELAY` (Task 3). Runs `uv tool install claude-relay` + `claude-relay init` only when set.

- [ ] **Step 1: Add the relay branch (after the settings merge section)**

```bash
# ── g. Relay (opt-in) ─────────────────────────────────────────────────────────
if [ "$WITH_RELAY" -eq 1 ]; then
    echo "→ Installing claude-relay..."
    uv tool install claude-relay || { echo "FATAL: uv tool install claude-relay failed" >&2; exit 1; }
    claude-relay init || { echo "FATAL: claude-relay init failed" >&2; exit 1; }
    echo "✓ Relay installed and initialized"
else
    echo "ℹ  Relay skipped (pass --with-relay to enable multi-session)"
fi
```

- [ ] **Step 2: Replace the tail (current lines 64-74, the "Next steps" echo) with a doctor gate**

```bash
# ── h. Verify ─────────────────────────────────────────────────────────────────
echo ""
echo "=== verifying (doctor) ==="
exec "$CORE_DIR/doctor.sh"
```

(`exec` makes the installer's exit status equal doctor's — a real gate. Everything above already ran; a WARN surfaces loudly without hiding that install completed.)

- [ ] **Step 3: Full isolated-home run, no relay**

```bash
tmp=$(mktemp -d)
HOME="$tmp" CLAUDE_DIR="$tmp/.claude" bash ~/dev/claude-core/install.sh --wiki-url git@github.com:FreddieMcHeart/claude-core-wiki.git; echo "exit=$?"
```
Expected: ends with doctor `Summary: N PASS, ...`. `exit=0` if project_root default resolves; if the isolated home's default project_root doesn't exist that single WARN is expected — note it, it is not an install failure.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/claude-core
git add install.sh
git commit -m "[R2] install.sh: opt-in --with-relay branch + final doctor gate"
```

---

### Task 7: doctor.sh — three new checks

**Files:**
- Modify: `~/dev/claude-core/doctor.sh` (add checks after the existing platform_config check, before the Summary block at line 59)

**Interfaces:**
- Consumes: `settings_merge.py --check` (Task 2), the `docs/core` submodule (Task 4), relay hooks (installed by `claude-relay init`).

- [ ] **Step 1: Insert three checks before the Summary block (before line 59)**

```bash
# ── 5. cost-discipline hook registered in settings.json ──────────────────────
CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$CLAUDE_DIR/lib/settings_merge.py" ]; then
    if python3 "$CLAUDE_DIR/lib/settings_merge.py" --settings "$CLAUDE_DIR/settings.json" --claude-dir "$CLAUDE_DIR" --check >/dev/null 2>&1; then
        _pass "hook:cost-discipline registered"
    else
        _warn "hook:cost-discipline" "not fully registered in settings.json — run ./install.sh"
    fi
else
    _warn "hook:cost-discipline" "settings_merge.py absent — run ./install.sh"
fi

# ── 6. docs/core wiki submodule resolves ─────────────────────────────────────
if [ -d "$CORE_DIR/docs/core" ] && [ -n "$(ls -A "$CORE_DIR/docs/core" 2>/dev/null)" ]; then
    _pass "wiki:docs/core submodule"
else
    _warn "wiki:docs/core" "submodule missing or empty — run ./install.sh (needs wiki_url)"
fi

# ── 7. relay hooks (only if relay is installed) ──────────────────────────────
if command -v claude-relay >/dev/null 2>&1; then
    if grep -q "relay-inbox.py" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
        _pass "relay:hooks registered"
    else
        _warn "relay:hooks" "claude-relay installed but hooks not in settings.json — run 'claude-relay init'"
    fi
fi
```

- [ ] **Step 2: Run doctor on the owner machine + an isolated install**

Run: `bash ~/dev/claude-core/doctor.sh; echo "exit=$?"`
Expected on owner: the 3 new lines appear; hook check PASSes (owner has it wired), submodule check WARNs unless the owner has run the R2 install to add docs/core (note it), relay check PASSes (relay installed). Confirm no check crashes.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-core
git add doctor.sh
git commit -m "[R2] doctor.sh: check hook-registered + docs/core submodule + relay hooks"
```

---

### Task 8: End-to-end fresh-machine smoke test

**Files:**
- Create: `~/dev/claude-core/tests/smoke_install.sh`

**Interfaces:**
- Consumes: the finished install.sh + doctor.sh. Runs the whole orchestrator against an isolated `$HOME` and asserts the post-conditions + idempotency.

- [ ] **Step 1: Write the smoke test**

```bash
#!/usr/bin/env bash
# Fresh-$HOME end-to-end smoke test for claude-platform install (no relay).
set -uo pipefail
CORE="$(cd "$(dirname "$0")/.." && pwd)"
WIKI_URL="${WIKI_URL:-git@github.com:FreddieMcHeart/claude-core-wiki.git}"
TMP="$(mktemp -d)"; trap 'git -C "$CORE" submodule deinit -f docs/core >/dev/null 2>&1; rm -rf "$TMP"' EXIT
FAIL=0
ck() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAIL=1; fi; }

echo "=== run 1 (fresh install) ==="
HOME="$TMP" CLAUDE_DIR="$TMP/.claude" bash "$CORE/install.sh" --wiki-url "$WIKI_URL" >/dev/null 2>&1 || true

ck "skills symlinked"      '[ -L "$TMP/.claude/skills/models-router" ]'
ck "hook symlinked"        '[ -L "$TMP/.claude/hooks/cost-discipline.py" ]'
ck "config copied"         '[ -f "$TMP/.claude/platform.config.toml" ]'
ck "settings has 4 events" 'python3 -c "import json;h=json.load(open(\"$TMP/.claude/settings.json\"))[\"hooks\"];exit(0 if all(e in h for e in [\"PreToolUse\",\"PostToolUse\",\"SessionStart\",\"PostCompact\"]) else 1)"'
ck "wiki submodule present" '[ -n "$(ls -A "$CORE/docs/core" 2>/dev/null)" ]'
ck "no host /Users path in settings" '! grep -q "/Users/" "$TMP/.claude/settings.json"'

echo "=== run 2 (idempotency) ==="
cp "$TMP/.claude/settings.json" "$TMP/settings.before"
HOME="$TMP" CLAUDE_DIR="$TMP/.claude" bash "$CORE/install.sh" --wiki-url "$WIKI_URL" >/dev/null 2>&1 || true
ck "settings unchanged on re-run" 'diff -q "$TMP/settings.before" "$TMP/.claude/settings.json" >/dev/null'
ck "no stray settings backup"     '[ -z "$(ls "$TMP/.claude/"settings.json.bak-* 2>/dev/null)" ]'

echo ""; [ "$FAIL" -eq 0 ] && echo "SMOKE: all pass" || echo "SMOKE: FAILURES"; exit "$FAIL"
```

- [ ] **Step 2: Run it**

Run: `chmod +x ~/dev/claude-core/tests/smoke_install.sh && bash ~/dev/claude-core/tests/smoke_install.sh`
Expected: every line `PASS`, final `SMOKE: all pass`, exit 0.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/claude-core
git add tests/smoke_install.sh
git commit -m "[R2] tests: fresh-\$HOME e2e smoke + idempotency"
```

---

## Post-plan notes

- The whole plan is claude-core-local; the parent session pushes claude-core after review (children/agents commit locally only).
- `docs/core` submodule on the OWNER's machine: the owner already has a standalone `~/dev/claude-core-wiki` clone; running R2 install additionally mounts the wiki as `docs/core` inside the core clone. That is intended (submodule is the harness mount mechanism) and additive.
- R3 (CI portability gate) consumes `tests/smoke_install.sh` + a fresh-container matrix — out of scope here.
