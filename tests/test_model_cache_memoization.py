"""What _EXPENSIVE_MODEL_CACHE's 60s TTL actually buys, and what it doesn't.

First tests for this path (2026-07-29). The docstrings used to say "cached for
60s" as if the cache survived across hook events; it can't, because each event
is a fresh process and the cache is a plain module-level dict reset to None on
import. Two properties are both true and both worth pinning:

  1. WITHIN one invocation, several call sites share the cache (is_expensive_
     main_model, get_main_model_name, get_main_effort_level — all used from
     different points during a single handle_pre_tool/handle_post_tool run), so
     settings.json is read at most once per event no matter how many of them run.
  2. ACROSS invocations the TTL provides zero benefit: a brand-new process starts
     the cache back at None regardless of how little wall-clock time passed, so
     two events a millisecond apart each still pay a full settings.json read.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_cache", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _reset_cache():
    cd._EXPENSIVE_MODEL_CACHE["value"] = None
    cd._EXPENSIVE_MODEL_CACHE["name"] = None
    cd._EXPENSIVE_MODEL_CACHE["effort"] = None
    cd._EXPENSIVE_MODEL_CACHE["checked_at"] = 0.0


def _fake_settings(monkeypatch, tmp_path, **fields):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings_file = claude_dir / "settings.json"
    settings_file.write_text(json.dumps(fields))
    monkeypatch.setattr(cd.Path, "home", lambda: tmp_path)

    reads = {"n": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *a, **k):
        if self == settings_file:
            reads["n"] += 1
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(cd.Path, "read_text", counting_read_text)
    return reads


def test_cache_memoizes_across_sibling_readers_within_one_invocation(monkeypatch, tmp_path):
    _reset_cache()
    reads = _fake_settings(monkeypatch, tmp_path, model="claude-opus-5", effortLevel="high")

    assert cd.is_expensive_main_model() is True
    assert cd.get_main_model_name() == "opus"
    assert cd.get_main_effort_level() == "high"

    assert reads["n"] == 1, (
        "three distinct call sites shared one cache within this invocation — "
        "settings.json must be read exactly once. This is the real property the "
        "60s TTL provides; it is not cross-invocation persistence."
    )


def test_cache_gives_zero_benefit_across_separate_invocations(monkeypatch, tmp_path):
    """Each hook event is a fresh process, so the module-level dict starts back
    at None every time — the 60s TTL never actually gets a chance to elapse or
    not elapse across events, because there is no cache left to check."""
    reads = _fake_settings(monkeypatch, tmp_path, model="claude-opus-5")

    _reset_cache()
    assert cd.is_expensive_main_model() is True

    _reset_cache()  # simulates the next hook event: a brand-new process
    assert cd.is_expensive_main_model() is True

    assert reads["n"] == 2, (
        "a fresh process re-reads settings.json even though less than 60s "
        "elapsed between the two calls — the TTL only ever mattered inside a "
        "single process's lifetime"
    )
