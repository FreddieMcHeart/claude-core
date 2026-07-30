"""Cross-session cost ledger + the PostToolUse blind-spot fix.

Before this change the PostToolUse hook fired only for Agent|Task|Workflow, so
Read/Bash/Grep results — the dominant context flood — were never metered. The
matcher now covers all real tools; these tests pin that handle_post_tool meters a
non-dispatch (Read) result and that every session writes a collectable cost ledger.

Segments, not a running total (ROADMAP.md "Design settled 2026-07-30"): the ledger
used to be written from the CURRENT RUNNING TOTAL on every PostToolUse, but the
underlying /tmp state is wiped at every SessionStart and partially at every
post-compact (`/clear` or auto-compact) — so most of a long session's real activity
never survived to the ledger file (93% of 442 files read zero counters). The fix
stores SEGMENTS: SessionStart and post-compact each APPEND a new segment;
PostToolUse only REPLACES the last segment; `totals` are recomputed by summing all
stored segments on every write, never accumulated independently.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _isolate(monkeypatch, tmp_path):
    """Point both the /tmp working state and the cost-ledger dir at tmp_path.
    Uses monkeypatch so the module globals are restored after each test (no leak
    into test_harness_hygiene.py, which shares the same imported module)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cd, "STATE_DIR", state_dir)
    monkeypatch.setattr(cd, "COST_LEDGER_DIR", tmp_path / "cost-ledger")


# ---------------- build_cost_ledger (pure) ----------------

def test_build_cost_ledger_shape_and_derived_fields():
    state = cd.new_state("s1")
    state["segment_tool_calls_total"] = 3
    state["tool_result_chars"] = 35000
    state["tool_result_chars_by_tool"] = {"Read": 30000, "Bash": 5000}
    led = cd.build_cost_ledger(state)
    assert led["session_id"] == "s1"
    # first-ever write (no existing ledger) always appends, regardless of new_segment
    assert led["segment_count"] == 1
    assert len(led["segments"]) == 1
    seg = led["segments"][0]
    assert seg["tool_calls_total"] == 3
    assert seg["tool_result_chars"] == 35000
    # per-tool breakdown preserved, token-derived, and sorted descending by chars —
    # both at the segment level and in the summed totals
    assert list(seg["by_tool"].keys()) == ["Read", "Bash"]
    totals = led["totals"]
    assert totals["tool_result_chars"] == 35000
    assert totals["tool_result_tokens_est"] == int(35000 / cd.LEDGER_CHARS_PER_TOKEN)
    assert totals["cache_reread_usd_per_turn_est"] >= 0
    assert list(totals["by_tool"].keys()) == ["Read", "Bash"]
    assert totals["by_tool"]["Read"]["chars"] == 30000
    assert totals["by_tool"]["Read"]["tokens"] == int(30000 / cd.LEDGER_CHARS_PER_TOKEN)


def test_build_cost_ledger_empty_state_is_zeroed_not_crashing():
    led = cd.build_cost_ledger(cd.new_state("empty"))
    # a quiet segment (a session/window that did nothing) is still appended, not
    # suppressed — see test_quiet_window_segment_is_not_suppressed below
    assert led["segment_count"] == 1
    assert led["totals"]["tool_result_chars"] == 0
    assert led["totals"]["tool_result_tokens_est"] == 0
    assert led["totals"]["by_tool"] == {}


# ---------------- write_cost_ledger ----------------

def test_write_cost_ledger_persists_collectable_json(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    state = cd.new_state("sess-abc")
    state["tool_result_chars"] = 400
    cd.write_cost_ledger(state)
    p = cd.COST_LEDGER_DIR / "sess-abc.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["session_id"] == "sess-abc"
    assert data["segment_count"] == 1
    assert data["totals"]["tool_result_chars"] == 400


def test_write_cost_ledger_never_raises_on_bad_state(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # missing session_id -> best-effort no-op, must not raise or write
    cd.write_cost_ledger({"session_id": None})
    assert not (cd.COST_LEDGER_DIR / "None.json").exists()
    # completely malformed -> still must not raise
    cd.write_cost_ledger({})


# ---------------- the blind-spot fix: handle_post_tool meters a non-dispatch Read ----------------

def test_post_tool_meters_nondispatch_read_and_writes_ledger(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"session_id": "sread", "tool_name": "Read", "tool_response": "x" * 5000}
    cd.handle_post_tool(payload)
    # state accumulated the Read result (never happened for non-dispatch before the fix)
    state = cd.load_state("sread")
    assert state["tool_result_chars"] >= 5000
    assert state["tool_result_chars_by_tool"]["Read"] >= 5000
    # and it was written to the collectable cross-session ledger
    led = json.loads((cd.COST_LEDGER_DIR / "sread.json").read_text())
    assert led["totals"]["by_tool"]["Read"]["chars"] >= 5000


def test_post_tool_writes_ledger_on_agent_dispatch_and_resets_counters(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # prime some read streak, then an Agent dispatch result arrives
    seed = cd.new_state("sdisp")
    seed["aggregate_reads"] = 5
    seed["agent_counters"]["main"] = {
        "read_streak": 3, "agent_reads": 5, "warnings_fired": ["aggregate_15"]}
    cd.save_state(seed)
    cd.handle_post_tool({"session_id": "sdisp", "tool_name": "Agent", "tool_response": "ok"})
    state = cd.load_state("sdisp")
    # dispatch path still resets the streak/aggregate counters (no regression)...
    assert state["aggregate_reads"] == 0
    assert state["agent_counters"]["main"]["read_streak"] == 0
    assert state["agent_counters"]["main"]["agent_reads"] == 0
    assert state["agent_counters"]["main"]["warnings_fired"] == []
    # ...and the ledger was written on this path too
    assert (cd.COST_LEDGER_DIR / "sdisp.json").exists()


def test_post_tool_meters_mcp_result(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {
        "session_id": "smcp",
        "tool_name": "mcp__claude_ai_Atlassian__getJiraIssue",
        "tool_response": "y" * 6000,
    }
    cd.handle_post_tool(payload)
    state = cd.load_state("smcp")
    # MCP results are metered too (previously invisible to the ledger)
    assert state["tool_result_chars"] >= 6000
    assert state["tool_result_chars_by_tool"]["mcp__claude_ai_Atlassian__getJiraIssue"] >= 6000
    # metered_results is the consistent denominator: it counts the MCP result even though
    # tool_calls_total (a PreToolUse-only counter that never matches mcp__) stays 0.
    assert state["metered_results"] == 1
    assert state["tool_calls_total"] == 0
    led = json.loads((cd.COST_LEDGER_DIR / "smcp.json").read_text())
    assert led["totals"]["tool_result_chars"] >= 6000
    assert led["totals"]["metered_results"] == 1


def test_post_tool_skips_empty_tool_name_bucket(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # a payload with no tool_name still meters total chars but must not create a "" bucket
    cd.handle_post_tool({"session_id": "sblank", "tool_response": "z" * 100})
    state = cd.load_state("sblank")
    assert state["tool_result_chars"] >= 100
    assert state["metered_results"] == 1
    assert "" not in state["tool_result_chars_by_tool"]


# ---------------- compaction resets the ledger window consistently ----------------

def test_post_compact_resets_ledger_window_and_keeps_denominator_matching(tmp_path, monkeypatch):
    """tool_result_chars models in-context drag and resets to 0 on compaction. The
    per-tool breakdown and the metered_results denominator describe the SAME window, so
    they must reset with it — otherwise by_tool becomes a lifetime accumulator next to a
    since-compact headline (never reconciles) and metered_results stops matching its
    numerator (avg-chars-per-result goes meaningless). This pins all three to one window.
    """
    _isolate(monkeypatch, tmp_path)
    sid = "scompact"
    # prime: two metered results across two tools; headline == sum(by_tool); denom == 2
    cd.handle_post_tool({"session_id": sid, "tool_name": "Read", "tool_response": "r" * 4000})
    cd.handle_post_tool({"session_id": sid, "tool_name": "Bash", "tool_response": "b" * 2000})
    primed = cd.load_state(sid)
    primed["tool_calls_total"] = 7  # PreToolUse lifetime counter; must survive compaction
    cd.save_state(primed)
    assert primed["metered_results"] == 2
    assert sum(primed["tool_result_chars_by_tool"].values()) == primed["tool_result_chars"] >= 6000

    # compaction flushes context: all three window fields reset together...
    cd.handle_post_compact({"session_id": sid})
    post = cd.load_state(sid)
    assert post["tool_result_chars"] == 0
    assert post["metered_results"] == 0
    assert post["tool_result_chars_by_tool"] == {}
    # ...while lifetime counters are preserved / advanced
    assert post["tool_calls_total"] == 7
    assert post["compactions_seen"] == 1

    # one result after compaction: the denominator still matches its numerator and the
    # per-tool breakdown reconciles with the headline (the invariant that broke pre-fix).
    cd.handle_post_tool({"session_id": sid, "tool_name": "Read", "tool_response": "r" * 5000})
    final = cd.load_state(sid)
    assert final["metered_results"] == 1
    assert sum(final["tool_result_chars_by_tool"].values()) == final["tool_result_chars"] >= 5000


def test_hooks_json_posttooluse_covers_read_bash_grep_and_mcp():
    path = Path(cd.__file__).resolve().parent / "hooks.json"
    matcher = json.loads(path.read_text())["hooks"]["PostToolUse"][0]["matcher"]
    for tool in ("Read", "Bash", "Grep"):
        assert tool in matcher
    assert "mcp__" in matcher


# ---------------- segments: the fix for the near-empty ledger (ROADMAP 2026-07-30) ----------------

def test_write_cost_ledger_is_idempotent_on_repeated_calls(tmp_path, monkeypatch):
    """The property whose absence WAS the whole bug. write_cost_ledger(state) (no
    new_segment) called TWICE in a row with the exact same state must leave `totals`
    IDENTICAL — not doubled. This is the regression test for "sum on write": if
    build_cost_ledger's segment-update logic ever goes back to adding onto the existing
    segment instead of replacing it, this test must fail."""
    _isolate(monkeypatch, tmp_path)
    state = cd.new_state("sidem")
    state["tool_result_chars"] = 4000
    state["tool_result_chars_by_tool"] = {"Read": 4000}
    state["metered_results"] = 2
    state["aggregate_reads"] = 2
    state["segment_tool_calls_total"] = 2

    cd.write_cost_ledger(state)
    first = json.loads((cd.COST_LEDGER_DIR / "sidem.json").read_text())
    cd.write_cost_ledger(state)
    second = json.loads((cd.COST_LEDGER_DIR / "sidem.json").read_text())

    assert first["segment_count"] == second["segment_count"] == 1
    assert first["totals"] == second["totals"]
    assert second["totals"]["tool_result_chars"] == 4000  # not 8000


def test_post_compact_opens_new_segment_and_preserves_prior_totals(tmp_path, monkeypatch):
    """Segment boundary correctness: a post-compact reset opens a new segment without
    touching the prior one, and totals keep the prior segment's contribution instead of
    zeroing it — "restart/clear opens a new segment; does not zero the derived total;
    does not double-count the segment in flight."""
    _isolate(monkeypatch, tmp_path)
    sid = "sboundary"
    # segment 1: some real activity, written as a plain (non-new-segment) PostToolUse
    cd.handle_post_tool({"session_id": sid, "tool_name": "Read", "tool_response": "r" * 4000})
    led1 = json.loads((cd.COST_LEDGER_DIR / f"{sid}.json").read_text())
    assert led1["segment_count"] == 1
    seg1_chars = led1["segments"][0]["tool_result_chars"]
    assert seg1_chars >= 4000

    # post-compact: zeroes the segment-scoped state and opens segment 2
    cd.handle_post_compact({"session_id": sid})

    led2 = json.loads((cd.COST_LEDGER_DIR / f"{sid}.json").read_text())
    assert led2["segment_count"] == 2
    # (a) two segments now exist
    # (b) segment 1's stored values are UNCHANGED from before the reset
    assert led2["segments"][0]["tool_result_chars"] == seg1_chars
    # segment 2 is the fresh, zeroed window
    assert led2["segments"][1]["tool_result_chars"] == 0
    # (c) totals still include segment 1's contribution, not reset to zero
    assert led2["totals"]["tool_result_chars"] == seg1_chars


def test_totals_equal_sum_of_stored_segments(tmp_path, monkeypatch):
    """Totals are reconstructible from the stored segments alone: sum the segments
    independently in the test (not trusting build_cost_ledger's own arithmetic) and
    compare against `totals`."""
    _isolate(monkeypatch, tmp_path)
    sid = "ssum"
    cd.handle_post_tool({"session_id": sid, "tool_name": "Read", "tool_response": "a" * 1000})
    cd.handle_post_compact({"session_id": sid})
    cd.handle_post_tool({"session_id": sid, "tool_name": "Bash", "tool_response": "b" * 2000})
    cd.handle_post_compact({"session_id": sid})
    cd.handle_post_tool({"session_id": sid, "tool_name": "Grep", "tool_response": "c" * 3000})

    led = json.loads((cd.COST_LEDGER_DIR / f"{sid}.json").read_text())
    assert led["segment_count"] == 3
    segs = led["segments"]

    assert led["totals"]["tool_result_chars"] == sum(s["tool_result_chars"] for s in segs)
    assert led["totals"]["metered_results"] == sum(s["metered_results"] for s in segs)
    assert led["totals"]["tool_calls_total"] == sum(s["tool_calls_total"] for s in segs)
    assert led["totals"]["aggregate_reads"] == sum(s["aggregate_reads"] for s in segs)

    by_tool_sum = {}
    for s in segs:
        for name, entry in s["by_tool"].items():
            by_tool_sum[name] = by_tool_sum.get(name, 0) + entry["chars"]
    assert {k: v["chars"] for k, v in led["totals"]["by_tool"].items()} == by_tool_sum


def test_quiet_window_segment_is_not_suppressed(tmp_path, monkeypatch):
    """A segment with all-zero fields (a quiet window) must still appear in `segments`
    and count toward `segment_count` — the design doc explicitly forbids filtering it
    out to keep the file tidy."""
    _isolate(monkeypatch, tmp_path)
    sid = "squiet"
    cd.handle_post_tool({"session_id": sid, "tool_name": "Read", "tool_response": "a" * 1000})
    # a compaction opens a new segment; nothing happens in it before we inspect the ledger
    cd.handle_post_compact({"session_id": sid})
    led = json.loads((cd.COST_LEDGER_DIR / f"{sid}.json").read_text())
    assert led["segment_count"] == 2
    quiet = led["segments"][1]
    assert quiet["tool_result_chars"] == 0
    assert quiet["tool_calls_total"] == 0
    assert quiet["metered_results"] == 0
    assert quiet["by_tool"] == {}
    assert quiet["dispatches"] == {}


def test_dispatches_counted_by_subagent_type_not_bare_total(tmp_path, monkeypatch):
    """Dispatch counting sources from tool_input.subagent_type on the Agent dispatch
    call and must count BY TYPE, not just a total — the question is "did this session
    delegate, and downward to what?", which a bare count cannot answer."""
    _isolate(monkeypatch, tmp_path)
    sid = "sdispatch2"
    cd.handle_pre_tool({"session_id": sid, "tool_name": "Agent",
                         "tool_input": {"subagent_type": "gh-reader"}})
    cd.handle_pre_tool({"session_id": sid, "tool_name": "Agent",
                         "tool_input": {"subagent_type": "gh-reader"}})
    cd.handle_pre_tool({"session_id": sid, "tool_name": "Agent",
                         "tool_input": {"subagent_type": "gcloud-reader"}})
    state = cd.load_state(sid)
    assert state["dispatches_by_type"] == {"gh-reader": 2, "gcloud-reader": 1}

    cd.handle_post_tool({"session_id": sid, "tool_name": "Agent", "tool_response": "ok"})
    led = json.loads((cd.COST_LEDGER_DIR / f"{sid}.json").read_text())
    assert led["segments"][-1]["dispatches"] == {"gh-reader": 2, "gcloud-reader": 1}
    assert led["totals"]["dispatches"] == {"gh-reader": 2, "gcloud-reader": 1}


def test_write_cost_ledger_survives_corrupt_or_missing_existing_ledger(tmp_path, monkeypatch):
    """A corrupt or missing existing ledger file must not crash write_cost_ledger — it
    should just mean "start fresh" (an empty segments list), not raise."""
    _isolate(monkeypatch, tmp_path)

    # missing file (directory doesn't even exist yet)
    sid_missing = "smissing"
    state = cd.new_state(sid_missing)
    state["tool_result_chars"] = 100
    cd.write_cost_ledger(state)  # must not raise
    led = json.loads((cd.COST_LEDGER_DIR / f"{sid_missing}.json").read_text())
    assert led["segment_count"] == 1
    assert led["totals"]["tool_result_chars"] == 100

    # corrupt JSON already on disk
    sid_corrupt = "scorrupt"
    cd.COST_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    (cd.COST_LEDGER_DIR / f"{sid_corrupt}.json").write_text("{not valid json!!")
    state2 = cd.new_state(sid_corrupt)
    state2["tool_result_chars"] = 500
    cd.write_cost_ledger(state2)  # must not raise
    led2 = json.loads((cd.COST_LEDGER_DIR / f"{sid_corrupt}.json").read_text())
    assert led2["segment_count"] == 1
    assert led2["totals"]["tool_result_chars"] == 500
