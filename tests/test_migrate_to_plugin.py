import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "migrate_to_plugin.py"
spec = importlib.util.spec_from_file_location("migrate_to_plugin", MOD)
mtp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtp)

CD = "/home/u/.claude"


def _legacy(mode):
    return f"{CD}/hooks/cost-discipline.py {mode}"


def _write(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    return p


def test_removes_solo_group_entirely(tmp_path):
    """A group containing ONLY the legacy hook is dropped, not left as hooks: []."""
    data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
                    "hooks": [{"type": "command", "command": _legacy("pre-tool")}],
                }
            ]
        }
    }
    p = _write(tmp_path, data)
    removed = mtp.migrate(p, CD)
    assert removed == 1
    result = json.loads(p.read_text())
    assert "PreToolUse" not in result["hooks"]


def test_removes_only_legacy_entry_from_mixed_group(tmp_path):
    """A group with the legacy hook PLUS other hooks (downbeat's relay-inbox.py,
    a hand-added user hook) keeps everything except the legacy entry."""
    data = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume",
                    "hooks": [
                        {"type": "command", "command": "/x/.claude/scripts/obsidian-hot-cache-inject.sh SessionStart"},
                        {"type": "command", "command": _legacy("session-start")},
                        {"type": "command", "command": "/x/.claude/hooks/relay-inbox.py"},
                    ],
                }
            ]
        }
    }
    p = _write(tmp_path, data)
    removed = mtp.migrate(p, CD)
    assert removed == 1
    result = json.loads(p.read_text())
    remaining_cmds = {h["command"] for h in result["hooks"]["SessionStart"][0]["hooks"]}
    assert remaining_cmds == {
        "/x/.claude/scripts/obsidian-hot-cache-inject.sh SessionStart",
        "/x/.claude/hooks/relay-inbox.py",
    }


def test_removes_all_four_events(tmp_path):
    data = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|Agent|Task|Workflow",
                             "hooks": [{"type": "command", "command": _legacy("pre-tool")}]}],
            "PostToolUse": [{"matcher": "Agent|Task|Workflow",
                              "hooks": [{"type": "command", "command": _legacy("post-tool")}]}],
            "SessionStart": [{"matcher": "startup|resume",
                               "hooks": [{"type": "command", "command": _legacy("session-start")}]}],
            "PostCompact": [{"hooks": [{"type": "command", "command": _legacy("post-compact")}]}],
        }
    }
    p = _write(tmp_path, data)
    removed = mtp.migrate(p, CD)
    assert removed == 4
    result = json.loads(p.read_text())
    assert result["hooks"] == {}


def test_nothing_to_migrate_returns_zero_and_does_not_write(tmp_path):
    data = {"hooks": {"PostToolUse": [{"matcher": "Bash",
                       "hooks": [{"type": "command", "command": "/x/.claude/hooks/relay-poll-offer.py"}]}]}}
    p = _write(tmp_path, data)
    before = p.read_text()
    removed = mtp.migrate(p, CD)
    assert removed == 0
    assert p.read_text() == before  # untouched, no backup, no rewrite


def test_creates_backup_only_when_something_removed(tmp_path):
    data = {"hooks": {"PostCompact": [{"hooks": [{"type": "command", "command": _legacy("post-compact")}]}]}}
    p = _write(tmp_path, data)
    mtp.migrate(p, CD)
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1


def test_malformed_json_backs_up_and_exits_2(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not valid json")
    try:
        mtp.migrate(p, CD)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 2
    malformed = list(tmp_path.glob("settings.json.malformed-*"))
    assert len(malformed) == 1
