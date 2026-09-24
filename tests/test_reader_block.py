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
    "kubectl get pods -n x\nkubectl delete pod y -n x",
    "gh api repos/o/r/issues \\\n  -f title=x",
    "gh api repos/o/r/issues \\\n  -X POST",
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
    assert _blocks(lines), f"output={lines}"   # an assertion, not an IndexError, on main and A
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
    assert obj.get("decision") == "block", f"stdout={out!r}"   # not a KeyError on main and A
    assert "gh-reader" in obj["reason"]
