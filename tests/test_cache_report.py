"""Tests for lib/cache_report.py — prompt-cache totals per session."""
import importlib.util
import json
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
_spec = importlib.util.spec_from_file_location("cache_report", LIB / "cache_report.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _usage(inp=0, out=0, read=0, write=0, w1h=None, w5m=None):
    usage = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
    }
    if w1h is not None or w5m is not None:
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": w1h or 0,
            "ephemeral_5m_input_tokens": w5m or 0,
        }
    return usage


def _line(mid, usage):
    return {"type": "assistant", "message": {"id": mid, "usage": usage}}


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def _main(root, sid, records, project="proj"):
    return _write(root / project / f"{sid}.jsonl", records)


def _sub(root, sid, name, records, project="proj"):
    return _write(root / project / sid / "subagents" / f"{name}.jsonl", records)


# ---------------------------------------------------------------- dedup

def test_repeated_id_counted_once_and_last_line_kept(tmp_path):
    # One response written as three content-block lines: usage repeats, and
    # only the last line carries the final output count.
    u1 = _usage(inp=5, out=1, read=1000, write=200, w1h=200)
    u3 = _usage(inp=5, out=40, read=1000, write=200, w1h=200)
    path = _main(tmp_path, "s1", [_line("m1", u1), _line("m1", u1), _line("m1", u3)])
    reqs, stats = cr.read_chain(path)
    assert len(reqs) == 1
    assert reqs[0]["output_tokens"] == 40
    assert reqs[0]["cache_read_input_tokens"] == 1000
    assert stats["lines"] == 3


def test_order_follows_first_appearance(tmp_path):
    path = _main(tmp_path, "s1", [
        _line("a", _usage(write=10)),
        _line("b", _usage(write=20)),
        _line("a", _usage(write=10, out=3)),
    ])
    reqs, _ = cr.read_chain(path)
    assert [r["cache_creation_input_tokens"] for r in reqs] == [10, 20]


def test_line_without_id_is_counted_not_summed(tmp_path):
    rec = {"type": "assistant", "message": {"usage": _usage(write=999)}}
    path = _main(tmp_path, "s1", [rec, _line("m1", _usage(write=5))])
    reqs, stats = cr.read_chain(path)
    assert stats["no_id"] == 1
    assert sum(r["cache_creation_input_tokens"] for r in reqs) == 5


def test_non_assistant_and_bad_json_ignored(tmp_path):
    path = tmp_path / "proj" / "s1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type": "user", "message": {"id": "x", "usage": {"input_tokens": 9}}}\n'
                    "not json\n")
    reqs, stats = cr.read_chain(path)
    assert reqs == []
    assert stats["parse_errors"] == 1


def test_unreadable_file_reports_error(tmp_path):
    reqs, stats = cr.read_chain(tmp_path / "missing.jsonl")
    assert reqs == []
    assert stats["read_error"]


# ---------------------------------------------------------------- TTL split

def test_split_by_ttl_and_unsplit_remainder(tmp_path):
    path = _main(tmp_path, "s1", [
        _line("a", _usage(write=300, w1h=100, w5m=150)),
        _line("b", _usage(write=40)),
    ])
    reqs, _ = cr.read_chain(path)
    assert (reqs[0]["write_1h"], reqs[0]["write_5m"], reqs[0]["write_unsplit"]) == (100, 150, 50)
    assert (reqs[1]["write_1h"], reqs[1]["write_5m"], reqs[1]["write_unsplit"]) == (0, 0, 40)


def test_split_larger_than_total_is_not_trusted(tmp_path):
    path = _main(tmp_path, "s1", [_line("a", _usage(write=100, w1h=80, w5m=80))])
    reqs, _ = cr.read_chain(path)
    assert (reqs[0]["write_1h"], reqs[0]["write_5m"], reqs[0]["write_unsplit"]) == (0, 0, 100)


# ---------------------------------------------------------------- weighting

def test_write_share_uses_ttl_weights():
    totals = {
        "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 1000,
        "write_1h": 50, "write_5m": 40, "write_unsplit": 0,
        "cache_creation_input_tokens": 90,
    }
    write, total = cr.weighted(totals)
    # 50*2 + 40*1.25 = 150; rest = 100 + 20*5 + 1000*0.1 = 300
    assert write == 150
    assert total == 450


def test_weights_match_cited_ratios():
    assert cr.WEIGHTS == {
        "input_tokens": 1.0, "output_tokens": 5.0, "cache_read_input_tokens": 0.1,
        "write_5m": 1.25, "write_1h": 2.0, "write_unsplit": 1.25,
    }
    assert "fc69f96" in cr.WEIGHTS_SOURCE


# ---------------------------------------------------------------- rebuilds

def test_rebuild_threshold_boundary():
    at = {"input_tokens": 0, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 50}
    below = {"input_tokens": 1, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 49}
    assert cr.is_rebuild(at)
    assert not cr.is_rebuild(below)
    assert not cr.is_rebuild({"input_tokens": 0, "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 0})


def test_first_request_is_prefix_write_not_rebuild(tmp_path):
    path = _main(tmp_path, "s1", [
        _line("a", _usage(write=90_000, w1h=90_000)),                        # first: full write
        _line("b", _usage(read=90_000, write=2_000, w1h=2_000)),             # warm
        _line("c", _usage(read=10_000, write=164_000, w1h=164_000)),         # resume: rebuild
        _line("d", _usage(read=180_000, write=57_000, w1h=57_000)),          # ratio 0.24: not
    ])
    rep = cr.session_report(path)
    assert rep["main_first_write"] == 90_000
    assert rep["main_rebuilds"] == 1
    assert rep["main_rebuild_tokens"] == 164_000


# ---------------------------------------------------------------- sessions

def test_subagents_are_read_and_kept_apart(tmp_path):
    path = _main(tmp_path, "s1", [_line("a", _usage(write=100, w1h=100))])
    _sub(tmp_path, "s1", "agent-x", [
        _line("x1", _usage(write=70, w5m=70)),                  # prefix write
        _line("x2", _usage(read=70, write=100, w5m=100)),       # rebuild
    ])
    _sub(tmp_path, "s1", "agent-y", [_line("y1", _usage(read=50))])  # no write at all
    rep = cr.session_report(path)
    assert rep["sub_agents"] == 2
    assert rep["sub_requests"] == 3
    assert rep["sub_prefix_writes"] == 1
    assert rep["sub_prefix_tokens"] == 70
    assert rep["sub_rebuilds"] == 1
    assert rep["main_rebuilds"] == 0
    assert rep["totals"]["write_1h"] == 100
    assert rep["totals"]["write_5m"] == 170
    assert rep["totals"]["cache_creation_input_tokens"] == 270


def test_other_sessions_subagents_not_mixed_in(tmp_path):
    path = _main(tmp_path, "s1", [_line("a", _usage(write=1))])
    _main(tmp_path, "s2", [_line("b", _usage(write=1))])
    _sub(tmp_path, "s2", "agent-z", [_line("z", _usage(write=500))])
    assert cr.session_report(path)["totals"]["cache_creation_input_tokens"] == 1


def test_find_sessions_skips_subagent_files_and_filters_prefix(tmp_path):
    _main(tmp_path, "abc123", [_line("a", _usage())])
    _main(tmp_path, "def456", [_line("b", _usage())])
    _sub(tmp_path, "abc123", "agent-q", [_line("q", _usage())])
    stems = sorted(p.stem for p in cr.find_sessions(tmp_path))
    assert stems == ["abc123", "def456"]
    assert [p.stem for p in cr.find_sessions(tmp_path, ["abc"])] == ["abc123"]


def test_cli_text_and_json(tmp_path, capsys):
    _main(tmp_path, "s1", [_line("a", _usage(inp=10, write=100, w1h=100))])
    assert cr.main(["--projects", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "s1" in out and "fc69f96" in out and "message.id" in out
    assert cr.main(["--projects", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["totals"]["write_1h"] == 100


def test_empty_projects_dir(tmp_path, capsys):
    assert cr.main(["--projects", str(tmp_path)]) == 0
    assert "No session transcripts found." in capsys.readouterr().out
