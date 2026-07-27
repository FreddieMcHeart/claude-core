"""The read-discipline hard-block tier: what it counts, and what it tells you to do.

First tests for this path. It went unexercised while the two defects below were
live, which is the whole argument for writing them now rather than after the next
incident.

Both defects came from one live report (2026-07-27): a foreground session hit the
block, dispatched the sub-agent the message recommended, and the sub-agent landed
in the same block on the same shared counter — then read `CC_DISCIPLINE_BLOCK=0`
out of the text that blocked it and proposed exporting it. Two of the three causes
are fixed here (the counter ratchet, and the message handing out its own bypass);
the third, agent detection, needs a signal nobody has measured yet.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_block", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _armed(monkeypatch):
    """Force the block tier ON. It is off by default in this session, which is
    itself one of the reported defects: `detect_session_mode()` keys on
    $CLAUDE_JOB_DIR — the BACKGROUND-JOB signal — so every background main agent
    is exempted while every foreground one is blocked. A test that ran under the
    ambient gating would silently exercise nothing."""
    monkeypatch.setattr(cd, "blocks_enabled", lambda: True)


def _state(monkeypatch, **fields):
    """Drive handle_pre_tool against an in-memory state, capturing what it saves."""
    saved = {}
    # Built from the real new_state() rather than hand-rolled. A hand-rolled dict
    # was missing `files_edited` and the edit-loop path raised KeyError — a fixture
    # narrower than the structure it stands in for, which is the failure mode this
    # repo has a whole wiki page about.
    base = cd.new_state("s1")
    base.update(fields)
    monkeypatch.setattr(cd, "load_state", lambda sid: dict(base))
    monkeypatch.setattr(cd, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    return saved


def _read(capsys, tool="Read"):
    cd.handle_pre_tool({"session_id": "s1", "tool_name": tool, "tool_input": {}})
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


# ---------------- a refused call must not be counted ----------------

def test_blocked_call_does_not_advance_the_counters(monkeypatch, capsys):
    """The ratchet. Counting a refused call makes the counter measure work that
    never happened, and every retry pushes further over — which is why the block
    read as inescapable for reasons the threshold alone did not explain."""
    _armed(monkeypatch)
    saved = _state(monkeypatch, read_streak=cd.STREAK_BLOCK_THRESHOLD - 1)
    payload = _read(capsys)
    assert payload["decision"] == "block"
    assert saved["read_streak"] == cd.STREAK_BLOCK_THRESHOLD - 1, \
        "a refused call performed no read, so the streak must not move"


def test_repeated_blocks_report_the_same_number(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch, read_streak=cd.STREAK_BLOCK_THRESHOLD - 1)
    first = _read(capsys)["reason"]
    second = _read(capsys)["reason"]
    assert first == second, "the reported count must not climb while blocked"
    assert f"{cd.STREAK_BLOCK_THRESHOLD}th" in first


def test_an_allowed_read_still_advances_the_counters(monkeypatch, capsys):
    """The counterpart, and the one that would catch an over-eager fix: only
    calls that are let through may move the number."""
    _armed(monkeypatch)
    saved = _state(monkeypatch, read_streak=2, aggregate_reads=5)
    assert _read(capsys) is None or _read(capsys).get("decision") != "block"
    assert saved["read_streak"] == 3
    assert saved["aggregate_reads"] == 6


def test_aggregate_tier_blocks_and_does_not_count_the_refused_call(monkeypatch, capsys):
    _armed(monkeypatch)
    saved = _state(monkeypatch, aggregate_reads=cd.AGGREGATE_BLOCK_THRESHOLD - 1,
                   read_streak=0)
    payload = _read(capsys)
    assert payload["decision"] == "block"
    assert saved["aggregate_reads"] == cd.AGGREGATE_BLOCK_THRESHOLD - 1


# ---------------- the message must not hand out its own bypass ----------------

def test_streak_block_message_does_not_advertise_the_override(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch, read_streak=cd.STREAK_BLOCK_THRESHOLD - 1)
    reason = _read(capsys)["reason"]
    assert "CC_DISCIPLINE_BLOCK" not in reason


def test_aggregate_block_message_does_not_advertise_the_override(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch, aggregate_reads=cd.AGGREGATE_BLOCK_THRESHOLD - 1)
    reason = _read(capsys)["reason"]
    assert "CC_DISCIPLINE_BLOCK" not in reason


def test_cat_as_read_block_does_not_advertise_the_override(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                        "tool_input": {"command": "cat lib/config_loader.py"}})
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "CC_DISCIPLINE_BLOCK" not in payload["reason"]


def test_the_kill_switch_itself_still_works(monkeypatch):
    """Removing the ADVERTISEMENT is not removing the escape hatch. If a later
    edit conflates the two, this fails."""
    monkeypatch.setenv("CC_DISCIPLINE_BLOCK", "0")
    monkeypatch.setattr(cd, "detect_session_mode", lambda: "interactive")
    assert cd.blocks_enabled() is False


# ---------------- the remedies offered must actually work for that tier ----------------

def test_streak_message_offers_write_because_a_write_resets_the_streak(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch, read_streak=cd.STREAK_BLOCK_THRESHOLD - 1)
    reason = _read(capsys)["reason"]
    assert "write/edit something concrete" in reason


def test_aggregate_message_says_a_write_will_not_help(monkeypatch, capsys):
    """A write resets `read_streak` and NOT `aggregate_reads`, so offering it to an
    aggregate-blocked session is a remedy that leaves the session blocked — the
    same shape as the bypass line, one step subtler."""
    _armed(monkeypatch)
    _state(monkeypatch, aggregate_reads=cd.AGGREGATE_BLOCK_THRESHOLD - 1)
    reason = _read(capsys)["reason"]
    assert "does NOT reset the session aggregate" in reason
    assert "write/edit something concrete" not in reason


def test_a_write_resets_the_streak_but_not_the_aggregate(monkeypatch, capsys):
    """Pins the asymmetry the two messages now describe, so a change to either
    counter's reset semantics breaks the claim rather than just the prose."""
    _armed(monkeypatch)
    saved = _state(monkeypatch, read_streak=7, aggregate_reads=30)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Write",
                        "tool_input": {"file_path": "/tmp/x"}})
    capsys.readouterr()
    assert saved["read_streak"] == 0
    assert saved["aggregate_reads"] == 30


# ---------------- the gating defect that is NOT fixed here ----------------

def test_background_sessions_are_exempt_which_is_the_open_defect(monkeypatch):
    """Documents the live behaviour rather than the intent, so the eventual fix
    has something concrete to change.

    `detect_session_mode()` keys on $CLAUDE_JOB_DIR, the harness's BACKGROUND-JOB
    signal. So the exemption written for reader/scout sub-agents lands on
    background MAIN agents instead, while a foreground sub-agent — the population
    it was written for — gets blocked on its parent's shared counter. Inverted on
    both sides. Fixing it needs a signal that identifies an Agent-tool sub-agent,
    which has not been measured; guessing a payload key is how a detector gets
    built on an assumption.
    """
    monkeypatch.delenv("CC_DISCIPLINE_BLOCK", raising=False)
    monkeypatch.setattr(cd, "is_expensive_main_model", lambda: True)
    monkeypatch.setenv("CLAUDE_JOB_DIR", "/tmp/some-job")
    assert cd.detect_session_mode() == "agent"
    assert cd.blocks_enabled() is False, \
        "background mains are exempt today — this is the open half of the defect"
    monkeypatch.delenv("CLAUDE_JOB_DIR")
    assert cd.detect_session_mode() == "interactive"
    assert cd.blocks_enabled() is True, \
        "foreground sessions are blocked, including foreground sub-agents"


def test_bash_counts_toward_the_streak_content_blind(monkeypatch, capsys):
    """Also reported and deliberately NOT changed yet. `Bash` is in READ_TOOLS and
    the increment inspects no command text, so `git commit` counts as a read. The
    two available fixes both cost something — classifying command content is a
    fresh unknown-to-permissive default, and dropping Bash from the streak gives up
    the `Bash(cat)`/`Bash(ls)` case the streak was written for — so it needs its own
    decision. Pinned so the current behaviour is explicit, not assumed.
    """
    _armed(monkeypatch)
    saved = _state(monkeypatch, read_streak=1)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                        "tool_input": {"command": "git commit -m x"}})
    capsys.readouterr()
    assert saved["read_streak"] == 2, "an outbound git write currently counts as a read"
