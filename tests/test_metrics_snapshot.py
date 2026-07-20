"""Tests for lib/metrics_snapshot.py — cohort baseline capture/compare."""
import importlib.util
import json
import sys
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "metrics_snapshot.py"
sys.path.insert(0, str(MOD.parent))  # so its `import cost_ledger_report` etc. resolve
spec = importlib.util.spec_from_file_location("metrics_snapshot", MOD)
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


def _led(sid, date, tokens=8500, calls=10, metered=8, aggr=2, usd=1.5, chars=30000):
    return {
        "session_id": sid,
        "updated_at": f"{date}T12:00:00+00:00",
        "main_model": "opus",
        "tool_calls_total": calls,
        "metered_results": metered,
        "tool_result_chars": chars,
        "tool_result_tokens_est": tokens,
        "cache_reread_usd_per_turn_est": usd,
        "aggregate_reads": aggr,
        "tool_result_chars_by_tool": {"Read": {"chars": chars, "tokens": tokens}},
    }


def _fire(sid, date, rule, action="warn"):
    return {"ts": f"{date}T12:00:00+00:00", "rule": rule, "action": action, "session_id": sid}


# ---------------- helpers ----------------

def test_in_window():
    assert ms._in_window("2026-07-19", "2026-07-15", None)
    assert not ms._in_window("2026-07-10", "2026-07-15", None)
    assert not ms._in_window("2026-07-20", None, "2026-07-19")
    assert ms._in_window("", None, None)           # undateable passes when no lower bound
    assert not ms._in_window("", "2026-07-15", None)


# ---------------- metrics ----------------

def test_ledger_metrics_per_session():
    leds = [
        _led("a", "2026-07-19", tokens=100, calls=10, usd=1.0),
        _led("b", "2026-07-19", tokens=300, calls=20, usd=3.0),
    ]
    m = ms.ledger_metrics(leds)
    assert m["n_sessions"] == 2
    assert m["result_tokens_est"]["total"] == 400
    assert m["result_tokens_est"]["per_session_mean"] == 200
    assert m["result_tokens_est"]["per_session_median"] == 200
    assert m["tool_calls_per_session_mean"] == 15
    assert m["cache_reread_usd_per_turn"]["mean"] == 2.0


def test_ledger_metrics_excludes_zero_sessions():
    leds = [_led("a", "2026-07-19", metered=8), _led("z", "2026-07-19", metered=0, chars=0, tokens=0)]
    assert ms.ledger_metrics(leds)["n_sessions"] == 1  # zero-activity session dropped


def test_ledger_metrics_tolerates_null_field():
    bad = _led("a", "2026-07-19")
    bad["tool_result_tokens_est"] = None  # partial/interrupted ledger must not crash
    assert ms.ledger_metrics([bad])["result_tokens_est"]["total"] == 0


def test_firelog_metrics():
    fires = [
        _fire("s1", "2026-07-19", "aggregate_15"),
        _fire("s1", "2026-07-19", "block_read_streak", "block"),
        _fire("s2", "2026-07-19", "aggregate_15"),
    ]
    m = ms.firelog_metrics(fires)
    assert m["n_fires"] == 3
    assert m["n_sessions_with_fires"] == 2
    assert m["fires_per_session_mean"] == 1.5
    assert m["block_rate"] == round(1 / 3, 3)
    assert m["volume_rule_fires_per_session"]["aggregate_15"] == 1.0  # 2 fires / 2 sessions


# ---------------- snapshot / windowing ----------------

def test_build_snapshot_windows_both_sources():
    leds = [_led("old", "2026-07-10"), _led("new", "2026-07-19")]
    fires = [_fire("old", "2026-07-10", "streak_4"), _fire("new", "2026-07-19", "streak_4")]
    snap = ms.build_snapshot("t", leds, fires, since="2026-07-15", captured_at="2026-07-20T00:00:00Z")
    assert snap["ledger"]["n_sessions"] == 1   # only 'new' inside the window
    assert snap["fire_log"]["n_fires"] == 1
    assert snap["window"]["since"] == "2026-07-15"
    assert snap["label"] == "t"


# ---------------- compare ----------------

def test_format_compare_has_rows_and_delta():
    a = ms.build_snapshot("pre", [_led("a", "2026-07-19", tokens=200)], [_fire("a", "2026-07-19", "streak_4")])
    b = ms.build_snapshot("post", [_led("b", "2026-07-19", tokens=100)], [_fire("b", "2026-07-19", "streak_4")])
    out = ms.format_compare(a, b)
    assert "COMPARE" in out and "pre" in out and "post" in out
    row = next(ln for ln in out.splitlines() if "tokens/sess mean" in ln)
    assert "200" in row and "100" in row  # the drop is visible in the row


# ---------------- cli ----------------

def test_capture_compare_list_cli(tmp_path, capsys):
    ldir = tmp_path / "ledger"
    ldir.mkdir()
    (ldir / "a.json").write_text(json.dumps(_led("a", "2026-07-19", tokens=300)))
    flog = tmp_path / "fire.jsonl"
    flog.write_text(json.dumps(_fire("a", "2026-07-19", "aggregate_15")) + "\n")
    out = tmp_path / "baselines"

    rc = ms.main(["capture", "--label", "pre-ccm-lite", "--ledger-dir", str(ldir),
                  "--fire-log", str(flog), "--out", str(out)])
    assert rc == 0
    assert (out / "pre-ccm-lite.json").exists()
    assert "captured 'pre-ccm-lite'" in capsys.readouterr().out

    assert ms.main(["list", "--out", str(out)]) == 0
    assert "pre-ccm-lite" in capsys.readouterr().out

    p = str(out / "pre-ccm-lite.json")
    assert ms.main(["compare", p, p]) == 0
    assert "COMPARE" in capsys.readouterr().out


def test_capture_no_data_returns_1(tmp_path, capsys):
    rc = ms.main(["capture", "--label", "x", "--ledger-dir", str(tmp_path / "none"),
                  "--fire-log", str(tmp_path / "none.jsonl"), "--out", str(tmp_path / "b")])
    assert rc == 1
    assert "No ledger or fire-log data" in capsys.readouterr().err
