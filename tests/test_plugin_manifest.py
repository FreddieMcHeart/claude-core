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

def test_hooks_json_covers_all_five_events():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    assert set(hooks.keys()) == {
        "PreToolUse", "PostToolUse", "SessionStart", "PostCompact", "UserPromptSubmit",
    }

def test_hooks_json_commands_and_matchers_match_legacy_table():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    hooks = data["hooks"]
    # As of 2026-07-17 the L4 context ledger (PostToolUse) must meter the dominant
    # flood — Read/Bash/Grep AND MCP results — not only the Agent|Task|Workflow
    # dispatch returns it was originally (mistakenly) scoped to. PostToolUse therefore
    # adds `mcp__.*` on top of PreToolUse's enforcement list; PreToolUse stays as-is
    # (its streak/edit-loop enforcement is intentionally tool-type specific).
    pre_tools = "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow"
    expected = {
        "PreToolUse": (pre_tools, "pre-tool"),
        "PostToolUse": (pre_tools + "|mcp__.*", "post-tool"),
        "SessionStart": ("startup|resume", "session-start"),
        "PostCompact": (None, "post-compact"),
        "UserPromptSubmit": (None, "user-prompt-submit"),
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
