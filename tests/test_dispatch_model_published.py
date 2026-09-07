"""The model a sub-agent was dispatched on, published for the status line.

WHY THIS EXISTS. Nothing on screen says which model a sub-agent is running.
The session header and the HUD identity row both render the SESSION's model, and
the running-agent line carries name, task, elapsed and tokens with no model field
at all. Sub-agent turns are not written to the dispatcher's transcript either —
measured 2026-09-07 on a live session: five running sub-agents, zero `isSidechain`
turns in the dispatcher's own file. So the status line has no source for this and
cannot acquire one without the hook recording it here.

WHAT IT CAN HONESTLY SAY: the model the dispatch ASKED FOR. The model that
actually ran is in the sub-agent's own task-output file and is not reachable from
a PreToolUse hook. The two agreed on every dispatch measured that day (829
sub-agent turns, sonnet + haiku, zero opus), but agreement is not identity and the
field is named for what it holds.

The load-bearing case is `test_a_frontmatter_inferred_model_is_recorded`: a
dispatch may legitimately carry no `model:` when its agent file declares one, and
recording only the explicit parameter would render 20 of 152 real dispatches as
blank — displaying "no model" for agents whose model is set, just set elsewhere.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_dispatch_model", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

FRONTMATTER = {"jira-reader": "haiku", "gh-reader": "haiku"}


def _state(monkeypatch, **fields):
    base = cd.new_state("s1")
    base["agent_models"] = dict(FRONTMATTER)
    base.update(fields)
    saved = {}

    monkeypatch.setattr(cd, "load_state", lambda sid: dict(base))
    monkeypatch.setattr(cd, "save_state", lambda st: (saved.clear(), saved.update(st),
                                                      base.clear(), base.update(st)))
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    # Pin the map so a test never depends on which agent files happen to exist on
    # the machine running it — the defect this whole area keeps producing.
    monkeypatch.setattr(cd, "scan_agent_models", lambda: dict(FRONTMATTER))
    monkeypatch.setattr(cd, "blocks_enabled", lambda payload: True)
    return saved


def _dispatch(capsys, subagent_type, model=None, description="d"):
    tool_input = {"subagent_type": subagent_type, "description": description}
    if model is not None:
        tool_input["model"] = model
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Agent",
                        "tool_input": tool_input})
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


# ---------------- the model is recorded, however it was chosen ----------------

def test_an_explicit_model_is_recorded_against_the_dispatch(monkeypatch, capsys):
    saved = _state(monkeypatch)
    _dispatch(capsys, "general-purpose", model="sonnet")
    assert saved["dispatches_by_model"] == {"sonnet": 1}


def test_a_frontmatter_inferred_model_is_recorded(monkeypatch, capsys):
    """No `model:` in the call, but the agent file declares one. The dispatch is
    allowed, so the row must show haiku — not a blank that reads as "unset"."""
    saved = _state(monkeypatch)
    _dispatch(capsys, "jira-reader")
    assert saved["dispatches_by_model"] == {"haiku": 1}


def test_counts_accumulate_across_dispatches_and_models(monkeypatch, capsys):
    saved = _state(monkeypatch)
    _dispatch(capsys, "general-purpose", model="sonnet")
    _dispatch(capsys, "general-purpose", model="sonnet")
    _dispatch(capsys, "jira-reader")
    assert saved["dispatches_by_model"] == {"sonnet": 2, "haiku": 1}


# ---------------- a refused dispatch must not move a counter ----------------

def test_a_blocked_dispatch_records_no_model(monkeypatch, capsys):
    """`general-purpose` has no frontmatter default, so a dispatch without an
    explicit model is REFUSED. A counter that advanced here would measure work
    that never happened — the ratchet defect the read-block tier was repaired
    for, and the reason the ledger's own counting sits after the block's
    `return`."""
    saved = _state(monkeypatch)
    _dispatch(capsys, "general-purpose")          # no model -> blocked
    assert saved.get("dispatches_by_model", {}) == {}
    assert saved.get("dispatches_by_type", {}) == {}, (
        "the existing type counter must not move either; if it does, this test is "
        "reporting a pre-existing defect rather than one this change introduced"
    )


# ---------------- the last dispatch, for a one-glance row ----------------

def test_the_last_dispatch_names_both_the_type_and_the_model(monkeypatch, capsys):
    saved = _state(monkeypatch)
    _dispatch(capsys, "jira-reader")
    _dispatch(capsys, "general-purpose", model="sonnet")
    assert saved["last_dispatch"] == {"subagent_type": "general-purpose",
                                      "model": "sonnet"}


# ---------------- it has to survive to the file the HUD reads ----------------

def test_the_fields_reach_disk_because_the_hud_reads_the_file(tmp_path, monkeypatch):
    """Asserts against the FILE, not the in-memory dict. A field mutated in memory
    and lost on serialisation satisfies every in-memory assertion above and still
    leaves the row empty."""
    monkeypatch.setattr(cd, "STATE_DIR", tmp_path)
    st = cd.new_state("s2")
    st["dispatches_by_model"] = {"sonnet": 3}
    st["last_dispatch"] = {"subagent_type": "general-purpose", "model": "sonnet"}
    cd.save_state(st)
    on_disk = json.loads((tmp_path / "cc-discipline-s2.json").read_text())
    assert on_disk["dispatches_by_model"] == {"sonnet": 3}
    assert on_disk["last_dispatch"]["model"] == "sonnet"
