"""Tests for lib/cost_ledger_report.py — the cross-session cost-ledger summary."""
import importlib.util
import json
import sys
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "cost_ledger_report.py"
sys.path.insert(0, str(MOD.parent))  # so the module's `from _report_table import ...` resolves
spec = importlib.util.spec_from_file_location("cost_ledger_report", MOD)
rpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpt)

# The real writer, imported the same way test_cost_ledger.py imports it — for the
# round-trip test below (drives the real build_cost_ledger into the real reader).
CD_MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
cd_spec = importlib.util.spec_from_file_location("cost_discipline_for_report_test", CD_MOD)
cd = importlib.util.module_from_spec(cd_spec)
cd_spec.loader.exec_module(cd)


def _led(sid, date, model="opus", calls=10, metered=8, chars=30000,
         tokens=8500, aggr=2, usd=1.5, by_tool=None):
    """Build a ledger in the NEW (segments/totals) schema — matching the real
    ``build_cost_ledger`` output shape, not a fixture that quietly encodes the
    old flat schema the reader used to expect."""
    return {
        "session_id": sid,
        "started_at": f"{date}T10:00:00+00:00",
        "updated_at": f"{date}T12:00:00+00:00",
        "main_model": model,
        "segments": [],
        "totals": {
            "tool_calls_total": calls,
            "metered_results": metered,
            "tool_result_chars": chars,
            "tool_result_tokens_est": tokens,
            "cache_reread_usd_per_turn_est": usd,
            "aggregate_reads": aggr,
            "tool_result_chars_by_tool": by_tool or {"Read": {"chars": chars, "tokens": tokens}},
        },
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


# ---------------- robustness: malformed fields don't crash the whole report ----------------

def test_report_survives_null_numeric_field(tmp_path, capsys):
    # one interrupted-mid-write ledger with null fields, next to a good one.
    # Nulls land inside `totals` (not top-level) — that's where the real
    # writer's metric fields live in the new schema, and `_ledger_view` merges
    # `totals` over the top level, so a top-level-only null would be shadowed
    # by the (numeric) totals value and never actually exercise the null path.
    good = _led("good", "2026-07-19")
    bad = _led("bad", "2026-07-19")
    bad["totals"]["tool_calls_total"] = None
    bad["totals"]["cache_reread_usd_per_turn_est"] = None
    bad["cwd"] = None  # a real ledger can carry cwd:null (session cwd not captured)
    _write(tmp_path, good)
    _write(tmp_path, bad)
    ledgers = rpt.load_ledgers(tmp_path)
    # aggregation treats the bad field as 0 rather than raising TypeError
    assert rpt.totals(ledgers)["tool_calls_total"] == good["totals"]["tool_calls_total"]
    # and the CLI renders both sessions without an unhandled traceback
    assert rpt.main(["--dir", str(tmp_path)]) == 0
    assert "COST LEDGER" in capsys.readouterr().out


def test_by_tool_rollup_coerces_nonnumeric_values():
    ledgers = [
        _led("a", "2026-07-19", by_tool={"Read": "500", "Bash": {"chars": None, "tokens": 10}}),
        _led("b", "2026-07-19", by_tool={"Read": {"chars": 100, "tokens": 30}}),
    ]
    roll = rpt.by_tool_rollup(ledgers)  # must not raise
    assert roll["Read"]["chars"] == 100  # the bare string "500" coerces to 0
    assert roll["Bash"]["chars"] == 0    # null chars coerces to 0
    assert roll["Bash"]["tokens"] == 10


def test_format_report_tolerates_nonnumeric_dollar_field():
    bad = _led("bad", "2026-07-19")
    bad["totals"]["cache_reread_usd_per_turn_est"] = "n/a"  # lives in totals, not top-level
    out = rpt.format_report([bad])  # the $/TURN :.2f format must not raise
    assert "0.00" in out


def test_table_normalizes_mismatched_row_length():
    out = rpt._table(["A", "B", "C"], [["1", "2"], ["1", "2", "3", "4"]])
    lines = out.splitlines()
    assert lines[0].split() == ["A", "B", "C"]  # all headers survive a short row
    assert "4" not in out  # long row truncated to header count, not spilled


def test_format_report_labels_lifetime_vs_since_compaction():
    out = rpt.format_report([_led("a", "2026-07-19")])
    assert "(lifetime)" in out  # tool calls
    assert "since last compaction" in out
    # aggregate reads is NOT lifetime — the hook resets it on dispatch/compaction
    assert "CALLS/AGGR = lifetime" not in out
    aggr_line = next(ln for ln in out.splitlines() if ln.startswith("aggregate reads"))
    assert "lifetime" not in aggr_line and "dispatch/compaction" in aggr_line


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


# ---------------- _ledger_view: schema normalization ----------------
#
# This is the primary deliverable: a test that would have caught the original
# defect. That defect was never a bug in `totals()`/`by_tool_rollup()`/etc. —
# it was that the real writer (`build_cost_ledger`) moved every metric under
# `totals`, and the reader kept reading the old flat top level, so every read
# silently coerced to 0/None. A hand-built fixture that ALSO encodes the flat
# schema can never catch that; only driving the real writer into the real
# reader can. See `test_round_trip_real_ledger_is_read_correctly` below.

def test_ledger_view_merges_new_schema_totals_to_top_level():
    led = {"session_id": "x", "totals": {"tool_calls_total": 7, "metered_results": 3}}
    view = rpt._ledger_view(led)
    assert view["tool_calls_total"] == 7
    assert view["metered_results"] == 3
    assert view["session_id"] == "x"  # non-metric top-level fields survive too


def test_ledger_view_leaves_old_flat_schema_unchanged():
    """The one place a hand-built old-schema fixture is correct to use: this is
    deliberately testing backward compatibility with a real pre-migration ledger
    file on disk, not the current writer's output shape."""
    old = {
        "session_id": "legacy",
        "started_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T01:00:00+00:00",
        "main_model": "opus",
        "tool_calls_total": 12,
        "metered_results": 9,
        "tool_result_chars": 40000,
        "tool_result_tokens_est": 11428,
        "cache_reread_usd_per_turn_est": 0.26,
        "aggregate_reads": 4,
        "tool_result_chars_by_tool": {"Read": {"chars": 40000, "tokens": 11428}},
    }
    view = rpt._ledger_view(old)
    assert view == old  # no `totals` key -> merge is a no-op, nothing changes
    # Fields with NO old-schema equivalent must be ABSENT, never defaulted to
    # zero/empty — a zero here would be a measurement claim about a session that
    # predates the field existing.
    assert "segment_count" not in view
    assert "dispatches" not in view
    assert "result_bytes_buckets" not in view
    # And the old-schema ledger reads correctly end to end through the real
    # aggregation functions, same as it always did.
    assert rpt.totals([old])["tool_calls_total"] == 12
    assert not rpt.is_zero_session(old)


def test_round_trip_real_ledger_is_read_correctly():
    """THE deliverable. Drives the REAL build_cost_ledger (hooks/cost-discipline.py)
    into the REAL reader functions — not a fixture that quietly encodes the
    schema the reader expects. This is the test whose absence let the original
    defect (report reads the new totals-nested ledger as all zeros) through 192+
    green tests; it must never again be satisfiable by a hand-built dict.
    """
    state = cd.new_state("round-trip-1")
    state["cwd"] = "/repo/round-trip"
    state["started_at"] = "2026-07-30T00:00:00+00:00"
    state["tool_calls_total"] = 14
    state["metered_results"] = 11
    state["tool_result_chars"] = 77_000
    state["tool_result_chars_by_tool"] = {"Read": 60_000, "Bash": 17_000}
    state["aggregate_reads"] = 6

    led = cd.build_cost_ledger(state)
    # Sanity: this really is the new totals-nested shape, not the old flat one.
    assert "totals" in led
    assert "tool_calls_total" not in led

    # _ledger_view flattens it correctly.
    view = rpt._ledger_view(led)
    assert view["tool_calls_total"] == 14
    assert view["tool_result_chars"] == 77_000

    # is_zero_session must recognise this as an ACTIVE session, not a zero one.
    assert not rpt.is_zero_session(led)

    # totals() aggregates real, non-zero numbers from the real ledger.
    t = rpt.totals([led])
    assert t["tool_calls_total"] == 14
    assert t["metered_results"] == 11
    assert t["result_chars"] == 77_000
    assert t["result_tokens_est"] == led["totals"]["tool_result_tokens_est"]
    assert t["aggregate_reads"] == 6

    # by_tool_rollup sees the real per-tool breakdown, not an empty rollup.
    rollup = rpt.by_tool_rollup([led])
    assert rollup["Read"]["chars"] == 60_000
    assert rollup["Bash"]["chars"] == 17_000

    # The per-session row path (format_report/build_json) renders real values,
    # not a silently-zeroed row.
    out = rpt.format_report([led])
    assert "COST LEDGER" in out
    assert "no active sessions" not in out.lower()
    row = next(ln for ln in out.splitlines() if "round-trip" in ln)
    assert "14" in row  # CALLS column shows the real call count, not 0

    j = rpt.build_json([led])
    assert j["totals"]["tool_calls_total"] == 14
    assert j["sessions"][0]["tool_calls_total"] == 14  # per_session_rows() output is flattened too
