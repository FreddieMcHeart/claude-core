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
    itself one of the reported defects: `blocks_enabled()`'s haiku-main
    exemption. A test that ran under the ambient gating would silently
    exercise nothing."""
    monkeypatch.setattr(cd, "blocks_enabled", lambda payload: True)


def _state(monkeypatch, **fields):
    """Drive handle_pre_tool against an in-memory state, capturing what it saves.

    `read_streak`/`agent_reads` kwargs seed the MAIN scope's nested per-agent
    counters (`agent_counters["main"]`), since that's where handle_pre_tool
    keeps them for a payload with no agent_type/agent_id — every test in this
    file drives a plain main-agent call via `_read()`. `aggregate_reads` stays
    a top-level kwarg: it seeds the FLAT, ledger-owned field, a different
    counter since 2026-07-30 (see _agent_scope's docstring in the hook).
    """
    saved = {}
    # Built from the real new_state() rather than hand-rolled. A hand-rolled dict
    # was missing `files_edited` and the edit-loop path raised KeyError — a fixture
    # narrower than the structure it stands in for, which is the failure mode this
    # repo has a whole wiki page about.
    base = cd.new_state("s1")
    scoped = {k: fields.pop(k) for k in ("read_streak", "agent_reads") if k in fields}
    base.update(fields)
    if scoped:
        base["agent_counters"]["main"] = {"read_streak": 0, "agent_reads": 0, "warnings_fired": []}
        base["agent_counters"]["main"].update(scoped)
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
    assert saved["agent_counters"]["main"]["read_streak"] == cd.STREAK_BLOCK_THRESHOLD - 1, \
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
    assert saved["agent_counters"]["main"]["read_streak"] == 3
    assert saved["aggregate_reads"] == 6


def test_aggregate_tier_blocks_and_does_not_count_the_refused_call(monkeypatch, capsys):
    _armed(monkeypatch)
    saved = _state(monkeypatch, agent_reads=cd.AGGREGATE_BLOCK_THRESHOLD - 1,
                   read_streak=0)
    payload = _read(capsys)
    assert payload["decision"] == "block"
    assert saved["agent_counters"]["main"]["agent_reads"] == cd.AGGREGATE_BLOCK_THRESHOLD - 1


# ---------------- the message must not hand out its own bypass ----------------

def test_streak_block_message_does_not_advertise_the_override(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch, read_streak=cd.STREAK_BLOCK_THRESHOLD - 1)
    reason = _read(capsys)["reason"]
    assert "CC_DISCIPLINE_BLOCK" not in reason


def test_aggregate_block_message_does_not_advertise_the_override(monkeypatch, capsys):
    _armed(monkeypatch)
    _state(monkeypatch, agent_reads=cd.AGGREGATE_BLOCK_THRESHOLD - 1)
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
    assert cd.blocks_enabled({"tool_name": "Read"}) is False


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
    _state(monkeypatch, agent_reads=cd.AGGREGATE_BLOCK_THRESHOLD - 1)
    reason = _read(capsys)["reason"]
    assert "does NOT reset the session aggregate" in reason
    assert "write/edit something concrete" not in reason


def test_a_write_resets_the_streak_but_not_the_aggregate(monkeypatch, capsys):
    """Pins the asymmetry the two messages now describe, so a change to either
    counter's reset semantics breaks the claim rather than just the prose."""
    _armed(monkeypatch)
    saved = _state(monkeypatch, read_streak=7, agent_reads=30, aggregate_reads=30)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Write",
                        "tool_input": {"file_path": "/tmp/x"}})
    capsys.readouterr()
    assert saved["agent_counters"]["main"]["read_streak"] == 0
    assert saved["agent_counters"]["main"]["agent_reads"] == 30, \
        "a non-read tool resets the streak, not the per-agent aggregate count"
    assert saved["aggregate_reads"] == 30


# ---------------- the gating defect, now fixed ----------------

def test_subagent_exemption_keys_on_the_payload_not_the_background_job_signal(monkeypatch):
    """Was the open half of a defect: the exemption used to key on
    detect_session_mode(), which answers "is this process a background job?" —
    a DIFFERENT population from "is this an Agent-tool sub-agent call?". That
    inverted both halves: background MAINS were exempt (never the intent) and
    foreground SUB-AGENTS were blocked on their dispatcher's shared counter
    (exactly the population the exemption exists to protect). Fixed 2026-07-30
    via is_subagent_call(payload), which reads `agent_type` directly instead of
    asking $CLAUDE_JOB_DIR. detect_session_mode() itself is untouched — it still
    answers the background-job question correctly for its other 3 call sites.
    """
    monkeypatch.delenv("CC_DISCIPLINE_BLOCK", raising=False)
    monkeypatch.setattr(cd, "is_expensive_main_model", lambda: True)

    monkeypatch.setenv("CLAUDE_JOB_DIR", "/tmp/some-job")
    assert cd.blocks_enabled({"tool_name": "Read"}) is True, \
        "a background-job MAIN call is no longer exempt just for being a background job"
    assert cd.blocks_enabled(
        {"tool_name": "Read", "agent_type": "general-purpose", "agent_id": "a1"}
    ) is False, "a real sub-agent call is exempt regardless of $CLAUDE_JOB_DIR"

    monkeypatch.delenv("CLAUDE_JOB_DIR")
    assert cd.blocks_enabled({"tool_name": "Read"}) is True, \
        "a foreground main call is still blockable, as before"
    assert cd.blocks_enabled(
        {"tool_name": "Read", "agent_type": "gh-reader", "agent_id": "a2"}
    ) is False, "a real sub-agent call is exempt in the foreground too"


def test_known_bash_writes_reset_the_streak_instead_of_counting(monkeypatch, capsys):
    """Fixed 2026-08-03. `Bash` is in READ_TOOLS and the increment used to inspect no
    command text, so `git commit`, `gh pr create`, `downbeat reply`, and `claude
    plugin update` each counted as a read — four reported instances across two
    sessions in one day. `is_bash_write_command` carves known git/gh/downbeat/
    claude-plugin mutating verbs out of the read count; they now take the same
    streak-reset path an Edit/Write call would."""
    _armed(monkeypatch)
    for cmd in (
        "git commit -m x",
        "git push",
        "git worktree remove /tmp/pr48-verify --force",
        'gh pr create --title x --body y',
        "downbeat reply e8b3dd1af912 hello",
        "claude plugin update claude-core-hooks@claude-core-local",
    ):
        saved = _state(monkeypatch, read_streak=1)
        cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                            "tool_input": {"command": cmd}})
        capsys.readouterr()
        assert saved["agent_counters"]["main"]["read_streak"] == 0, \
            f"{cmd!r} is a known write and must reset, not advance, the streak"


def test_known_bash_writes_do_not_touch_the_aggregate(monkeypatch, capsys):
    """Mirrors test_a_write_resets_the_streak_but_not_the_aggregate for the Bash
    case: a recognized write resets read_streak but must not move agent_reads or
    the flat aggregate_reads ledger — those only move on an actual read."""
    _armed(monkeypatch)
    saved = _state(monkeypatch, read_streak=3, agent_reads=10, aggregate_reads=10)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                        "tool_input": {"command": "gh pr create --title x --body y"}})
    capsys.readouterr()
    assert saved["agent_counters"]["main"]["read_streak"] == 0
    assert saved["agent_counters"]["main"]["agent_reads"] == 10
    assert saved["aggregate_reads"] == 10


def test_unrecognized_bash_still_counts_toward_the_streak(monkeypatch, capsys):
    """The fix must stay content-blind for anything it doesn't recognize as a known
    write — that default is what preserves the Bash(cat)/Bash(ls) read-substitute
    case the streak was written for in the first place."""
    _armed(monkeypatch)
    # Not `cat lib/config_loader.py`: that specific command is intercepted by the
    # separate cat-as-Read block tier (see test_cat_as_read_block_does_not_advertise
    # _the_override above) and returns before ever reaching the streak counter —
    # asserting a streak advance on it would test the wrong code path.
    saved = _state(monkeypatch, read_streak=1)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                        "tool_input": {"command": "ls -la"}})
    capsys.readouterr()
    assert saved["agent_counters"]["main"]["read_streak"] == 2

    saved = _state(monkeypatch, read_streak=2)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                        "tool_input": {"command": "git log --oneline -5"}})
    capsys.readouterr()
    assert saved["agent_counters"]["main"]["read_streak"] == 3, \
        "git log is a read, not a write, and must still count"

    saved = _state(monkeypatch, read_streak=3)
    cd.handle_pre_tool({"session_id": "s1", "tool_name": "Bash",
                        "tool_input": {"command": "gh pr diff 48"}})
    capsys.readouterr()
    assert saved["agent_counters"]["main"]["read_streak"] == 4, \
        "gh pr diff is a read, not a write, and must still count"


def test_bash_write_matcher_catches_compound_and_flagged_forms():
    """Independent review of #50 (2026-08-03) ran the first cut of
    is_bash_write_command against 24 real command strings and found 10 misses,
    0 false positives — cmd[0] was checked, but the write verb wasn't there. A
    leading `cd <path> &&`, `git -C <path>`, `git -c k=v`, or a leading VAR=val
    env assignment all shielded it. Pinned directly against the function (the
    population the review actually exercised) rather than via handle_pre_tool."""
    for cmd in (
        "cd /Users/x/.claude && git commit -q -F -",
        "cd /Users/x/.claude && git push",
        "git add CLAUDE.md && git commit -m x",
        "git -C /Users/x/.claude commit -m x",
        "git -C /Users/x/.claude push",
        "GIT_AUTHOR_NAME=x git commit -m y",
        "git stash",
        "git branch feat/x",
    ):
        assert cd.is_bash_write_command(cmd) is True, \
            f"{cmd!r} is a write and the matcher must see it regardless of position"


def test_bash_write_matcher_stays_bounded_to_the_reported_clis():
    """`gh api`/`gh workflow run` are deliberately NOT covered — arbitrary
    API/workflow dispatch is a different, unbounded population than the four
    reported CLIs. And the positional fix must not turn genuine reads (branch
    listing, stash list/show) into false positives — the review found 0 false
    positives on its 24-command table and this must stay that way."""
    for cmd in (
        "gh api -X POST repos/o/r/issues",
        "gh workflow run ci.yaml",
        "git branch",
        "git branch -a",
        "git stash list",
        "git stash show",
        "git log --oneline -5",
        "gh pr diff 48",
    ):
        assert cd.is_bash_write_command(cmd) is False, f"{cmd!r} must stay a read"
