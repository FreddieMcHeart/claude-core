"""The session reminder must quote the LIVE reasoning effort, not a hardcoded one.

The effort parenthetical was hardcoded ("(high effort)") until 2026-07-26, so it
went stale the moment anyone ran `/effort`. These tests pin the substitution and,
more importantly, the failure modes: an unreadable or effort-less settings.json
must degrade to a neutral phrase rather than emitting a wrong number, and the
placeholder must never survive into the text a session actually reads.
"""

import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _settings(monkeypatch, tmp_path, payload):
    """Point the model/effort cache at a synthetic settings.json and clear it.

    payload=None writes no file at all, exercising the read-failure branch.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    if payload is not None:
        (home / ".claude" / "settings.json").write_text(json.dumps(payload))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(
        cd, "_EXPENSIVE_MODEL_CACHE",
        {"value": None, "name": None, "effort": None, "checked_at": 0.0},
    )


def test_effort_is_read_from_settings(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path, {"model": "opus[1m]", "effortLevel": "high"})
    assert cd.get_main_effort_level() == "high"
    assert "(high effort)" in cd.force_load_rules()


def test_effort_change_is_reflected_without_a_code_edit(monkeypatch, tmp_path):
    """The whole point of 3g: /effort xhigh must change the reminder."""
    _settings(monkeypatch, tmp_path, {"model": "opus[1m]", "effortLevel": "xhigh"})
    text = cd.force_load_rules()
    assert "(xhigh effort)" in text
    assert "(high effort)" not in text.replace("(xhigh effort)", "")


def test_missing_effort_key_degrades_to_neutral_phrase(monkeypatch, tmp_path):
    """No effortLevel set: say nothing specific rather than assert a wrong level."""
    _settings(monkeypatch, tmp_path, {"model": "sonnet"})
    assert cd.get_main_effort_level() == "unset"
    assert "(effort per the Claude Code default)" in cd.force_load_rules()


def test_unreadable_settings_degrades_to_neutral_phrase(monkeypatch, tmp_path):
    """Read failure sets effort='unknown' — must not surface as '(unknown effort)'."""
    _settings(monkeypatch, tmp_path, None)
    assert cd.get_main_effort_level() == "unknown"
    text = cd.force_load_rules()
    assert "(effort per the Claude Code default)" in text
    assert "unknown effort" not in text


def test_placeholder_never_leaks_into_the_emitted_reminder(monkeypatch, tmp_path):
    """A missed substitution would ship a literal {{EFFORT}} to every session."""
    assert "{{EFFORT}}" in cd.FORCE_LOAD_RULES, "template lost its placeholder"
    for payload in (
        {"model": "opus[1m]", "effortLevel": "max"},
        {"model": "opus[1m]"},
        None,
    ):
        _settings(monkeypatch, tmp_path, payload)
        assert "{{EFFORT}}" not in cd.force_load_rules()


def test_reminder_still_carries_the_pricing_anchor(monkeypatch, tmp_path):
    """Guard the facts the substitution sits next to, so an edit can't drop them."""
    _settings(monkeypatch, tmp_path, {"model": "opus[1m]", "effortLevel": "high"})
    text = cd.force_load_rules()
    assert "Opus 5" in text
    assert "$5/$25" in text
    assert "2026-08-31" in text
