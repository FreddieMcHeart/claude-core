"""The state file's published view for external consumers (the statusline HUD).

Why these fields exist at all: the HUD read `aggregate_reads`, which counts every
inline read in the session INCLUDING a sub-agent's — sub-agents share their
dispatcher's session_id, so their calls land in the same flat counter. So
dispatching a scout made the displayed pressure rise. The gauge moved against the
one action the discipline exists to encourage.

The discriminating test here is `test_a_subagent_read_does_not_move_the_main_gauge`.
The others would all pass against the old behaviour, because in a session with no
sub-agent activity `aggregate_reads` and the main scope's count are equal — which
is exactly why the defect survived: on the author's own screen the two numbers
agreed until the moment delegation started.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_hud_fields", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _saved(tmp_path, monkeypatch, state):
    """save_state, then read back what actually landed on disk.

    Deliberately asserts against the FILE, not the in-memory dict. The HUD reads
    the file; a field mutated in memory and lost on serialisation would satisfy
    an in-memory assertion and still leave the gauge broken.
    """
    monkeypatch.setattr(cd, "STATE_DIR", tmp_path)
    cd.save_state(state)
    return json.loads((tmp_path / f"cc-discipline-{state['session_id']}.json").read_text())


def _state(session_id="s1", **over):
    st = cd.new_state(session_id)
    st.update(over)
    return st


# ---------------- the field means the main scope, not the session ----------------

def test_a_subagent_read_does_not_move_the_main_gauge(tmp_path, monkeypatch):
    """THE test. A dispatched scout's reads raise the flat session counter and
    must NOT raise the published main-scope gauge — otherwise delegating makes
    the HUD look worse, which is the defect this field was added to fix."""
    st = _state(
        aggregate_reads=12,               # 4 in main + 8 done by a scout
        agent_counters={
            "main": {"read_streak": 1, "agent_reads": 4, "warnings_fired": []},
            "agent_abc123": {"read_streak": 8, "agent_reads": 8, "warnings_fired": []},
        },
    )
    out = _saved(tmp_path, monkeypatch, st)
    assert out["main_agent_reads"] == 4
    assert out["aggregate_reads"] == 12, "the flat counter must stay untouched"
    assert out["main_agent_reads"] != out["aggregate_reads"], (
        "if these are equal the fixture has no sub-agent activity and this test "
        "proves nothing"
    )


def test_main_scope_count_is_published_verbatim(tmp_path, monkeypatch):
    st = _state(agent_counters={"main": {"read_streak": 3, "agent_reads": 7,
                                         "warnings_fired": []}})
    assert _saved(tmp_path, monkeypatch, st)["main_agent_reads"] == 7


# ---------------- derived, not incremented ----------------

def test_the_field_is_recomputed_on_every_save_not_carried(tmp_path, monkeypatch):
    """A stale published value must not survive a save. This is what makes the
    field derived rather than a second counter: there is no path by which it can
    disagree with the scope it summarises."""
    st = _state(
        main_agent_reads=999,             # a lie planted in the incoming state
        agent_counters={"main": {"read_streak": 0, "agent_reads": 2,
                                 "warnings_fired": []}},
    )
    assert _saved(tmp_path, monkeypatch, st)["main_agent_reads"] == 2


# ---------------- absence is 0, never a crash ----------------

def test_no_agent_counters_yet_publishes_zero(tmp_path, monkeypatch):
    """First call of a session: agent_counters is {} and there is no main bucket."""
    assert _saved(tmp_path, monkeypatch, _state())["main_agent_reads"] == 0


def test_null_agent_counters_does_not_raise(tmp_path, monkeypatch):
    """A state file hand-edited or written by an older version can carry null.
    save_state runs on EVERY tool call, so an exception here takes the whole
    hook down — fail to 0, never fail loudly."""
    assert _saved(tmp_path, monkeypatch, _state(agent_counters=None))["main_agent_reads"] == 0


def test_main_bucket_without_the_key_publishes_zero(tmp_path, monkeypatch):
    st = _state(agent_counters={"main": {"read_streak": 2, "warnings_fired": []}})
    assert _saved(tmp_path, monkeypatch, st)["main_agent_reads"] == 0


# ---------------- the threshold travels with the number ----------------

def test_threshold_is_published_so_the_hud_stops_hardcoding_it(tmp_path, monkeypatch):
    """The HUD had 15 hardcoded in two places. A duplicated constant does not
    fail when it drifts — it displays a ratio against a limit that is no longer
    the limit, and nothing anywhere errors."""
    out = _saved(tmp_path, monkeypatch, _state())
    assert out["aggregate_threshold"] == cd.AGGREGATE_THRESHOLD


def test_published_threshold_is_the_warn_tier_not_the_block_tier(tmp_path, monkeypatch):
    """Pinned because the two are easy to confuse and mean different things: the
    HUD gauge has always been scaled to the WARN threshold, and swapping in the
    block threshold would silently rescale every session's gauge."""
    out = _saved(tmp_path, monkeypatch, _state())
    assert out["aggregate_threshold"] == 15
    assert out["aggregate_threshold"] != cd.AGGREGATE_BLOCK_THRESHOLD
