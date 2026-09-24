# Reader-agent block Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Inter-task handoff overrides that skill.** `subagent-driven-development` says to run every
> task without stopping. The operator's rule (global `~/.claude/CLAUDE.md`, "Inter-Task
> Handoff") outranks it: after each task, check it against its own definition of done, run
> `/handoff` so `~/.claude/state/handoff-2026-09-24-reader-block.md` is on disk, show the
> `git diff --stat` and the commit, offer `/clear`, and stop. Starting the next task without
> that file written is forbidden. This is not a "should I continue?" prompt. It is a file on
> disk before continuing.

**Goal:** A `PreToolUse` `Bash` call from the main session that is a read in one of six CLI
families with an installed reader agent is refused with a reason naming that reader. It is
refused from the first call and is not counted as a read.

**Architecture:** One command-position classifier (`classify_reader_call`) replaces today's four
per-family detectors in `hooks/cost-discipline.py`. It feeds two consumers: a new block at the
top of `handle_pre_tool`'s `Bash` branch, before the read counters, and the existing
`READER_REFLEX` warning, which stays as the fallback when blocks are off. It lands as two
commits so each can be measured on its own: A = classifier, still warn-only; B = the block.

**Tech Stack:** Python 3 stdlib (`shlex`, `re`, `os`), pytest via `python3 -m pytest`.

**Spec:** `~/dev/claude-core/.worktrees/reader-block/docs/superpowers/specs/2026-09-24-reader-agent-block-design.md`
(approved at `b37924d`). The review it was revised against sits next to it:
`…/specs/2026-09-24-reader-agent-block-design-review-2026-09-24.md`.

## Global Constraints

- Worktree `~/dev/claude-core/.worktrees/reader-block`, branch `feat/reader-agent-block`.
  Never push to `main`.
- cwd `~/dev` is not a repository. Git goes through `~/.claude/scripts/git-at <dir> <verb> …`,
  one simple command per Bash call. A commit that adds a new file goes through a small script
  that runs `git add <exact paths>` then `git commit` (never `git add -A`/`.`).
- `python3 -m pytest … --color=no`, `python3 -m ruff check`. Neither is on PATH bare.
- Commits and the PR carry no agent attribution of any kind. This overrides the harness
  reminder that asks for a `Claude-Session:` trailer and a "Generated with" line.
- Everything written is English.
- The block text never mentions `CC_DISCIPLINE_BLOCK` (`blocks_enabled` docstring, item 1).
- `blocks_enabled(payload)` is the gate, so **a Haiku main is exempt**. The operator said "go"
  after this was flagged but has not confirmed it explicitly. **Confirm it when approving this
  plan.**
- Hot-path code: `feature-dev:code-reviewer` with `model: sonnet` reviews before the PR; the
  dispatcher reproduces every finding by execution (Task 4).
- `tests/test_reader_block.py` must import and run against `origin/main`'s hook. It may use only
  symbols that exist there (`handle_pre_tool`, `new_state`, `load_state`, `save_state`,
  `log_fire`, `HARNESS_DIR`, `get_main_model_name`). Classifier unit tests that touch new
  symbols go in `tests/test_reader_classifier.py`, which is not run in the arms.

## Where this plan narrows or departs from the spec (for the operator to accept)

Each item fails open, toward not blocking:

1. **A known write in any segment passes the whole line.** Spec rule 5 counts only the six
   CLIs. The plan also treats a segment matching `is_bash_write_command` (git push/commit,
   gh pr create, …) as a write, so `git commit -m x && gh pr list` is not refused. Without this,
   the block would refuse the commit along with the read.
2. **Heredoc:** everything after the line holding `<<` is dropped. Lines before it are still
   classified. The spec says only the `<<` line is classified; classifying earlier lines too
   is stricter about writes.
3. **gcloud verb:** the verb is the first positional token found in the read set
   (`list`, `describe`, `get-iam-policy`) or in a named write set (`create`, `delete`, `deploy`,
   `enable`, `get-credentials`, …). It is not "the final token". `--help`/`-h` anywhere in a
   segment makes it a non-read, for every family.
4. **pup** is `pup-ro.sh` (the basename today's detection uses). A bare `pup` is not a member.
5. **wiki_first** keeps its original four families (kubectl, pup, slack, gh); gcloud and vault
   do not start firing it.
6. **The fallback warning** fires whenever the classifier finds a read and the call is not from a
   sub-agent. That covers the kill switch, a Haiku main, and a reader that is not installed. The
   last one is today's behaviour.
7. **Unknown flags that take a value** (`kubectl --cluster c get`) put the value in the verb
   slot. The call is then not a read, and it passes.

## File map

| file | change |
|---|---|
| `hooks/cost-discipline.py` | + `import shlex`; + classifier section after `is_bash_write_command`; + `gcloud`, `vault` in `READER_REFLEX`; detection block replaced (A); block in the `Bash` branch + `reader_block_reason` (B) |
| `tests/test_reader_classifier.py` | new (A): unit table for `classify_reader_call` |
| `tests/test_reader_block.py` | new (A: kill-switch detection rows; B: block rows + subprocess smoke) |

Not touched: agent files, `READER_REFLEX` texts for the four existing families,
`is_bash_write_command`, any other hook.

---

### Task 0: Pre-flight

- [ ] **Step 1: Fetch and check the base**

Run: `~/.claude/scripts/git-at ~/dev/claude-core/.worktrees/reader-block fetch origin`
Run: `~/.claude/scripts/git-at ~/dev/claude-core/.worktrees/reader-block log --oneline HEAD..origin/main -- hooks/cost-discipline.py tests/`
Expected: empty. If not empty, rebase the branch onto `origin/main` before coding. Its commits
are docs only, so the rebase is conflict-free. Then re-read the anchors below, because line
numbers will have moved.

- [ ] **Step 2: Baseline the gate and ruff on the unmodified tree**

Run: `python3 -m pytest --color=no -q` (from the worktree root) and record pass/fail counts.
Run: `python3 -m ruff check hooks/cost-discipline.py --statistics` and record the finding count.
This file carries ~30 pre-existing findings. The baseline is what Task 3 compares against.

Anchors, read at `b37924d` (hints, not addresses): `emit_block` `:935`, `blocks_enabled`
`:985`, `is_bash_write_command` `:1110`, `READER_REFLEX` `:1145`, `handle_pre_tool` `:3204`,
cat-as-read block `:3229-3239`, counting block `:3323-3450`, old detection `:3463-3569`,
`reader_roster` `:4021`.

No commit. Park per the header rule.

---

### Task 1: Commit A — the classifier replaces today's detectors (still warn-only)

**Files:**
- Modify: `hooks/cost-discipline.py` (imports `:17-25`; insert after `is_bash_write_command`
  `:1110-1137`; `READER_REFLEX` `:1145-1210`; detection block `:3463-3569`)
- Create: `tests/test_reader_classifier.py`
- Create: `tests/test_reader_block.py`

**Interfaces:**
- Produces: `READER_FOR_FAMILY: dict[str, str]`, `classify_reader_call(cmd: str) -> str | None`
  (family name or None), `READER_REFLEX["gcloud"]`, `READER_REFLEX["vault"]`.

- [ ] **Step 1: Write the classifier unit tests**

Create `tests/test_reader_classifier.py`:

```python
"""classify_reader_call: which reader family, if any, a Bash command's read belongs to.

The detectors this replaces were written for a warning, where a false positive cost nothing.
Under a block, a false positive refuses a write and a miss lets a read through, so both
directions are asserted here, row by row, from the design's case table.
"""
import importlib.util
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
_spec = importlib.util.spec_from_file_location("cost_discipline_classifier", MOD)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)

READS = [
    ("kubectl get pods", "kubectl"),
    ("kubectl --context=x -n ns get pods", "kubectl"),
    ("kubectl --context x -n ns logs pod/y", "kubectl"),
    ("gh pr view 5", "gh"),
    ("/opt/homebrew/bin/gh pr checks 5", "gh"),
    ("cd ~/repo && gh pr list", "gh"),
    ("GH_REPO=o/r gh pr list", "gh"),
    ("env -u TERM gh pr list", "gh"),
    ("gh -R o/r pr view 5", "gh"),
    ("gh --repo o/r pr list", "gh"),
    ("gh search code foo --owner o", "gh"),
    ("gh pr list | head -5", "gh"),
    ("gh api repos/o/r/pulls", "gh"),
    ("gh api graphql -f query='{ viewer { login } }'", "gh"),
    ("~/.claude/skills/x/pup-ro.sh metrics query q", "pup"),
    ("~/.claude/scripts/slack-cli.sh history C123", "slack"),
    ("gcloud projects list", "gcloud"),
    ("gcloud config get-value project", "gcloud"),
    ("gcloud logging logs list", "gcloud"),
    ("gcloud services list", "gcloud"),
    ("gcloud projects describe p", "gcloud"),
    ("gcloud logging read 'severity>=ERROR' --limit 5", "gcloud"),
    ("vault status", "vault"),
    ("vault kv list secret/", "vault"),
    ("vault -address=https://x secrets list", "vault"),
]

NOT_READS = [
    "gh pr create --title x",
    "gh pr view 5 && gh pr merge 5",
    "gh api -X POST repos/o/r/issues",
    "gh api -X DELETE repos/o/r/x",
    "gh api repos/o/r/issues/1/comments -f body=x",
    "gh api graphql -f query='mutation{addStar(input:{}){clientMutationId}}'",
    "kubectl apply -f x.yaml",
    "kubectl delete configmap top",
    "kubectl rollout restart deploy/x && kubectl get pods",
    'git commit -m "docs: kubectl get pods"',
    "echo 'kubectl logs x'",
    "cat > f.md <<'EOF'\nkubectl get pods\nEOF",
    "git commit -m wip && gh pr list",
    "vault write secret/x a=b",
    "vault read secret/x",
    "vault kv get secret/x",
    "gcloud run deploy x",
    "gcloud services enable x",
    "gcloud --help",
    "gcloud services list --help",
    "gcloud container clusters get-credentials c --region r",
    "~/.claude/scripts/slack-cli.sh send C1 hi",
    "git status",
    "echo 'unbalanced",
    "",
]


@pytest.mark.parametrize("cmd,family", READS)
def test_read_is_classified_to_its_family(cmd, family):
    assert cd.classify_reader_call(cmd) == family


@pytest.mark.parametrize("cmd", NOT_READS)
def test_non_read_is_not_classified(cmd):
    assert cd.classify_reader_call(cmd) is None


def test_every_family_maps_to_a_reader_name_that_exists_as_a_convention():
    """pup is served by datadog-reader, so the name is looked up, never derived."""
    assert cd.READER_FOR_FAMILY["pup"] == "datadog-reader"
    assert set(cd.READER_FOR_FAMILY) == {"kubectl", "gh", "pup", "slack", "gcloud", "vault"}
    assert all(cd._READER_NAME_RE.fullmatch(v) for v in cd.READER_FOR_FAMILY.values())


def test_every_family_has_a_fallback_warning():
    assert set(cd.READER_FOR_FAMILY) <= set(cd.READER_REFLEX)
```

- [ ] **Step 2: Write the kill-switch detection rows of the behaviour tests**

Create `tests/test_reader_block.py`. Its helpers use only symbols that exist on `origin/main`,
so the same file runs in all three arms (Task 3):

```python
"""The reader-agent block, driven through the real handle_pre_tool.

Runs unchanged against origin/main, commit A and commit B (the three arms), so it may use only
symbols that exist on origin/main. Classifier internals are tested in
test_reader_classifier.py. `blocks_enabled` is deliberately NOT monkeypatched: it is part of
the behaviour under test. Only the model lookup it depends on is pinned.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
_spec = importlib.util.spec_from_file_location("cost_discipline_reader_block", MOD)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)

READERS = ("kubectl-reader", "gh-reader", "datadog-reader",
           "slack-reader", "gcloud-reader", "vault-reader")

# One bare read per family, and the reader that must be named for it.
FAMILY_READS = [
    ("kubectl get pods -n ns", "kubectl-reader"),
    ("gh pr view 5", "gh-reader"),
    ("~/.claude/skills/x/pup-ro.sh metrics query q", "datadog-reader"),
    ("~/.claude/scripts/slack-cli.sh history C123", "slack-reader"),
    ("gcloud projects list", "gcloud-reader"),
    ("vault status", "vault-reader"),
]


def _run(monkeypatch, tmp_path, cmd, *, model="opus", readers=READERS,
         agent_type=None, kill_switch=False):
    """One real handle_pre_tool call. Returns (stdout JSON objects, saved state)."""
    agents = tmp_path / "agents"
    agents.mkdir(exist_ok=True)
    for r in readers:
        (agents / f"{r}.md").write_text("---\nname: r\n---\n")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    monkeypatch.setattr(cd, "get_main_model_name", lambda: model)
    if kill_switch:
        monkeypatch.setenv("CC_DISCIPLINE_BLOCK", "0")
    else:
        monkeypatch.delenv("CC_DISCIPLINE_BLOCK", raising=False)
    base = cd.new_state("s1")
    saved = {}
    monkeypatch.setattr(cd, "load_state", lambda sid: dict(base))
    monkeypatch.setattr(cd, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": cmd}}
    if agent_type:
        payload.update(agent_type=agent_type, agent_id="a1")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cd.handle_pre_tool(payload)
    lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    return lines, saved


def _blocks(lines):
    return [o for o in lines if o.get("decision") == "block"]


def _warnings_naming(lines, reader):
    return [o for o in lines if reader in (o.get("systemMessage") or "")]


# ---- detection under the kill switch: where commit A's arm is shown to change ----

@pytest.mark.parametrize("cmd,reader", [
    ("gcloud projects list", "gcloud-reader"),
    ("vault status", "vault-reader"),
    ("cd ~/repo && gh pr list", "gh-reader"),
    ("GH_REPO=o/r gh pr list", "gh-reader"),
    ("gh -R o/r pr view 5", "gh-reader"),
    ("gh search code foo --owner o", "gh-reader"),
])
def test_kill_switch_warns_on_a_read_today_missed(monkeypatch, tmp_path, cmd, reader):
    lines, _ = _run(monkeypatch, tmp_path, cmd, kill_switch=True)
    assert not _blocks(lines), f"kill switch must never block; output={lines}"
    assert _warnings_naming(lines, reader), f"expected a {reader} warning; output={lines}"


@pytest.mark.parametrize("cmd", [
    "gh api -X POST repos/o/r/issues",
    'git commit -m "docs: kubectl get pods"',
    "kubectl delete configmap top",
])
# Each of these warns on origin/main (checked against its detector: gh api counts as a read,
# kubectl is scanned across the string). `echo 'kubectl logs x'` is NOT here: today's
# scanner sees the token `'kubectl`, so it never warned, and a row that passes on main
# would demonstrate nothing. It stays in the not-blocked rows below.
def test_kill_switch_does_not_warn_on_a_write_today_misflagged(monkeypatch, tmp_path, cmd):
    lines, _ = _run(monkeypatch, tmp_path, cmd, kill_switch=True)
    assert not any(_warnings_naming(lines, r) for r in READERS), f"output={lines}"


def test_subagent_read_gets_no_reader_output(monkeypatch, tmp_path):
    lines, _ = _run(monkeypatch, tmp_path, "gh pr view 5", agent_type="gh-reader")
    assert not _blocks(lines)
    assert not any(_warnings_naming(lines, r) for r in READERS), f"output={lines}"
```

Note: `_run` captures with `redirect_stdout` rather than `capsys` so a test can call it more
than once without the captures mixing.

- [ ] **Step 3: Run both files and confirm they fail for the right reason**

Run: `python3 -m pytest tests/test_reader_classifier.py tests/test_reader_block.py --color=no -q`
Expected: the classifier file fails with `AttributeError: … 'classify_reader_call'`. In
`test_reader_block.py`: the gcloud, vault, `cd &&`, `GH_REPO=`, `-R` and `search code` rows fail
with "expected a … warning"; the four mis-flag rows fail with a warning present; the sub-agent
row fails because today's reflex also fires for sub-agents. A crash or an import error in
`test_reader_block.py` is a wrong-reason red. Fix the test before going on.

- [ ] **Step 4: Add the import and the classifier**

In `hooks/cost-discipline.py`, add `import shlex` to the import block (keep alphabetical, after
`import re`). Then insert this section directly after `is_bash_write_command` ends (before the
`# ---- Reader-reflex table` comment):

```python
# ---- Reader-call classifier (fleet #120, 2026-09-24) ------------------------------
# One classifier for the six CLI families that have a reader agent. It feeds both the
# block in handle_pre_tool and the READER_REFLEX fallback warning, so the hook never
# carries two detectors for the same commands. It replaces four per-family checks that
# were written for a warning, where a false positive cost nothing: kubectl was scanned
# across the whole string (`git commit -m "... kubectl get ..."` counted as a read) and
# gh/pup/slack were matched on the first token only (`cd x && gh pr list` was missed).
# Command position only: split into simple commands, tokenise with shlex (a quoted
# argument is one token), skip assignments and `env`, then read the verb after the CLI's
# global flags. Every doubt resolves toward None — a miss costs a warning's worth of
# context, a false positive refuses a write. The kill switch CC_DISCIPLINE_BLOCK=0 turns
# the block back into the warning; it is documented here and in blocks_enabled, never in
# the block text.
READER_FOR_FAMILY = {
    "kubectl": "kubectl-reader", "gh": "gh-reader", "pup": "datadog-reader",
    "slack": "slack-reader", "gcloud": "gcloud-reader", "vault": "vault-reader",
}
_CLI_FAMILY = {
    "kubectl": "kubectl", "gh": "gh", "pup-ro.sh": "pup",
    "slack-cli.sh": "slack", "gcloud": "gcloud", "vault": "vault",
}
# Global flags that take a separate value, per family. `--flag=value` is one token and
# needs no entry. An unlisted value flag puts its value in the verb slot, which reads as
# a non-read and passes — the fail-open direction.
_VALUE_FLAGS = {
    "kubectl": frozenset({"-n", "--namespace", "--context", "--kubeconfig"}),
    "gh": frozenset({"-R", "--repo"}),
    "gcloud": frozenset({"--project", "--account", "--format"}),
    "vault": frozenset({"-address", "--address", "-namespace", "--namespace"}),
    "pup": frozenset(),
    "slack": frozenset(),
}
_KUBECTL_READ_VERBS = frozenset({"get", "describe", "logs", "top"})
_GH_READ_SUBJECTS = frozenset({"pr", "run", "repo", "issue", "release", "workflow"})
_GH_READ_ACTIONS = frozenset({"view", "list", "diff", "checks", "status"})
# gh api switches to POST when any of these is present.
_GH_API_WRITE_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
_SLACK_READ_SUBCOMMANDS = frozenset(
    {"history", "replies", "search", "channels", "users", "unreads"})
# From gcloud-reader.md's hard boundaries. The verb is the first positional token found in
# either set, so `gcloud container clusters get-credentials` is a write and
# `gcloud run services describe x` is a read.
_GCLOUD_READ_VERBS = frozenset({"list", "describe", "get-iam-policy"})
_GCLOUD_READ_PREFIXES = frozenset({("logging", "read"), ("config", "get-value"),
                                   ("asset", "search-all-resources")})
_GCLOUD_WRITE_VERBS = frozenset({
    "create", "delete", "deploy", "update", "set", "unset", "enable", "disable", "patch",
    "add-iam-policy-binding", "remove-iam-policy-binding", "set-iam-policy", "start",
    "stop", "reset", "resize", "import", "export", "write", "submit", "login", "revoke",
    "activate-service-account", "get-credentials", "ssh", "scp", "cp", "mv", "rm",
    "rsync", "access", "add", "remove", "execute", "run", "cancel", "rollback"})
# What vault-dev-read.sh serves. `vault read` / `vault kv get` are deliberately absent:
# the reader returns key names, never a value, so it would have to refuse them.
_VAULT_READ_PAIRS = frozenset({("secrets", "list"), ("auth", "list"), ("kv", "list")})
_SEGMENT_OPERATORS = frozenset({"&&", "||", ";", "|", "&", "|&", ";;", "\n"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _command_segments(cmd):
    """Simple commands of `cmd`, each a token list. Raises ValueError on bad quoting.
    A heredoc body is data: everything after the line holding `<<` is dropped."""
    lines = cmd.split("\n")
    for i, line in enumerate(lines):
        if "<<" in line:
            lines = lines[: i + 1]
            break
    lex = shlex.shlex("\n".join(lines), posix=True, punctuation_chars=True)
    lex.whitespace = " \t\r"   # newline stays a token, so each line is its own segment
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok in _SEGMENT_OPERATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def _command_start(seg):
    """Index of the command word: past `VAR=value` assignments and an `env` prefix."""
    i = 0
    while i < len(seg) and _ASSIGNMENT_RE.match(seg[i]):
        i += 1
    if i < len(seg) and seg[i] == "env":
        i += 1
        while i < len(seg):
            if seg[i] in ("-u", "--unset", "-C", "--chdir"):
                i += 2
            elif seg[i].startswith("-") or _ASSIGNMENT_RE.match(seg[i]):
                i += 1
            else:
                break
    return i


def _positionals(family, args):
    """Non-flag tokens of `args`, skipping the value of each value-taking flag."""
    out, i, value_flags = [], 0, _VALUE_FLAGS[family]
    while i < len(args):
        tok = args[i]
        if tok.startswith("-"):
            i += 2 if tok in value_flags else 1
            continue
        out.append(tok)
        i += 1
    return out


def _gh_api_is_read(args):
    """`gh api` is a read only as a GET. graphql is a read only without `mutation`."""
    graphql = "graphql" in args
    if graphql and any("mutation" in t.lower() for t in args):
        return False
    for i, tok in enumerate(args):
        if tok in ("-X", "--method"):
            if i + 1 >= len(args) or args[i + 1].upper() != "GET":
                return False
        elif tok.startswith("--method="):
            if tok.split("=", 1)[1].upper() != "GET":
                return False
        elif tok.startswith("-X") and len(tok) > 2 and tok[2:].upper() != "GET":
            return False
    if graphql:
        return True   # a graphql query always carries -f query=…, so fields are not a write here
    for tok in args:
        if tok in _GH_API_WRITE_FLAGS or tok.startswith(("--field=", "--raw-field=", "--input=")):
            return False
        if len(tok) > 2 and tok[:2] in ("-f", "-F"):
            return False
    return True


def _is_reader_read(family, args):
    """True iff `args` (the tokens after the CLI) are in `family`'s read set."""
    if "--help" in args or "-h" in args:
        return False
    if family == "pup":
        return True   # pup-ro.sh is read-only by construction
    pos = _positionals(family, args)
    if family == "kubectl":
        return bool(pos) and pos[0] in _KUBECTL_READ_VERBS
    if family == "gh":
        if pos[:1] == ["api"]:
            return _gh_api_is_read(args[args.index("api") + 1:])
        if pos[:2] == ["search", "code"]:
            return True
        return len(pos) >= 2 and pos[0] in _GH_READ_SUBJECTS and pos[1] in _GH_READ_ACTIONS
    if family == "slack":
        return bool(pos) and pos[0] in _SLACK_READ_SUBCOMMANDS
    if family == "gcloud":
        if tuple(pos[:2]) in _GCLOUD_READ_PREFIXES:
            return True
        for tok in pos:
            if tok in _GCLOUD_READ_VERBS:
                return True
            if tok in _GCLOUD_WRITE_VERBS:
                return False
        return False
    if family == "vault":
        return pos[:1] in (["status"], ["list"]) or tuple(pos[:2]) in _VAULT_READ_PAIRS
    return False


def classify_reader_call(cmd):
    """The family (a READER_FOR_FAMILY key) whose reader should serve `cmd`, or None.

    A family is returned only if some simple command is a read of it AND no simple command
    in the line is a write: one of the six CLIs outside its read set, or a known write per
    is_bash_write_command. `kubectl rollout restart x && kubectl get pods` is None —
    splitting a mixed line into a dispatch plus an inline write is the caller's call. A
    CLI that only appears inside a quoted argument or a heredoc body is never a command."""
    try:
        segments = list(_command_segments(cmd or ""))
    except ValueError:
        return None   # unbalanced quoting: fail open
    found = None
    for seg in segments:
        i = _command_start(seg)
        if i >= len(seg) or seg[i] == "cd":
            continue
        if is_bash_write_command(" ".join(seg[i:])):
            return None
        family = _CLI_FAMILY.get(os.path.basename(seg[i]))
        if family is None:
            continue
        if not _is_reader_read(family, seg[i + 1:]):
            return None
        found = found or family
    return found
```

- [ ] **Step 5: Add the gcloud and vault fallback warnings**

In `READER_REFLEX`, after the `"gh"` entry and before the closing `}`, add:

```python
    "gcloud": (
        "🛑 STOP — dispatch gcloud-reader instead of running gcloud inline. "
        "gcloud list/describe/get-iam-policy, logging read and config get-value belong in "
        "the gcloud-reader Haiku sub-agent (summarizes by design). "
        "Dispatch: `Agent(subagent_type='gcloud-reader', model='haiku', "
        "prompt='RAW: gcloud <group> <verb> ...')`.",
        "🚨 Third inline gcloud read this session on an expensive main. "
        "Agent(subagent_type='gcloud-reader', model='haiku', ...)",
    ),
    "vault": (
        "🛑 STOP — dispatch vault-reader instead of running vault inline. "
        "vault status and secrets/auth/kv listings belong in the vault-reader Haiku "
        "sub-agent, which returns key names only. "
        "Dispatch: `Agent(subagent_type='vault-reader', model='haiku', "
        "prompt='RAW: vault <verb> ...')`. "
        "vault-reader is DEV-only; if this is prod it will refuse — ask the operator instead.",
        "🚨 Third inline vault read this session on an expensive main. "
        "Agent(subagent_type='vault-reader', model='haiku', ...)",
    ),
```

Also update the comment above `READER_REFLEX` from "(kubectl / pup / slack / gh)" and "add a
reader by adding an entry here + one detection branch in handle_pre_tool" to say detection is
`classify_reader_call` and a new family needs an entry here, in `READER_FOR_FAMILY` and in
`_CLI_FAMILY`.

- [ ] **Step 6: Replace the old detection block**

In `handle_pre_tool`, replace everything from `if tool_name == "Bash":` / `cmd_full = …` at the
start of the "Reader-agent nudges" section (`~:3463`) through the end of the reflex chain
(`fire_reader_reflex(state, "gh")`, `~:3569`) with:

```python
    if tool_name == "Bash":
        cmd_full = tool_input.get("command") or ""
        _family = classify_reader_call(cmd_full)

        # Wiki-first precondition: incident-investigation tools should be preceded by a
        # wiki grep. Kept to the four families it has always covered (gcloud and vault
        # joined the classifier on 2026-09-24, not this nudge). Self-disables once
        # wiki_read_count > 0.
        if _family in ("kubectl", "pup", "slack", "gh") and state.get("wiki_read_count", 0) == 0:
            fire_once(state, "wiki_first",
                <the existing wiki_first message, unchanged>)

        # Fallback warning. A read the block did not refuse lands here: kill switch off,
        # Haiku main, or the family's reader not installed. Sub-agent calls pass silently —
        # the reader itself runs these commands.
        if _family and not is_subagent_call(payload):
            fire_reader_reflex(state, _family)
```

Keep the section's header comment, rewritten to say detection is `classify_reader_call`.
Copy the `wiki_first` message string from the removed block verbatim.

- [ ] **Step 7: Run the tests**

Run: `python3 -m pytest tests/test_reader_classifier.py tests/test_reader_block.py --color=no -q`
Expected: all pass.
Run: `python3 -m pytest --color=no -q`
Expected: the Task 0 baseline counts, plus the new tests. If an existing test asserted the old
detector's behaviour (for example, a gh warning on `gh api -X POST`), read it before changing
it. It may be a guard the spec overlooked.

- [ ] **Step 8: Commit A**

Two of the three paths are new, so write
`~/.claude/jobs/4ca1e8fd/tmp/commit-a.sh`:

```sh
#!/bin/sh
# Commit A adds two new test files, so `git commit <path>` cannot be used: add the exact paths, then commit.
set -e
cd ~/dev/claude-core/.worktrees/reader-block
git add hooks/cost-discipline.py tests/test_reader_classifier.py tests/test_reader_block.py
git diff --cached --stat
git commit -q -m "fix(hooks): classify reader calls by command position" -m "One classifier for the six CLI families that have a reader agent replaces four per-family detectors. kubectl was scanned across the whole command string, so a commit message mentioning kubectl get counted as a read; gh, pup and slack were matched on the first token only, so cd x && gh pr list was missed. gcloud and vault are detected for the first time. Still warn-only; sub-agent calls no longer get the reader warning."
git log -1 --format='%h %s'
git status --short
```

Run: `sh ~/.claude/jobs/4ca1e8fd/tmp/commit-a.sh`. Read the `--stat` output: it should be
exactly these three files. Record the sha as **A**.

**Definition of done:** both new files are green; the full suite matches the baseline plus the
new tests; commit A exists and touches exactly three files. Park per the header rule.

---

### Task 2: Commit B — the block

**Files:**
- Modify: `hooks/cost-discipline.py` (the `Bash` branch at `~:3229`; one helper next to
  `classify_reader_call`)
- Modify: `tests/test_reader_block.py`

**Interfaces:**
- Consumes: `classify_reader_call`, `READER_FOR_FAMILY` (Task 1); `blocks_enabled`,
  `reader_roster`, `emit_block`, `save_state`, `log_fire` (existing).
- Produces: `reader_block_reason(family: str, cmd: str) -> str`.

- [ ] **Step 1: Append the block rows and the smoke test**

Append to `tests/test_reader_block.py`:

```python
# ---- the block ----

@pytest.mark.parametrize("cmd,reader", FAMILY_READS)
def test_main_read_is_blocked_naming_only_its_reader(monkeypatch, tmp_path, cmd, reader):
    lines, _ = _run(monkeypatch, tmp_path, cmd)
    assert len(lines) == 1, f"exactly one JSON object on stdout; output={lines}"
    assert lines[0].get("decision") == "block", f"output={lines}"
    reason = lines[0]["reason"]
    assert reader in reason
    assert not [r for r in READERS if r != reader and r in reason]
    assert "CC_DISCIPLINE_BLOCK" not in reason


@pytest.mark.parametrize("cmd", [
    "gh api repos/o/r/pulls",
    "gh api graphql -f query='{ viewer { login } }'",
    "kubectl --context=x -n ns get pods",
    "cd ~/repo && gh pr list",
    "GH_REPO=o/r gh pr list",
    "gh -R o/r pr view 5",
    "gh --repo o/r pr list",
    "gh search code foo --owner o",
    "gcloud config get-value project",
    "gcloud logging logs list",
    "gcloud services list",
    "gcloud projects describe p",
])
def test_read_forms_are_blocked(monkeypatch, tmp_path, cmd):
    lines, _ = _run(monkeypatch, tmp_path, cmd)
    assert _blocks(lines), f"output={lines}"


@pytest.mark.parametrize("cmd", [
    "gh pr create --title x", "kubectl apply -f x.yaml", "vault write secret/x a=b",
    "gcloud run deploy x", "gcloud services enable x", "gcloud --help",
    "~/.claude/scripts/slack-cli.sh send C1 hi",
    "gh api -X POST repos/o/r/issues", "gh api -X DELETE repos/o/r/x",
    "gh api repos/o/r/issues/1/comments -f body=x",
    "gh api graphql -f query='mutation{addStar(input:{}){clientMutationId}}'",
    'git commit -m "docs: kubectl get pods"', "echo 'kubectl logs x'",
    "cat > f.md <<'EOF'\nkubectl get pods\nEOF",
    "kubectl delete configmap top",
    "kubectl rollout restart deploy/x && kubectl get pods",
    "git commit -m wip && gh pr list",
    "vault read secret/x", "vault kv get secret/x",
])
def test_writes_and_mentions_are_not_blocked(monkeypatch, tmp_path, cmd):
    lines, _ = _run(monkeypatch, tmp_path, cmd)
    assert not _blocks(lines), f"output={lines}"


@pytest.mark.parametrize("cmd,reader", FAMILY_READS)
def test_haiku_main_is_not_blocked(monkeypatch, tmp_path, cmd, reader):
    lines, _ = _run(monkeypatch, tmp_path, cmd, model="haiku")
    assert not _blocks(lines), f"output={lines}"


@pytest.mark.parametrize("cmd,reader", FAMILY_READS)
def test_subagent_call_gets_no_reader_output(monkeypatch, tmp_path, cmd, reader):
    lines, _ = _run(monkeypatch, tmp_path, cmd, agent_type=reader)
    assert not _blocks(lines)
    assert not any(_warnings_naming(lines, r) for r in READERS), f"output={lines}"


@pytest.mark.parametrize("cmd,reader", FAMILY_READS)
def test_absent_reader_is_not_blocked_toward(monkeypatch, tmp_path, cmd, reader):
    lines, _ = _run(monkeypatch, tmp_path, cmd,
                    readers=[r for r in READERS if r != reader])
    assert not _blocks(lines), f"output={lines}"


@pytest.mark.parametrize("cmd,reader", FAMILY_READS)
def test_kill_switch_never_blocks(monkeypatch, tmp_path, cmd, reader):
    lines, _ = _run(monkeypatch, tmp_path, cmd, kill_switch=True)
    assert not _blocks(lines), f"output={lines}"
    assert _warnings_naming(lines, reader), f"falls back to the warning; output={lines}"


@pytest.mark.parametrize("cmd,reader", FAMILY_READS)
def test_blocked_call_does_not_move_the_read_counters(monkeypatch, tmp_path, cmd, reader):
    lines, saved = _run(monkeypatch, tmp_path, cmd)
    assert _blocks(lines), f"output={lines}"
    assert saved.get("aggregate_reads", 0) == 0
    main = (saved.get("agent_counters") or {}).get("main") or {}
    assert main.get("read_streak", 0) == 0
    assert main.get("agent_reads", 0) == 0


def test_vault_block_carries_the_dev_only_line(monkeypatch, tmp_path):
    lines, _ = _run(monkeypatch, tmp_path, "vault status")
    assert "vault-reader is DEV-only" in _blocks(lines)[0]["reason"]


def test_real_process_emits_exactly_one_block_line(tmp_path):
    """The argv/stdin/stdout path the in-process layer cannot see: the real script, run the
    way the harness runs it, with HOME pointed at a temp dir so HARNESS_DIR, LOG_FILE and the
    settings.json model lookup all resolve inside it. STATE_DIR is hard-coded /tmp, so the
    session id is unique and every /tmp file this run writes is removed afterwards."""
    home = tmp_path / "home"
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "gh-reader.md").write_text("---\nname: gh-reader\n---\n")
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": "opus"}))
    sid = f"test-reader-block-{uuid.uuid4().hex}"
    env = {k: v for k, v in os.environ.items() if k != "CC_DISCIPLINE_BLOCK"}
    env["HOME"] = str(home)
    payload = {"session_id": sid, "tool_name": "Bash",
               "tool_input": {"command": "gh pr list"}}
    proc = subprocess.Popen([sys.executable, str(MOD), "pre-tool"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        out, err = proc.communicate(json.dumps(payload), timeout=30)
    finally:
        for p in (Path(f"/tmp/cc-discipline-{sid}.json"),
                  Path(f"/tmp/cc-session-by-pid-{os.getpid()}.txt"),
                  Path(f"/tmp/cc-session-mode-by-pid-{os.getpid()}.txt")):
            p.unlink(missing_ok=True)
    assert proc.returncode == 0, err
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout={out!r} stderr={err!r}"
    obj = json.loads(lines[0])
    assert obj["decision"] == "block" and "gh-reader" in obj["reason"]
```

The pid markers are keyed on the child's parent pid, which is this pytest process. That is why
the cleanup uses `os.getpid()`. Nothing belonging to a live Claude session is touched.

- [ ] **Step 2: Run and confirm red for the right reason**

Run: `python3 -m pytest tests/test_reader_block.py --color=no -q`
Expected: every block row fails with "decision" missing, and a warning is present in `output=`.
The counters row fails at `assert _blocks(lines)`. Rows that assert "not blocked" already pass;
they guard B.

- [ ] **Step 3: Add the reason helper**

Directly after `classify_reader_call`, add:

```python
def reader_block_reason(family, cmd):
    """Block text for a reader-family read: names only that family's reader and the call.
    Never mentions the kill switch (see blocks_enabled)."""
    reader = READER_FOR_FAMILY[family]
    call = " ".join(cmd.split())[:160]
    reason = (
        f"🛑 This is {reader} work — dispatch it instead of running it inline: "
        f"Agent(subagent_type='{reader}', model='haiku', prompt='RAW: {call}'). "
        "The reader runs the same read and returns a summary, so the raw output never "
        "lands in this context. Only this read was refused, and it was not counted.")
    if family == "vault":
        reason += (" vault-reader is DEV-only; if this is prod it will refuse — "
                   "ask the operator instead.")
    return reason
```

- [ ] **Step 4: Add the block to the `Bash` branch**

In `handle_pre_tool`, inside `if tool_name == "Bash":` at the top (`~:3229`), directly after the
cat-as-read block's `return` and before `if is_ls_find_as_glob(_bcmd):`, add:

```python
        # Reader-agent block (fleet #120). Placed BEFORE the counting block on purpose:
        # the counters commit before the old reflex chain is reached, so a block there
        # would ratchet the streak on a refused call — a defect this hook has had once.
        # Returning here also keeps the fallback warning off stdout: one JSON object.
        _reader_family = classify_reader_call(_bcmd)
        if (_reader_family and blocks_enabled(payload)
                and READER_FOR_FAMILY[_reader_family] in reader_roster()[0]):
            save_state(state)
            log_fire("block_reader_call", session_id, "block",
                     family=_reader_family, command=_bcmd[:120])
            emit_block(reader_block_reason(_reader_family, _bcmd))
            return
```

`_bcmd` is `.strip()`-ed there; that is fine for the classifier.

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest tests/test_reader_block.py tests/test_reader_classifier.py --color=no -q`
Expected: all pass.
Run: `python3 -m pytest --color=no -q`
Expected: the baseline plus the new tests, all green. Watch `test_read_block_tier.py` and
`test_agent_scoped_counters.py`: they drive `Bash` calls, and a command in them that the
classifier now reads would change what they see.

- [ ] **Step 6: Lint**

Run: `python3 -m ruff check tests/test_reader_block.py tests/test_reader_classifier.py`
Expected: clean.
Run: `python3 -m ruff check hooks/cost-discipline.py --statistics`
Expected: the Task 0 count, or fewer. Any new finding is in the lines this plan wrote. Fix it.

- [ ] **Step 7: Commit B** (both paths are tracked now)

Run: `~/.claude/scripts/git-at ~/dev/claude-core/.worktrees/reader-block commit hooks/cost-discipline.py tests/test_reader_block.py -m "feat(hooks): block inline reads that have a reader agent" -m "A main-session Bash read in one of six families (kubectl, gh, pup, slack, gcloud, vault) whose reader agent is installed is refused from the first call, with a reason naming that reader. The block sits before the read counters, so a refused call does not move the streak or the aggregate. Gated on blocks_enabled: the kill switch, sub-agents and a Haiku main fall back to the warning."`
Record the sha as **B**.

**Definition of done:** all new rows green, including the subprocess smoke test; the full
suite is green; ruff has no new findings; commit B touches exactly two files. Park per the
header rule.

---

### Task 3: Three arms

**Files:** none in the repo. Scratch under `~/.claude/jobs/4ca1e8fd/tmp/arms/`.

- [ ] **Step 1: Write the arm runner**

Write `~/.claude/jobs/4ca1e8fd/tmp/run-arm.sh`:

```sh
#!/bin/sh
# Run the FINISHED tests/test_reader_block.py against one arm's hook.
# usage: run-arm.sh <name> <git-ref>
set -u
WT=~/dev/claude-core/.worktrees/reader-block
D=~/.claude/jobs/4ca1e8fd/tmp/arms/$1
rm -rf "$D"
mkdir -p "$D/hooks" "$D/tests"
git -C "$WT" show "$2:hooks/cost-discipline.py" > "$D/hooks/cost-discipline.py"
cp "$WT/tests/test_reader_block.py" "$D/tests/"
cd "$D"
env -u FORCE_COLOR -u PY_COLORS python3 -m pytest tests/test_reader_block.py --color=no -q -rA -p no:cacheprovider > result.txt 2>&1
echo "arm=$1 ref=$2 exit=$?"
grep -cE '^PASSED' result.txt
grep -cE '^FAILED' result.txt
grep -cE '^ERROR' result.txt
```

- [ ] **Step 2: Run the three arms** (fetch first, so `origin/main` is today's)

Run: `~/.claude/scripts/git-at ~/dev/claude-core/.worktrees/reader-block fetch origin`
Run: `sh ~/.claude/jobs/4ca1e8fd/tmp/run-arm.sh main origin/main`
Run: `sh ~/.claude/jobs/4ca1e8fd/tmp/run-arm.sh a <sha A>`
Run: `sh ~/.claude/jobs/4ca1e8fd/tmp/run-arm.sh b <sha B>`

- [ ] **Step 3: Check each arm against the spec's expectations**

Read each `result.txt`. Expected:

| rows | `origin/main` | A | B |
|---|---|---|---|
| block rows, bare kubectl/gh/pup/slack | FAIL, `output=` shows a warning, no `decision` | FAIL, warning | PASS |
| block rows, gcloud and vault | FAIL, `output=[]` or no reader warning | FAIL, warning | PASS |
| block rows, `cd &&`, `GH_REPO=`, `-R`/`--repo`, `search code` | FAIL, no reader warning | FAIL, warning | PASS |
| kill-switch warns on missed reads | FAIL | PASS | PASS |
| kill-switch does not warn on mis-flags | FAIL | PASS | PASS |
| sub-agent gets no reader output | FAIL (today's reflex fires) | PASS | PASS |
| not-blocked rows (writes, Haiku, absent reader) | PASS | PASS | PASS |
| subprocess smoke | FAIL, no `decision` | FAIL | PASS |

Any `ERROR`, or a failure whose message is an import error or a traceback, is a wrong-reason red
and invalidates that arm. Report the rows that pass in all three arms separately: they guard
the change and demonstrate nothing about the defect.

- [ ] **Step 4: Record the result**

Write the per-arm counts and every row that differs from the table above to
`~/.claude/jobs/4ca1e8fd/tmp/arms/summary.md`. It becomes the PR body's "Three arms" section.

**Definition of done:** three arms ran on fresh refs; every arm's reds are for the stated
reason; the summary is on disk. Park per the header rule.

---

### Task 4: Independent review

- [ ] **Step 1: Dispatch the reviewer**

`Agent(subagent_type="feature-dev:code-reviewer", model="sonnet", …)`. The brief opens with the
WRITE BOUNDARY block, verbatim:

```
[WRITE BOUNDARY] Sub-agent rules: read/edit only in your worktree. No issue-tracker
tickets, no GitHub PRs/issues, no `git push`, no chat posts. Report findings only; the
main agent performs all side-effect writes. For this review you are READ-ONLY.
```

It then carries the execution-constraints block from the global CLAUDE.md verbatim, and then:
- the diff to review: `origin/main...feat/reader-agent-block` (three dots) in
  `~/dev/claude-core/.worktrees/reader-block`, and the spec path
- focus: a write the classifier reads as a read (it would be refused); a read it misses; stdout
  carrying more than one JSON object; counters moving on a refused call; any path where an
  exception escapes `handle_pre_tool`; the block text naming another reader or the kill switch
- a failure scenario (input → wrong output) for every finding, at most 8, ranked
- a `NOT SETTLED` section with both halves

- [ ] **Step 2: Reproduce every finding by execution**

The reviewer cannot run code (it has `BashOutput` but not `Bash`). For each finding, run the
input through `classify_reader_call` or through `_run`-style code in a scratch script under
`~/.claude/jobs/4ca1e8fd/tmp/`, and mark it reproduced or refuted with the output.

- [ ] **Step 3: Fix what reproduced**

For each reproduced finding: add a failing row to the right test file first, then fix, then
run the full suite. Commit as `fix(hooks): <finding>` with a
`git-at … commit <paths> -m …`. Re-run Task 3's arm **b** at the new tip.

**Definition of done:** every finding is marked reproduced or refuted with evidence; the fixes
are committed with their tests; the full suite is green. Park per the header rule.

---

### Task 5: Push and PR (needs the operator)

- [ ] **Step 1: Push the branch**

Run: `~/.claude/scripts/git-at ~/dev/claude-core/.worktrees/reader-block push -u origin feat/reader-agent-block`

- [ ] **Step 2: Compose the PR body and show it in full**

Write it to `~/.claude/jobs/4ca1e8fd/tmp/pr-body.md` with these sections: Summary (what is
blocked, from the first call; the Haiku exemption); Detection changes (commit A; the seven
narrowings above); Three arms (from `summary.md`); Review (reviewer name, each finding and
whether it was reproduced or refuted); Rollout (plugin update and restart, the operator's
call); Definition of done (the live proof is still owed after merge). No attribution line.

Show the whole body to the operator and stop. Report READY TO SEND. `gh pr create` needs
`! ~/.claude/scripts/publish-grant github` from the operator and their go on this exact body.

- [ ] **Step 3: After the go**

Run: `gh pr create --base main --head feat/reader-agent-block --title "feat(hooks): block inline reads that have a reader agent" --body-file ~/.claude/jobs/4ca1e8fd/tmp/pr-body.md`
Then read back `gh pr view <N> --json state,baseRefName,headRefName,files,additions,body,headRefOid,statusCheckRollup`
and confirm that the body carries no attribution and that the rollup at `headRefOid` is green.

**Definition of done:** the PR is open; it was verified by reading it back, not from the create
receipt. Park per the header rule. The operator merges.

---

### Task 6: Rollout and live proof (after the merge; operator's decision)

- [ ] **Step 1:** The operator runs `claude plugin update claude-core-hooks@claude-core-local`
  and restarts the session. Installed was v0.18.2 against repo v0.19.1 on 2026-09-24, so the
  update also brings in unrelated changes already on main.
- [ ] **Step 2:** In the restarted main session (not a sub-agent, main model not Haiku), run
  `gh pr list` once.
  Expected: a tool error whose text starts `🛑 This is gh-reader work`.
- [ ] **Step 3:** Confirm the delivery from the transcript, not from the hook log. The error
  must be a `tool_result` with `is_error: true` whose content carries that text.
- [ ] **Step 4:** Close fleet #120 only now. If the operator defers the update, the row reads
  "merged, not installed" and stays open.
- [ ] **Step 5:** Clean up: remove the worktree and delete the local branch after the merge,
  checking by content (squash merge): `git-at ~/dev/claude-core ls-tree -l origin/main -- tests/test_reader_block.py`.
