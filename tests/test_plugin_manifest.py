import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_plugin_json_shape():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "claude-core-hooks"
    assert "version" in data
    assert "description" in data

def test_marketplace_json_plugin_name_matches_plugin_json():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert len(marketplace["plugins"]) == 1
    entry = marketplace["plugins"][0]
    assert entry["name"] == plugin["name"]
    assert entry["source"] == "."

def test_hooks_json_covers_all_four_events():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    assert set(hooks.keys()) == {"PreToolUse", "PostToolUse", "SessionStart", "PostCompact"}

def test_hooks_json_commands_and_matchers_match_legacy_table():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    expected = {
        "PreToolUse": ("Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow", "pre-tool"),
        "PostToolUse": ("Agent|Task|Workflow", "post-tool"),
        "SessionStart": ("startup|resume", "session-start"),
        "PostCompact": (None, "post-compact"),
    }
    for event, (matcher, mode) in expected.items():
        groups = hooks[event]
        assert len(groups) == 1
        grp = groups[0]
        if matcher is None:
            assert "matcher" not in grp
        else:
            assert grp["matcher"] == matcher
        cmd = grp["hooks"][0]["command"]
        assert cmd == f'"${{CLAUDE_PLUGIN_ROOT}}"/hooks/cost-discipline.py {mode}'
