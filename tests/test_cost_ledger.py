"""Cross-session cost ledger + the PostToolUse blind-spot fix.

Before this change the PostToolUse hook fired only for Agent|Task|Workflow, so
Read/Bash/Grep results — the dominant context flood — were never metered. The
matcher now covers all real tools; these tests pin that handle_post_tool meters a
non-dispatch (Read) result and that every session writes a collectable cost ledger.
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
    state["tool_calls_total"] = 3
    state["tool_result_chars"] = 35000
    state["tool_result_chars_by_tool"] = {"Read": 30000, "Bash": 5000}
    led = cd.build_cost_ledger(state)
    assert led["session_id"] == "s1"
    assert led["tool_result_chars"] == 35000
    assert led["tool_result_tokens_est"] == int(35000 / cd.LEDGER_CHARS_PER_TOKEN)
    assert led["cache_reread_usd_per_turn_est"] >= 0
    # per-tool breakdown preserved, token-derived, and sorted descending by chars
    assert list(led["tool_result_chars_by_tool"].keys()) == ["Read", "Bash"]
    assert led["tool_result_chars_by_tool"]["Read"]["chars"] == 30000
    assert led["tool_result_chars_by_tool"]["Read"]["tokens"] == int(30000 / cd.LEDGER_CHARS_PER_TOKEN)


def test_build_cost_ledger_empty_state_is_zeroed_not_crashing():
    led = cd.build_cost_ledger(cd.new_state("empty"))
    assert led["tool_result_chars"] == 0
    assert led["tool_result_tokens_est"] == 0
    assert led["tool_result_chars_by_tool"] == {}


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
    assert data["tool_result_chars"] == 400


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
    assert led["tool_result_chars_by_tool"]["Read"]["chars"] >= 5000


def test_post_tool_writes_ledger_on_agent_dispatch_and_resets_counters(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # prime some read streak, then an Agent dispatch result arrives
    seed = cd.new_state("sdisp")
    seed["aggregate_reads"] = 5
    seed["read_streak"] = 3
    cd.save_state(seed)
    cd.handle_post_tool({"session_id": "sdisp", "tool_name": "Agent", "tool_response": "ok"})
    state = cd.load_state("sdisp")
    # dispatch path still resets the streak/aggregate counters (no regression)...
    assert state["aggregate_reads"] == 0
    assert state["read_streak"] == 0
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
    assert led["tool_result_chars"] >= 6000
    assert led["metered_results"] == 1


def test_post_tool_skips_empty_tool_name_bucket(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # a payload with no tool_name still meters total chars but must not create a "" bucket
    cd.handle_post_tool({"session_id": "sblank", "tool_response": "z" * 100})
    state = cd.load_state("sblank")
    assert state["tool_result_chars"] >= 100
    assert state["metered_results"] == 1
    assert "" not in state["tool_result_chars_by_tool"]


def test_hooks_json_posttooluse_covers_read_bash_grep_and_mcp():
    path = Path(cd.__file__).resolve().parent / "hooks.json"
    matcher = json.loads(path.read_text())["hooks"]["PostToolUse"][0]["matcher"]
    for tool in ("Read", "Bash", "Grep"):
        assert tool in matcher
    assert "mcp__" in matcher
