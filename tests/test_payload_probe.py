"""The temporary payload probe: it must record shape and must not record data.

The probe exists to answer one question — which key, if any, distinguishes an
Agent-tool sub-agent's hook invocation from a foreground main's. Two earlier
probes established there is no such variable in the environment a *Bash tool
command* sees; the hook is a different process with a different environment and
a payload nobody has inspected.

These tests are weighted toward the redaction properties rather than the
recording ones. A probe that fails to record something is a wasted run; a probe
that records a value out of `tool_input` has turned a one-off diagnostic into a
file full of file contents and command lines sitting in /tmp. The asymmetry is
the point, and it is why the leak assertions check the RAW serialised line
rather than the parsed structure — a value that escapes into an unexpected key
still fails the test.

Delete this file when the probe block is deleted.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_probe", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

SECRET = "SUPERSECRET-DO-NOT-LEAK"


def _isolated(monkeypatch, tmp_path):
    """Both switch-on routes forced OFF, so a real marker file on this machine
    cannot make a test pass (or fail) for reasons outside the test."""
    monkeypatch.delenv("CC_PAYLOAD_PROBE", raising=False)
    monkeypatch.setattr(cd, "PAYLOAD_PROBE_MARKER", tmp_path / "no-such-marker")


def _armed_via_env(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    target = tmp_path / "probe.jsonl"
    monkeypatch.setenv("CC_PAYLOAD_PROBE", str(target))
    return target


def _lines(target):
    return [json.loads(ln) for ln in target.read_text().splitlines() if ln.strip()]


# ---------------- switched off by default ----------------

def test_off_by_default_writes_nothing(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {"tool_name": "Read"})
    assert list(tmp_path.iterdir()) == []


def test_empty_env_value_is_off_not_a_path(monkeypatch, tmp_path):
    """An exported-but-blank variable is the classic accidental enable."""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("CC_PAYLOAD_PROBE", "   ")
    cd.probe_payload("pre-tool", {"tool_name": "Read"})
    assert list(tmp_path.iterdir()) == []


# ---------------- the two switch-on routes ----------------

def test_env_route_appends_one_record_per_call(monkeypatch, tmp_path):
    target = _armed_via_env(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {"tool_name": "Read"})
    cd.probe_payload("post-tool", {"tool_name": "Read"})
    records = _lines(target)
    assert [r["mode"] for r in records] == ["pre-tool", "post-tool"]


def test_marker_file_route_works_without_the_env_var(monkeypatch, tmp_path):
    """The mid-session route. A hook inherits its environment when Claude Code
    launches, so an `export` from inside a running session never reaches it —
    and a session already running is exactly the one worth probing."""
    _isolated(monkeypatch, tmp_path)
    target = tmp_path / "probe.jsonl"
    marker = tmp_path / "marker"
    marker.write_text(f"{target}\n")
    monkeypatch.setattr(cd, "PAYLOAD_PROBE_MARKER", marker)
    cd.probe_payload("pre-tool", {"tool_name": "Read"})
    assert len(_lines(target)) == 1


def test_env_var_wins_over_marker_file(monkeypatch, tmp_path):
    env_target = _armed_via_env(monkeypatch, tmp_path)
    marker_target = tmp_path / "from-marker.jsonl"
    marker = tmp_path / "marker"
    marker.write_text(str(marker_target))
    monkeypatch.setattr(cd, "PAYLOAD_PROBE_MARKER", marker)
    cd.probe_payload("pre-tool", {})
    assert len(_lines(env_target)) == 1
    assert not marker_target.exists()


# ---------------- redaction: the load-bearing half ----------------

def test_tool_input_values_are_never_written(monkeypatch, tmp_path):
    target = _armed_via_env(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {
        "tool_name": "Bash",
        "tool_input": {"command": f"curl -H 'Authorization: {SECRET}'"},
    })
    raw = target.read_text()
    assert SECRET not in raw
    # The SHAPE still has to survive — a redaction that also drops the key path
    # would make the probe useless for the question it was written to answer.
    shape = _lines(target)[0]["shape"]
    assert shape["tool_input.command"] == "str"
    assert shape["tool_name"] == "str=Bash"


def test_redaction_latches_through_nesting(monkeypatch, tmp_path):
    """A value must not escape by sitting deeper than the opaque subtree root."""
    target = _armed_via_env(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {
        "tool_input": {"edits": [{"new_string": SECRET}]},
    })
    raw = target.read_text()
    assert SECRET not in raw
    assert _lines(target)[0]["shape"]["tool_input.edits[0].new_string"] == "str"


def test_secrety_key_names_are_typed_not_valued(monkeypatch, tmp_path):
    """Outside the opaque subtrees the probe does record values, so the key-name
    denylist is the second line of defence rather than a decoration."""
    target = _armed_via_env(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {"api_token": SECRET, "cwd": "/Users/x/dev"})
    raw = target.read_text()
    assert SECRET not in raw
    shape = _lines(target)[0]["shape"]
    assert shape["api_token"] == "str"
    assert shape["cwd"] == "str=/Users/x/dev"


def test_long_values_are_truncated(monkeypatch, tmp_path):
    target = _armed_via_env(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {"transcript_path": "/p/" + "a" * 500})
    value = _lines(target)[0]["shape"]["transcript_path"]
    assert len(value) < 200 and value.endswith("…")


def test_environment_contributes_names_but_never_values(monkeypatch, tmp_path):
    """The reason to record env at all is that the hook's environment is a
    population nobody has inspected. Names are enough to find a distinguishing
    variable, and values are what would make the file dangerous."""
    target = _armed_via_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PROBE_FIXTURE_VAR", SECRET)
    cd.probe_payload("pre-tool", {})
    raw = target.read_text()
    assert SECRET not in raw
    assert "PROBE_FIXTURE_VAR" in _lines(target)[0]["env_names"]


def test_env_names_are_not_filtered_to_a_claude_prefix(monkeypatch, tmp_path):
    """Filtering to where the answer is expected is how the previous two probes
    missed. Pinned so a later 'tidy-up' does not reintroduce the filter."""
    target = _armed_via_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ZZ_UNPREFIXED_FIXTURE", "1")
    cd.probe_payload("pre-tool", {})
    assert "ZZ_UNPREFIXED_FIXTURE" in _lines(target)[0]["env_names"]


# ---------------- bounds, and reporting them ----------------

def test_key_cap_is_reported_rather_than_silent(monkeypatch, tmp_path):
    target = _armed_via_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cd, "PAYLOAD_PROBE_MAX_KEYS", 5)
    cd.probe_payload("pre-tool", {f"k{i}": i for i in range(50)})
    record = _lines(target)[0]
    assert record["truncated"] is True
    assert len(record["shape"]) <= 5


def test_deep_nesting_terminates(monkeypatch, tmp_path):
    target = _armed_via_env(monkeypatch, tmp_path)
    node = {"leaf": 1}
    for _ in range(60):
        node = {"n": node}
    cd.probe_payload("pre-tool", node)
    assert len(_lines(target)) == 1


# ---------------- a probe must never break the hook ----------------

def test_unwritable_target_does_not_raise(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("CC_PAYLOAD_PROBE", str(tmp_path / "no" / "such" / "dir" / "p.jsonl"))
    cd.probe_payload("pre-tool", {"tool_name": "Read"})   # must not raise


def test_unserialisable_payload_does_not_raise(monkeypatch, tmp_path):
    target = _armed_via_env(monkeypatch, tmp_path)
    cd.probe_payload("pre-tool", {"weird": object()})
    assert len(_lines(target)) == 1


# ---------------- wire-up ----------------

def test_probe_runs_for_every_mode_including_unparsable_payloads(monkeypatch):
    """The call sits before the dispatch on purpose: a record for a payload that
    failed to parse is still evidence, and the modes main() ignores are still
    invocations whose environment we want."""
    seen = []
    monkeypatch.setattr(cd, "probe_payload", lambda mode, payload: seen.append((mode, payload)))
    monkeypatch.setattr(sys, "argv", ["cost-discipline.py", "not-a-real-mode"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{ this is not json"))
    cd.main()
    assert seen == [("not-a-real-mode", {})]


def test_probe_receives_the_parsed_payload(monkeypatch):
    seen = []
    monkeypatch.setattr(cd, "probe_payload", lambda mode, payload: seen.append((mode, payload)))
    monkeypatch.setattr(cd, "handle_pre_tool", lambda payload: None)
    monkeypatch.setattr(sys, "argv", ["cost-discipline.py", "pre-tool"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"tool_name": "Read"}'))
    cd.main()
    assert seen == [("pre-tool", {"tool_name": "Read"})]
