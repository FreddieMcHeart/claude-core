"""Tests for lib/fire_log_report.py — the cost-discipline fire-log summary."""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "fire_log_report.py"
spec = importlib.util.spec_from_file_location("fire_log_report", MOD)
rpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpt)


def _fire(sid, date, rule, action="warn", file_path=None):
    e = {"ts": f"{date}T12:00:00+00:00", "rule": rule, "action": action, "session_id": sid}
    if file_path is not None:
        e["file_path"] = file_path
    return e


def _write_log(path, fires):
    path.write_text("\n".join(json.dumps(f) for f in fires) + "\n")


# ---------------- loading ----------------

def test_load_fires_skips_malformed_and_blank(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        json.dumps(_fire("s1", "2026-07-19", "streak_4")) + "\n"
        + "\n"                      # blank line
        + "{bad json\n"             # unparseable
        + "[1, 2]\n"                # valid json, wrong shape
        + json.dumps(_fire("s2", "2026-07-19", "edit_loop")) + "\n"
    )
    fires = rpt.load_fires(p)
    assert [f["session_id"] for f in fires] == ["s1", "s2"]


def test_load_fires_missing_file_returns_empty(tmp_path):
    assert rpt.load_fires(tmp_path / "nope.jsonl") == []


# ---------------- aggregation ----------------

def test_totals():
    fires = [
        _fire("s1", "2026-07-17", "streak_4", "warn"),
        _fire("s1", "2026-07-19", "block_read_streak", "block"),
        _fire("s2", "2026-07-18", "streak_4", "warn"),
    ]
    t = rpt.totals(fires)
    assert t["fires"] == 3
    assert t["window"] == ["2026-07-17", "2026-07-19"]
    assert t["sessions"] == 2
    assert t["rules"] == 2
    assert t["actions"] == {"warn": 2, "block": 1}


def test_by_rule_counts_and_action_split():
    fires = [
        _fire("s1", "2026-07-19", "streak_4", "warn"),
        _fire("s1", "2026-07-19", "streak_4", "warn"),
        _fire("s2", "2026-07-19", "block_read_streak", "block"),
    ]
    br = rpt.by_rule(fires)
    assert list(br.keys()) == ["streak_4", "block_read_streak"]  # 2 > 1
    assert br["streak_4"] == {"total": 2, "warn": 2, "info": 0, "block": 0}
    assert br["block_read_streak"]["block"] == 1


def test_by_session_sorted_descending():
    fires = [_fire("a", "2026-07-19", "r")] + [_fire("b", "2026-07-19", "r") for _ in range(3)]
    assert list(rpt.by_session(fires).items())[0] == ("b", 3)


def test_by_file_only_entries_with_filepath():
    fires = [
        _fire("s", "2026-07-19", "edit_loop", "warn", "/x/a.py"),
        _fire("s", "2026-07-19", "edit_loop", "warn", "/x/a.py"),
        _fire("s", "2026-07-19", "streak_4", "warn"),  # no file_path
    ]
    assert rpt.by_file(fires) == {"/x/a.py": 2}


def test_filter_since_and_rule():
    fires = [_fire("s", "2026-07-15", "streak_4"), _fire("s", "2026-07-19", "edit_loop")]
    assert [f["rule"] for f in rpt.filter_fires(fires, since="2026-07-18")] == ["edit_loop"]
    assert [f["rule"] for f in rpt.filter_fires(fires, rule="streak_4")] == ["streak_4"]


# ---------------- formatting / json ----------------

def test_format_report_smoke():
    fires = [
        _fire("sess1234abcd", "2026-07-19", "streak_4", "warn"),
        _fire("sess1234abcd", "2026-07-19", "block_read_streak", "block"),
    ]
    out = rpt.format_report(fires)
    assert "COST-DISCIPLINE FIRE-LOG" in out
    assert "BY RULE" in out
    assert "block:1" in out
    assert "TOP SESSIONS" in out


def test_format_report_rule_drill_shows_hotspots_hides_by_rule():
    fires = [
        _fire("s", "2026-07-19", "edit_loop", "warn", "/x/a.py"),
        _fire("s", "2026-07-19", "edit_loop", "warn", "/x/b.py"),
    ]
    out = rpt.format_report(fires, rule="edit_loop")
    assert "rule: edit_loop" in out
    assert "FILE HOTSPOTS" in out
    assert "/x/a.py" in out
    assert "BY RULE" not in out  # by-rule table suppressed in a single-rule drill


def test_build_json_shape_and_rule_hotspots():
    fires = [_fire("s", "2026-07-19", "streak_4", "warn", "/x/a.py")]
    j = rpt.build_json(fires)
    assert set(j.keys()) == {"totals", "by_rule", "top_sessions"}
    j2 = rpt.build_json(fires, rule="streak_4")
    assert "file_hotspots" in j2 and j2["file_hotspots"] == {"/x/a.py": 1}


def test_cap_top_zero_returns_all():
    fires = [_fire("s", "2026-07-19", f"r{i}") for i in range(20)]
    assert len(rpt.build_json(fires, top=0)["by_rule"]) == 20
    assert len(rpt.build_json(fires, top=5)["by_rule"]) == 5


def test_table_normalizes_mismatched_row_length():
    out = rpt._table(["A", "B", "C"], [["1", "2"], ["1", "2", "3", "4"]])
    assert out.splitlines()[0].split() == ["A", "B", "C"]
    assert "4" not in out  # long row truncated to header count, header preserved


# ---------------- robustness / consistency (review-driven) ----------------

def test_by_rule_ignores_unknown_and_total_string_actions():
    fires = [
        {"ts": "2026-07-19T12:00:00+00:00", "rule": "r", "action": "total", "session_id": "s"},
        {"ts": "2026-07-19T12:00:00+00:00", "rule": "r", "action": "weird", "session_id": "s"},
        _fire("s", "2026-07-19", "r", "info"),
    ]
    br = rpt.by_rule(fires)
    assert br["r"]["total"] == 3   # every entry counted once — action "total" must not double-count
    assert br["r"]["info"] == 1
    assert br["r"]["warn"] == 0 and br["r"]["block"] == 0


def test_report_survives_nonstring_ts():
    # a partially-written / hand-edited line with an epoch-int ts must not crash the report
    bad = {"ts": 1753000000, "rule": "r", "action": "warn", "session_id": "s"}
    assert rpt._date(bad) == ""
    assert rpt.totals([bad])["fires"] == 1                     # no TypeError in totals
    assert "COST-DISCIPLINE FIRE-LOG" in rpt.format_report([bad])
    assert rpt.filter_fires([bad], since="2026-01-01") == []   # undateable row drops from --since


def test_totals_counts_match_table_rows_with_missing_fields():
    fires = [
        _fire("s1", "2026-07-19", "r1"),
        {"ts": "2026-07-19T12:00:00+00:00", "action": "warn"},  # no rule, no session_id
    ]
    t = rpt.totals(fires)
    assert t["rules"] == len(rpt.by_rule(fires))        # both count the "?" bucket -> 2
    assert t["sessions"] == len(rpt.by_session(fires))  # -> 2


def test_format_report_shows_info_column():
    fires = [_fire("s", "2026-07-19", "workflow_suggest_rlm", "info") for _ in range(3)]
    out = rpt.format_report(fires)
    assert "INFO" in out  # info-only rules are not invisible in the BY RULE table
    row = next(ln for ln in out.splitlines() if ln.startswith("workflow_suggest_rlm"))
    assert "3" in row  # FIRES and INFO both surfaced in the rule's row


# ---------------- cli ----------------

def test_main_missing_file_returns_1(tmp_path, capsys):
    assert rpt.main(["--file", str(tmp_path / "nope.jsonl")]) == 1
    assert "No fire-log" in capsys.readouterr().err


def test_main_empty_file_returns_1(tmp_path, capsys):
    p = tmp_path / "log.jsonl"
    p.write_text("\n")  # exists but only blank -> parses to nothing
    assert rpt.main(["--file", str(p)]) == 1
    assert "empty or unparseable" in capsys.readouterr().err


def test_main_prints_report(tmp_path, capsys):
    p = tmp_path / "log.jsonl"
    _write_log(p, [_fire("s", "2026-07-19", "streak_4")])
    assert rpt.main(["--file", str(p)]) == 0
    assert "COST-DISCIPLINE FIRE-LOG" in capsys.readouterr().out


def test_main_json_output(tmp_path, capsys):
    p = tmp_path / "log.jsonl"
    _write_log(p, [_fire("s", "2026-07-19", "streak_4")])
    assert rpt.main(["--file", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["fires"] == 1
