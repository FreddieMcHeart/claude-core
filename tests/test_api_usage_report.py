"""Tests for lib/api_usage_report.py — real API token usage per rate-limit window."""
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "api_usage_report.py"
sys.path.insert(0, str(MOD.parent))  # so the module's `from _report_table import ...` resolves
spec = importlib.util.spec_from_file_location("api_usage_report", MOD)
rpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpt)

NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)


def _usage(inp=0, out=0, cread=0, cwrite=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cread,
        "cache_creation_input_tokens": cwrite,
    }


def _record(ts, model="claude-opus-5", usage=None, rtype="assistant"):
    """One transcript line in the real shape: usage nested under `message`."""
    return {
        "type": rtype,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {"model": model, "usage": usage if usage is not None else _usage(out=10)},
    }


def _transcript(dirp, session_id, records, project="proj"):
    """Write a real .jsonl under a real project subdir — scan() globs `*/*.jsonl`."""
    proj = dirp / project
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


# ---------------------------------------------------------------- extraction

def test_usage_tokens_takes_iterations_when_top_level_is_zero():
    usage = dict(_usage(), iterations=[_usage(out=3733, cread=554810)])
    fields, multi = rpt.usage_tokens(usage)
    assert fields["output_tokens"] == 3733
    assert fields["cache_read_input_tokens"] == 554810
    assert multi is False


def test_usage_tokens_never_adds_equal_sources():
    """The measured case: both present and equal. Adding would double every record."""
    usage = dict(_usage(inp=5, out=7), iterations=[_usage(inp=5, out=7)])
    fields, _ = rpt.usage_tokens(usage)
    assert fields["input_tokens"] == 5
    assert fields["output_tokens"] == 7


def test_usage_tokens_keeps_top_level_when_it_is_larger():
    usage = dict(_usage(out=100), iterations=[_usage(out=1)])
    fields, _ = rpt.usage_tokens(usage)
    assert fields["output_tokens"] == 100


def test_usage_tokens_flags_more_than_one_iteration():
    """An unobserved shape must be surfaced, not silently absorbed."""
    usage = dict(_usage(), iterations=[_usage(out=1), _usage(out=2)])
    fields, multi = rpt.usage_tokens(usage)
    assert multi is True
    assert fields["output_tokens"] == 3


def test_usage_tokens_coerces_non_numeric_and_bools_to_zero():
    usage = {"input_tokens": "big", "output_tokens": True,
             "cache_read_input_tokens": None, "cache_creation_input_tokens": 4}
    fields, _ = rpt.usage_tokens(usage)
    assert fields == {"input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 4}


def test_usage_tokens_on_a_non_dict_returns_zeros():
    fields, multi = rpt.usage_tokens("nonsense")
    assert sum(fields.values()) == 0
    assert multi is False


# ---------------------------------------------------------------- scan_file

def test_scan_file_counts_unparseable_lines_rather_than_dropping_them(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(_record(NOW)) + "\n{not json\n")
    res = rpt.scan_file(path, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert res["parse_errors"] == 1
    assert res["in_window"] == 1


def test_scan_file_read_error_is_distinct_from_nothing_in_window(tmp_path):
    """`could not look` and `looked and found nothing` must not render the same."""
    missing = rpt.scan_file(tmp_path / "absent.jsonl", NOW, NOW)
    assert missing["read_error"] is not None
    assert missing["in_window"] == 0

    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    looked = rpt.scan_file(empty, NOW, NOW)
    assert looked["read_error"] is None
    assert looked["in_window"] == 0


def test_scan_file_excludes_records_outside_the_window(tmp_path):
    path = _transcript(tmp_path, "s", [
        _record(NOW - timedelta(hours=9), usage=_usage(out=1)),
        _record(NOW - timedelta(hours=1), usage=_usage(out=2)),
        _record(NOW + timedelta(hours=9), usage=_usage(out=4)),
    ])
    res = rpt.scan_file(path, NOW - timedelta(hours=5), NOW)
    assert res["in_window"] == 1
    assert res["tokens"]["output_tokens"] == 2


def test_scan_file_counts_records_without_a_timestamp(tmp_path):
    path = tmp_path / "s.jsonl"
    rec = _record(NOW)
    del rec["timestamp"]
    path.write_text(json.dumps(rec) + "\n")
    res = rpt.scan_file(path, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert res["no_timestamp"] == 1
    assert res["in_window"] == 0


def test_scan_file_ignores_non_assistant_records(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(_record(NOW, rtype="user")) + "\n")
    res = rpt.scan_file(path, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert res["records"] == 0


def test_scan_file_splits_by_model(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        _record(NOW, model="claude-opus-5", usage=_usage(out=3)),
        _record(NOW, model="claude-sonnet-5", usage=_usage(out=5)),
    ]) + "\n")
    res = rpt.scan_file(path, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert res["by_model"]["claude-opus-5"]["output_tokens"] == 3
    assert res["by_model"]["claude-sonnet-5"]["output_tokens"] == 5


# ---------------------------------------------------------------- scan

def test_scan_skips_files_older_than_the_window_and_reports_the_count(tmp_path):
    old = _transcript(tmp_path, "old", [_record(NOW, usage=_usage(out=99))])
    stale = (NOW - timedelta(days=30)).timestamp()
    os.utime(old, (stale, stale))
    _transcript(tmp_path, "fresh", [_record(NOW, usage=_usage(out=7))])

    sessions, stats = rpt.scan(tmp_path, NOW - timedelta(hours=5), NOW + timedelta(hours=1))
    assert "old" not in sessions
    assert stats["files_skipped_mtime"] == 1
    assert stats["files_seen"] == 2
    assert sessions["fresh"]["tokens"]["output_tokens"] == 7


def test_scan_omits_sessions_with_nothing_in_the_window(tmp_path):
    _transcript(tmp_path, "quiet", [_record(NOW - timedelta(hours=1), usage=_usage(out=1))])
    sessions, _ = rpt.scan(tmp_path, NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    assert sessions == {}


def test_scan_propagates_the_multi_iteration_count(tmp_path):
    usage = dict(_usage(), iterations=[_usage(out=1), _usage(out=2)])
    _transcript(tmp_path, "s", [_record(NOW, usage=usage)])
    _, stats = rpt.scan(tmp_path, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert stats["multi_iteration"] == 1


# ---------------------------------------------------------------- windows

def test_resolve_window_anchors_on_resets_at():
    resets = NOW + timedelta(hours=2)
    windows = {"five_hour": {"resets_at": resets.timestamp(), "used_percentage": 40}}
    since, until, source = rpt.resolve_window("5h", windows, now=NOW)
    assert source == "resets_at"
    assert until == resets
    assert since == resets - timedelta(hours=5)


def test_resolve_window_falls_back_to_now_and_names_the_fallback():
    since, until, source = rpt.resolve_window("7d", {}, now=NOW)
    assert source == "now"
    assert until == NOW
    assert since == NOW - timedelta(days=7)


def test_resolve_window_rejects_a_bool_resets_at():
    windows = {"five_hour": {"resets_at": True}}
    _, _, source = rpt.resolve_window("5h", windows, now=NOW)
    assert source == "now"


def test_load_windows_returns_empty_on_missing_or_malformed(tmp_path):
    assert rpt.load_windows(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert rpt.load_windows(bad) == {}
    wrong_shape = tmp_path / "list.json"
    wrong_shape.write_text("[1, 2]")
    assert rpt.load_windows(wrong_shape) == {}


# ---------------------------------------------------------------- rollups

def _session(sid, total_out, first=None, last=None):
    return {
        "session_id": sid,
        "project": "p",
        "records": 1,
        "tokens": _usage(out=total_out),
        "by_model": {"m": _usage(out=total_out)},
        "first_ts": first,
        "last_ts": last,
    }


def test_session_rows_share_of_limit_needs_no_limit():
    sessions = {"a": _session("a", 75), "b": _session("b", 25)}
    rows = rpt.session_rows(sessions, 100, used_percentage=40)
    assert rows[0]["session_id"] == "a"
    assert rows[0]["pct_points"] == 30.0   # 40% of the window, 75% of it is this session
    assert rows[1]["pct_points"] == 10.0


def test_session_rows_pct_points_is_none_without_a_percentage():
    rows = rpt.session_rows({"a": _session("a", 10)}, 10, used_percentage=None)
    assert rows[0]["pct_points"] is None


def test_session_rows_survives_a_zero_window_total():
    rows = rpt.session_rows({"a": _session("a", 0)}, 0, used_percentage=50)
    assert rows[0]["share"] == 0.0


def test_rate_is_none_when_the_span_is_zero():
    sessions = {"a": _session("a", 100, first=NOW, last=NOW)}
    rows = rpt.session_rows(sessions, 100)
    assert rows[0]["tok_per_min"] is None


def test_rate_uses_the_sessions_own_span_not_the_window():
    sessions = {"a": _session("a", 120, first=NOW, last=NOW + timedelta(minutes=2))}
    rows = rpt.session_rows(sessions, 120)
    assert rows[0]["tok_per_min"] == 60.0


def test_by_model_merges_across_sessions():
    sessions = {"a": _session("a", 3), "b": _session("b", 4)}
    assert rpt.by_model(sessions)["m"]["output_tokens"] == 7


# ---------------------------------------------------------------- output

def test_format_report_names_the_fallback_boundary():
    payload = rpt.build_json("5h", {}, {"files_seen": 0, "files_skipped_mtime": 0,
                                        "files_unreadable": 0, "parse_errors": 0,
                                        "multi_iteration": 0, "no_timestamp": 0},
                             NOW - timedelta(hours=5), NOW, "now", None)
    text = rpt.format_report(payload)
    assert "window ends now rather than at the real reset" in text
    assert "share-of-limit is not computed" in text


def test_format_report_surfaces_the_unobserved_iteration_shape():
    payload = rpt.build_json("5h", {}, {"files_seen": 1, "files_skipped_mtime": 0,
                                        "files_unreadable": 0, "parse_errors": 0,
                                        "multi_iteration": 2, "no_timestamp": 0},
                             NOW - timedelta(hours=5), NOW, "resets_at", 40)
    assert "more than one usage iteration" in rpt.format_report(payload)


def test_main_missing_projects_dir_returns_1(tmp_path, capsys):
    rc = rpt.main(["--projects", str(tmp_path / "nope")])
    assert rc == 1
    assert "No transcripts directory" in capsys.readouterr().err


def test_main_json_round_trip_over_a_real_transcript(tmp_path, capsys):
    """Drives the real reader over a real file on disk, not a stubbed scan."""
    now = datetime.now(UTC)
    projects = tmp_path / "projects"
    _transcript(projects, "sess-1", [
        _record(now - timedelta(minutes=1), usage=_usage(inp=10, out=20, cread=30, cwrite=40)),
    ])
    windows = tmp_path / "rate-limits.json"
    windows.write_text(json.dumps({
        "five_hour": {"used_percentage": 50,
                      "resets_at": (now + timedelta(hours=1)).timestamp()},
    }))

    rc = rpt.main(["--projects", str(projects), "--windows-file", str(windows), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_tokens"] == 100
    assert payload["window"]["boundary_source"] == "resets_at"
    assert payload["window"]["used_percentage"] == 50
    assert payload["sessions"][0]["pct_points"] == 50.0
    assert payload["totals"]["cache_creation_input_tokens"] == 40


def test_main_text_report_runs_over_a_real_transcript(tmp_path, capsys):
    now = datetime.now(UTC)
    projects = tmp_path / "projects"
    _transcript(projects, "sess-1", [_record(now, usage=_usage(out=5))])
    rc = rpt.main(["--projects", str(projects), "--windows-file", str(tmp_path / "absent.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "API token usage" in out
    assert "sess-1"[:8] in out
