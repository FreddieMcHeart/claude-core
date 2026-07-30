"""The cost ledger as segments: what survives a reset, and what must not double-count.

The defect these pin is LOSS, not multiplication. `write_cost_ledger` always did an
atomic full overwrite of a per-session snapshot derived from running totals, so it was
already idempotent — nothing summed on write. What it could not do was survive a
boundary: state resets at SessionStart while the ledger file persists, so the next write
overwrote the file with the smaller post-reset totals and everything before the boundary
was gone.

That distinction decides which test is the gate. An idempotence test is NOT the gate: it
passes before the fix and after it, because an atomic overwrite is idempotent by
construction. It is a true-property test, not a regression guard. The gate is
`test_totals_survive_a_session_start_reset` — that is the one that goes red on revert.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_segments", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _ledger_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cd, "COST_LEDGER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(cd, "get_main_model_name", lambda: "opus")


def _arc(session_id, *, calls, chars, results, reads=0, base=0,
         by_tool=None, dispatches=None, buckets=None):
    """A state dict standing for one session arc's running totals."""
    st = cd.new_state(session_id)
    st.update({
        "tool_calls_total": calls,
        "segment_base_tool_calls": base,
        "tool_result_chars": chars,
        "metered_results": results,
        "aggregate_reads": reads,
        "tool_result_chars_by_tool": by_tool or {},
        "dispatches_by_type": dispatches or {},
        "result_bytes_buckets": buckets or {},
        "started_at": "2026-07-30T00:00:00+00:00",
        "cwd": "/repo",
    })
    return st


def _on_disk(session_id):
    return json.loads(cd.cost_ledger_path(session_id).read_text())


# ---------------- THE GATE ----------------

def test_totals_survive_a_session_start_reset(monkeypatch, tmp_path):
    """THE regression guard. Revert the fix in place and this is the test that goes red.

    Arc 1 does real work. SessionStart then wipes /tmp state while the ledger file
    survives. Before segments, the next write replaced the file with arc 2's smaller
    numbers and arc 1 was unrecoverable — indistinguishable from "the session was quiet".
    """
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-reset"

    # Arc 1: 40 calls, 400k chars.
    cd.write_cost_ledger(_arc(sid, calls=40, chars=400_000, results=40), force=True)
    assert _on_disk(sid)["totals"]["tool_result_chars"] == 400_000

    # SessionStart: state is reset to zero, THEN a new segment is opened.
    fresh = cd.new_state(sid)
    fresh["cwd"] = "/repo"
    cd.open_cost_ledger_segment(fresh)

    # Arc 2 does a little work and writes.
    cd.write_cost_ledger(_arc(sid, calls=3, chars=1_000, results=3), force=True)

    led = _on_disk(sid)
    assert len(led["segments"]) == 2, "the boundary must open a second segment"
    assert led["totals"]["tool_result_chars"] == 401_000, (
        "arc 1 was overwritten — this is the defect segments exist to fix")
    assert led["totals"]["tool_calls_total"] == 43
    assert led["totals"]["segment_count"] == 2


# ---------------- properties 1 and 3 ----------------

def test_repeat_write_is_idempotent_on_totals_excluding_updated_at(monkeypatch, tmp_path):
    """Property 1. Named for what it compares ON PURPOSE: build_cost_ledger stamps
    `updated_at` from datetime.now(), so a byte or whole-dict comparison would fail on
    the timestamp — i.e. fail for the wrong reason, which is worse than no test.

    This property already held before the fix (atomic overwrite is idempotent by
    construction). It is here to stay true, not to catch a regression.
    """
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-idem"
    st = _arc(sid, calls=10, chars=5_000, results=10)

    cd.write_cost_ledger(st, force=True)
    first = _on_disk(sid)
    cd.write_cost_ledger(st, force=True)
    second = _on_disk(sid)

    assert first["totals"] == second["totals"]
    assert len(first["segments"]) == len(second["segments"]) == 1
    assert first["updated_at"] != second["updated_at"] or True  # timestamp is excluded


def test_every_total_is_reconstructible_from_the_stored_segments_alone(monkeypatch, tmp_path):
    """Property 3. If a stored total cannot be re-derived from the segments beside it,
    it is a second source of truth and will drift from the first."""
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-derive"
    cd.write_cost_ledger(
        _arc(sid, calls=7, chars=70_000, results=7, reads=4,
             by_tool={"Read": 50_000, "Bash": 20_000},
             dispatches={"gh-reader": 2}, buckets={"16384": 3}),
        force=True)
    fresh = cd.new_state(sid)
    cd.open_cost_ledger_segment(fresh)
    cd.write_cost_ledger(
        _arc(sid, calls=2, chars=3_000, results=2, reads=1,
             by_tool={"Read": 3_000}, dispatches={"gh-reader": 1, "slack-reader": 5},
             buckets={"1024": 2, "16384": 1}),
        force=True)

    led = _on_disk(sid)
    assert led["totals"] == cd._sum_segments(led["segments"]), (
        "totals must be a pure function of the stored segments")
    assert led["totals"]["tool_result_chars_by_tool"]["Read"]["chars"] == 53_000
    assert led["totals"]["dispatches"] == {"gh-reader": 3, "slack-reader": 5}
    assert led["totals"]["result_bytes_buckets"] == {"1024": 2, "16384": 4}


def test_empty_segments_are_not_suppressed(monkeypatch, tmp_path):
    """A session that started and did nothing is a real observation. Filtering it to
    keep the file tidy would be a vacuity trap one layer up."""
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-empty"
    cd.write_cost_ledger(_arc(sid, calls=5, chars=100, results=5), force=True)
    cd.open_cost_ledger_segment(cd.new_state(sid))
    led = _on_disk(sid)
    assert len(led["segments"]) == 2
    assert led["segments"][-1]["tool_calls_total"] == 0
    assert led["totals"]["segment_count"] == 2


# ---------------- the monotonic-counter trap ----------------

def test_compaction_does_not_double_count_the_monotonic_call_counter(monkeypatch, tmp_path):
    """tool_calls_total stays monotonic across compaction by design — the router nudge
    uses it as an index and PostCompact deliberately keeps it. Storing it raw in a
    segment would make the sum count every pre-compaction call twice. The segment holds
    a DELTA against segment_base_tool_calls instead.
    """
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-compact"
    cd.write_cost_ledger(_arc(sid, calls=30, chars=1_000, results=30), force=True)

    # PostCompact: counters reset, tool_calls_total is KEPT, base moves to it.
    after = _arc(sid, calls=30, chars=0, results=0, base=30)
    cd.open_cost_ledger_segment(after)
    # 5 more calls in the new arc.
    cd.write_cost_ledger(_arc(sid, calls=35, chars=500, results=5, base=30), force=True)

    led = _on_disk(sid)
    assert led["totals"]["tool_calls_total"] == 35, (
        "30 pre-compaction calls were counted twice")


# ---------------- dispatch counting, by type ----------------

def test_dispatch_counting_is_keyed_by_subagent_type(monkeypatch, tmp_path):
    """"Did this session delegate, and downward to WHAT" — a bare total cannot answer
    that, so two different types must not collapse into one number."""
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-disp"
    cd.write_cost_ledger(
        _arc(sid, calls=3, chars=10, results=3,
             dispatches={"gh-reader": 2, "general-purpose": 1}), force=True)
    assert _on_disk(sid)["totals"]["dispatches"] == {
        "gh-reader": 2, "general-purpose": 1}


def test_two_scopes_do_not_contaminate_each_others_dispatch_counts(monkeypatch, tmp_path):
    """Per-scope non-contamination for the field that actually carries scope. Bytes are
    deliberately summed-across-scopes (the metering site has no scope to attach), so
    this asserts on dispatches, which is the per-scope-meaningful figure. Asserting a
    per-scope byte split would be asserting on a fabricated number."""
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-two"
    cd.write_cost_ledger(
        _arc(sid, calls=4, chars=10, results=4, dispatches={"slack-reader": 3}),
        force=True)
    cd.open_cost_ledger_segment(cd.new_state(sid))
    cd.write_cost_ledger(
        _arc(sid, calls=1, chars=10, results=1, dispatches={"jira-reader": 1}),
        force=True)
    totals = _on_disk(sid)["totals"]["dispatches"]
    assert totals == {"slack-reader": 3, "jira-reader": 1}


# ---------------- byte buckets ----------------

def test_byte_buckets_are_bounded_and_log_scaled():
    """Bounded is the point: the state doc is re-serialised on every tool call, so the
    histogram must not grow with the call count."""
    assert cd._bytes_bucket(0) == "0"
    assert cd._bytes_bucket(255) == "0"
    assert cd._bytes_bucket(256) == "256"
    assert cd._bytes_bucket(1023) == "256"
    assert cd._bytes_bucket(65_536) == "65536"
    assert cd._bytes_bucket(10_000_000) == "1048576", "the top bucket is open-ended"
    # However many distinct sizes are seen, the key count cannot exceed the edge count.
    seen = {cd._bytes_bucket(n) for n in range(0, 2_000_000, 977)}
    assert len(seen) <= len(cd.LEDGER_BYTE_BUCKET_EDGES)


# ---------------- throttle ----------------

def test_throttle_skips_only_unchanged_writes_and_never_the_first(monkeypatch, tmp_path):
    """The throttle must not be count-based. A count-based one (every Nth metered
    result) leaves a short session holding only the zeroed segment SessionStart opened —
    a ledger reading ZEROS for a session that did work, which is the state the design
    calls indistinguishable from the bug it exists to fix.

    Skipping only UNCHANGED writes is lossless: any counter movement writes.
    """
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-throttle"
    st = _arc(sid, calls=1, chars=300, results=1)

    # First write ALWAYS lands, however small the session.
    cd.write_cost_ledger(st)
    assert cd.cost_ledger_path(sid).exists()
    assert _on_disk(sid)["totals"]["tool_result_chars"] == 300

    # Same counters again -> skipped. Detected via updated_at not moving.
    before = _on_disk(sid)["updated_at"]
    cd.write_cost_ledger(st)
    assert _on_disk(sid)["updated_at"] == before, "an unchanged write should be skipped"

    # Any counter movement writes, with no modulo to wait for.
    st["tool_result_chars"] = 350
    cd.write_cost_ledger(st)
    assert _on_disk(sid)["totals"]["tool_result_chars"] == 350


def test_throttle_signature_survives_a_json_round_trip(monkeypatch, tmp_path):
    """The signature is stored as a list because state is persisted as JSON, where a
    tuple returns as a list. Comparing a tuple against the reloaded value would never
    match and the throttle would silently never fire — a dead optimisation that still
    looks implemented."""
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-sig"
    st = _arc(sid, calls=2, chars=100, results=2)
    cd.write_cost_ledger(st)
    reloaded = json.loads(json.dumps(st))          # what save_state/load_state do
    before = _on_disk(sid)["updated_at"]
    cd.write_cost_ledger(reloaded)
    assert _on_disk(sid)["updated_at"] == before, (
        "throttle failed to recognise its own signature after a JSON round trip")


def test_a_missing_or_corrupt_ledger_never_raises(monkeypatch, tmp_path):
    """The ledger is best-effort by contract — it must not fail a tool call."""
    _ledger_dir(monkeypatch, tmp_path)
    sid = "s-corrupt"
    assert cd.read_cost_ledger(sid) == {}
    cd.COST_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    cd.cost_ledger_path(sid).write_text("{not json")
    assert cd.read_cost_ledger(sid) == {}
    cd.write_cost_ledger(_arc(sid, calls=1, chars=1, results=5), force=True)
    assert _on_disk(sid)["totals"]["tool_result_chars"] == 1
