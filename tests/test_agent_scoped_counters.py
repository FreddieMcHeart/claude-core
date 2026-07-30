"""Per-agent state scoping: a sub-agent's reads must not move its dispatcher's
read-discipline counters, and the dispatcher's must not move a sub-agent's.

First tests for this path (2026-07-30). Before this fix, state was keyed only
by session_id — and a dispatched sub-agent reports its DISPATCHER's session_id
(confirmed via the temporary payload probe), so every call landed on one
shared counter regardless of who made it. `agent_counters[scope]` (scope =
agent_id, or "main" for the dispatcher) fixes that; `state["aggregate_reads"]`
stays flat and unscoped on purpose — see _agent_scope's docstring in the hook
— because the cost ledger reads it directly and a sub-agent's reads
legitimately belong in that session-wide total.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_scoping", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _armed(monkeypatch):
    monkeypatch.setattr(cd, "blocks_enabled", lambda payload: True)


def _chained_state(monkeypatch, **fields):
    """State that persists across calls, the way separate hook invocations
    really do via the on-disk file — needed here since every test drives
    several calls and checks how counters accumulate across them."""
    base = cd.new_state("s1")
    base.update(fields)
    saved = {}

    def _load(sid):
        return dict(base)

    def _save(st):
        saved.clear()
        saved.update(st)
        base.clear()
        base.update(st)

    monkeypatch.setattr(cd, "load_state", _load)
    monkeypatch.setattr(cd, "save_state", _save)
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    return saved


def _fire(capsys, tool_name, agent_type=None, agent_id=None, file_path=None):
    payload = {"session_id": "s1", "tool_name": tool_name, "tool_input": {}}
    if file_path:
        payload["tool_input"]["file_path"] = file_path
    if agent_type:
        payload["agent_type"] = agent_type
        payload["agent_id"] = agent_id
    cd.handle_pre_tool(payload)
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_a_subagents_reads_do_not_move_the_dispatchers_counter(monkeypatch, capsys):
    _armed(monkeypatch)
    saved = _chained_state(monkeypatch)

    for _ in range(3):
        _fire(capsys, "Read")  # the dispatcher's own reads
    for _ in range(5):
        _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")

    assert saved["agent_counters"]["main"]["read_streak"] == 3, \
        "the sub-agent's 5 reads must not have moved the dispatcher's streak"
    assert saved["agent_counters"]["sub-1"]["read_streak"] == 5


def test_the_dispatchers_reads_do_not_move_a_subagents_counter(monkeypatch, capsys):
    _armed(monkeypatch)
    saved = _chained_state(monkeypatch)

    for _ in range(4):
        _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")
    for _ in range(2):
        _fire(capsys, "Read")

    assert saved["agent_counters"]["sub-1"]["read_streak"] == 4
    assert saved["agent_counters"]["main"]["read_streak"] == 2


def test_two_distinct_subagents_are_isolated_from_each_other(monkeypatch, capsys):
    _armed(monkeypatch)
    saved = _chained_state(monkeypatch)

    for _ in range(2):
        _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")
    for _ in range(7):
        _fire(capsys, "Bash", agent_type="gh-reader", agent_id="sub-2")

    assert saved["agent_counters"]["sub-1"]["read_streak"] == 2
    assert saved["agent_counters"]["sub-2"]["read_streak"] == 7


def test_flat_aggregate_reads_stays_session_wide_across_all_scopes(monkeypatch, capsys):
    """The ledger's counter is deliberately NOT scoped — a sub-agent's reads
    legitimately count toward the session's total context cost even though
    they must not move its dispatcher's own block-tier counter."""
    _armed(monkeypatch)
    saved = _chained_state(monkeypatch)

    _fire(capsys, "Read")
    _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")
    _fire(capsys, "Read", agent_type="gh-reader", agent_id="sub-2")

    assert saved["aggregate_reads"] == 3, \
        "the flat ledger counter sums reads from every scope in the session"
    assert saved["agent_counters"]["main"]["agent_reads"] == 1
    assert saved["agent_counters"]["sub-1"]["agent_reads"] == 1
    assert saved["agent_counters"]["sub-2"]["agent_reads"] == 1


def test_a_dispatchers_write_resets_only_its_own_scopes_streak(monkeypatch, capsys):
    _armed(monkeypatch)
    saved = _chained_state(monkeypatch)

    for _ in range(3):
        _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")
    for _ in range(2):
        _fire(capsys, "Read")

    _fire(capsys, "Write", file_path="/tmp/x")  # the dispatcher writes

    assert saved["agent_counters"]["main"]["read_streak"] == 0
    assert saved["agent_counters"]["sub-1"]["read_streak"] == 3, \
        "a dispatcher's Write must not reset a sub-agent's streak"


def test_warn_tier_dedup_is_scoped_so_one_agent_crossing_a_threshold_does_not_suppress_another(
        monkeypatch, capsys):
    """If the fire-once key for aggregate_15 were a flat, session-wide string
    (as fire_once's generic dedup would produce), whichever scope crossed
    AGGREGATE_THRESHOLD first would silently claim the warning for every other
    scope too — sub-1 hitting it here must not silence main's own warning."""
    # Seeded one read short of the threshold, rather than looped from zero:
    # looping AGGREGATE_THRESHOLD (15) consecutive Reads on one scope would hit
    # STREAK_BLOCK_THRESHOLD (10) first and get refused, never reaching 15.
    # NOTE: two independent dict literals, not dict(shared_source) twice — the
    # latter shallow-copies the nested `warnings_fired` LIST by reference, so
    # both scopes would silently share one list and this test would pass for
    # the wrong reason (or fail confusingly, as it did while authoring this).
    _armed(monkeypatch)
    saved = _chained_state(monkeypatch, agent_counters={
        "sub-1": {"read_streak": 0, "agent_reads": cd.AGGREGATE_THRESHOLD - 1,
                  "warnings_fired": []},
        "main": {"read_streak": 0, "agent_reads": cd.AGGREGATE_THRESHOLD - 1,
                 "warnings_fired": []},
    })

    _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")
    assert "aggregate_15" in saved["agent_counters"]["sub-1"]["warnings_fired"]

    messages = _fire(capsys, "Read")
    assert any("Aggregate read discipline" in m["systemMessage"] for m in messages), \
        "the dispatcher's own aggregate_15 warning must still fire even though sub-1 already claimed it"
    assert "aggregate_15" in saved["agent_counters"]["main"]["warnings_fired"]


def test_fire_log_records_the_scoped_number_that_actually_fired_it(monkeypatch, capsys):
    """A sub-agent's warning must not log the flat, session-wide aggregate_reads
    as if that were the count that tripped it — that number is the DISPATCHER's,
    since the flat field sums every scope. log_fire keeps the flat field for
    continuity but must ALSO carry the scoped figure and which scope fired,
    or the one instrument already trusted for "warnings ignored 82-88%" would
    record a number that did not cause the event."""
    fires = []
    _armed(monkeypatch)
    base = cd.new_state("s1")
    base["agent_counters"]["sub-1"] = {
        "read_streak": 0, "agent_reads": cd.AGGREGATE_THRESHOLD - 1, "warnings_fired": []}
    base["aggregate_reads"] = 999  # deliberately far from sub-1's real count

    monkeypatch.setattr(cd, "load_state", lambda sid: dict(base))
    monkeypatch.setattr(cd, "save_state", lambda st: None)
    monkeypatch.setattr(cd, "log_fire",
                         lambda rule, sid, action, **details: fires.append((rule, details)))

    _fire(capsys, "Read", agent_type="general-purpose", agent_id="sub-1")

    rule, details = next(f for f in fires if f[0] == "aggregate_15")
    assert details["scope"] == "sub-1"
    assert details["agent_reads"] == cd.AGGREGATE_THRESHOLD, \
        "must log the count that actually crossed the threshold, not the flat session total"
    assert details["aggregate_reads"] == 1000, \
        "the flat field is still logged too, for continuity — just not as the only number"
