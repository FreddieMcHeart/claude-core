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
