"""Tests for lib/cost_ledger_report.py — the cross-session cost-ledger summary."""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "cost_ledger_report.py"
spec = importlib.util.spec_from_file_location("cost_ledger_report", MOD)
rpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpt)


def _led(sid, date, model="opus", calls=10, metered=8, chars=30000,
         tokens=8500, aggr=2, usd=1.5, by_tool=None):
    return {
        "session_id": sid,
        "started_at": f"{date}T10:00:00+00:00",
        "updated_at": f"{date}T12:00:00+00:00",
        "main_model": model,
        "tool_calls_total": calls,
        "metered_results": metered,
        "tool_result_chars": chars,
        "tool_result_tokens_est": tokens,
        "cache_reread_usd_per_turn_est": usd,
        "aggregate_reads": aggr,
        "tool_result_chars_by_tool": by_tool or {"Read": {"chars": chars, "tokens": tokens}},
    }


def _write(dirp, led):
    (dirp / f"{led['session_id']}.json").write_text(json.dumps(led))


# ---------------- loading ----------------

def test_load_ledgers_skips_malformed_and_non_dict(tmp_path):
    _write(tmp_path, _led("good", "2026-07-18"))
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[1, 2, 3]")  # valid json, wrong shape
    loaded = rpt.load_ledgers(tmp_path)
    assert [x["session_id"] for x in loaded] == ["good"]


def test_load_ledgers_empty_dir(tmp_path):
    assert rpt.load_ledgers(tmp_path) == []


# ---------------- zero-session filtering ----------------

def test_is_zero_session():
    assert rpt.is_zero_session(_led("z", "2026-07-18", metered=0, chars=0))
    assert not rpt.is_zero_session(_led("a", "2026-07-18", metered=1, chars=0))
    assert not rpt.is_zero_session(_led("b", "2026-07-18", metered=0, chars=500))


def test_filter_hides_zero_by_default_shows_with_all():
    ledgers = [
        _led("active", "2026-07-18"),
        _led("empty", "2026-07-18", metered=0, chars=0),
    ]
    assert [x["session_id"] for x in rpt.filter_ledgers(ledgers)] == ["active"]
    assert len(rpt.filter_ledgers(ledgers, show_all=True)) == 2


def test_filter_since():
    ledgers = [_led("old", "2026-07-15"), _led("new", "2026-07-19")]
    kept = rpt.filter_ledgers(ledgers, since="2026-07-18")
    assert [x["session_id"] for x in kept] == ["new"]


# ---------------- aggregation ----------------

def test_totals_aggregates_and_counts_models():
    ledgers = [
        _led("a", "2026-07-17", model="opus", calls=10, metered=8, chars=1000, tokens=300, aggr=1),
        _led("b", "2026-07-19", model="sonnet", calls=5, metered=4, chars=2000, tokens=600, aggr=2),
    ]
    t = rpt.totals(ledgers)
    assert t["sessions"] == 2
    assert t["window"] == ["2026-07-17", "2026-07-19"]
    assert t["tool_calls_total"] == 15
    assert t["metered_results"] == 12
    assert t["result_chars"] == 3000
    assert t["result_tokens_est"] == 900
    assert t["aggregate_reads"] == 3
    assert t["models"] == {"opus": 1, "sonnet": 1}


def test_by_tool_rollup_sums_and_sorts_descending():
    ledgers = [
        _led("a", "2026-07-18", by_tool={"Read": {"chars": 100, "tokens": 30},
                                         "Bash": {"chars": 400, "tokens": 120}}),
        _led("b", "2026-07-18", by_tool={"Read": {"chars": 700, "tokens": 200}}),
    ]
    roll = rpt.by_tool_rollup(ledgers)
    assert list(roll.keys()) == ["Read", "Bash"]  # 800 > 400
    assert roll["Read"] == {"chars": 800, "tokens": 230}
    assert roll["Bash"] == {"chars": 400, "tokens": 120}


def test_by_tool_rollup_tolerates_bare_int_values():
    # a raw state dict (pre build_cost_ledger) uses {tool: int}
    ledgers = [_led("a", "2026-07-18", by_tool={"Read": 500})]
    roll = rpt.by_tool_rollup(ledgers)
    assert roll["Read"]["chars"] == 500
    assert roll["Read"]["tokens"] == 0


def test_per_session_rows_sorted_by_tokens_and_capped():
    ledgers = [
        _led("small", "2026-07-18", tokens=100),
        _led("big", "2026-07-18", tokens=900),
        _led("mid", "2026-07-18", tokens=500),
    ]
    top2 = rpt.per_session_rows(ledgers, top=2)
    assert [x["session_id"] for x in top2] == ["big", "mid"]
    assert len(rpt.per_session_rows(ledgers, top=0)) == 3  # 0 = all


# ---------------- formatting / json ----------------

def test_format_report_smoke_and_hidden_note():
    ledgers = [
        _led("active", "2026-07-19", by_tool={"mcp__uncapped_notion__notion_fetch": {"chars": 50, "tokens": 14}}),
        _led("empty", "2026-07-18", metered=0, chars=0),
    ]
    out = rpt.format_report(ledgers)
    assert "COST LEDGER" in out
    assert "BY TOOL" in out
    assert "zero-activity hidden" in out  # the empty session is filtered + noted
    assert "mcp:notion_fetch" in out  # mcp name shortened for the column


def test_format_report_all_includes_zero_sessions():
    ledgers = [_led("empty", "2026-07-18", metered=0, chars=0)]
    hidden = rpt.format_report(ledgers)
    shown = rpt.format_report(ledgers, show_all=True)
    # default: the only (zero-activity) session is filtered out of the table
    assert "no active sessions" in hidden.lower()
    # --all: the session's row is rendered (its date appears) and the table isn't empty
    assert "2026-07-18" in shown
    assert "no active sessions" not in shown.lower()


def test_build_json_shape():
    ledgers = [_led("a", "2026-07-18")]
    j = rpt.build_json(ledgers)
    assert set(j.keys()) == {"totals", "by_tool", "sessions"}
    assert j["totals"]["sessions"] == 1


# ---------------- cli ----------------

def test_main_missing_dir_returns_1(tmp_path, capsys):
    rc = rpt.main(["--dir", str(tmp_path / "nope")])
    assert rc == 1
    assert "No cost-ledger directory" in capsys.readouterr().err


def test_main_empty_dir_returns_1(tmp_path, capsys):
    rc = rpt.main(["--dir", str(tmp_path)])
    assert rc == 1
    assert "No ledger files" in capsys.readouterr().err


def test_main_prints_text_report(tmp_path, capsys):
    _write(tmp_path, _led("a", "2026-07-19"))
    rc = rpt.main(["--dir", str(tmp_path)])
    assert rc == 0
    assert "COST LEDGER" in capsys.readouterr().out


def test_main_json_output(tmp_path, capsys):
    _write(tmp_path, _led("a", "2026-07-19"))
    rc = rpt.main(["--dir", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["sessions"] == 1
