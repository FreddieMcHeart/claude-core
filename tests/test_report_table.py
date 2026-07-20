"""Tests for lib/_report_table.py — the shared fixed-width table renderer."""
import importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "_report_table.py"
spec = importlib.util.spec_from_file_location("_report_table", MOD)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)


def test_basic_render_has_header_rule_and_rows():
    out = rt.table(["A", "B"], [["1", "2"], ["3", "4"]])
    lines = out.splitlines()
    assert lines[0].split() == ["A", "B"]
    assert set(lines[1]) <= {"-", " "}  # separator rule row
    assert lines[2].split() == ["1", "2"]
    assert lines[3].split() == ["3", "4"]


def test_normalizes_short_and_long_rows_without_dropping_headers():
    out = rt.table(["A", "B", "C"], [["1", "2"], ["1", "2", "3", "4"]])
    assert out.splitlines()[0].split() == ["A", "B", "C"]  # short row keeps all headers
    assert "4" not in out                                   # long row truncated to header count


def test_empty_rows_render_header_and_rule_only():
    lines = rt.table(["A", "B"], []).splitlines()
    assert lines[0].split() == ["A", "B"]
    assert len(lines) == 2  # header + separator, no data rows


def test_alignment_argument_is_accepted():
    out = rt.table(["N"], [["1"]], aligns=[">"])
    assert "N" in out and "1" in out
