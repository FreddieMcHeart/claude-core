import json, sys, subprocess, importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "settings_merge.py"
spec = importlib.util.spec_from_file_location("settings_merge", MOD)
sm = importlib.util.module_from_spec(spec); spec.loader.exec_module(sm)

CD = "/home/u/.claude"   # a fixed claude-dir for deterministic command strings

def _load(p): return json.loads(Path(p).read_text())

def test_merge_into_absent_creates_all_four(tmp_path):
    s = tmp_path / "settings.json"
    assert sm.merge(s, CD) == "created"
    hooks = _load(s)["hooks"]
    for ev in ("PreToolUse", "PostToolUse", "SessionStart", "PostCompact"):
        assert ev in hooks and hooks[ev], f"{ev} missing"

def test_idempotent_second_run_unchanged_no_backup(tmp_path):
    s = tmp_path / "settings.json"
    sm.merge(s, CD)
    before = s.read_text()
    assert sm.merge(s, CD) == "unchanged"
    assert s.read_text() == before                       # byte-identical
    assert list(tmp_path.glob("settings.json.bak-*")) == []   # no backup on no-op

def test_preserves_foreign_hooks_and_keys(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "/other/thing.sh"}]}]}
    }))
    assert sm.merge(s, CD) == "updated"
    d = _load(s)
    assert d["model"] == "opus"
    cmds = [h["command"] for g in d["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "/other/thing.sh" in cmds                     # foreign hook kept
    assert any("cost-discipline.py" in c for c in cmds)  # ours added

def test_skip_already_registered(tmp_path):
    s = tmp_path / "settings.json"
    sm.merge(s, CD)
    d = _load(s); n = len(d["hooks"]["PreToolUse"])
    assert sm.merge(s, CD) == "unchanged"
    assert len(_load(s)["hooks"]["PreToolUse"]) == n     # no duplicate

def test_malformed_aborts_backs_up_and_leaves_original(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{ this is not json ")
    orig = s.read_text()
    try:
        sm.merge(s, CD); assert False, "should have raised SystemExit"
    except SystemExit as e:
        assert e.code == 2
    assert s.read_text() == orig                         # untouched
    assert list(tmp_path.glob("settings.json.malformed-*"))  # backup made

def test_check_mode_cli(tmp_path):
    s = tmp_path / "settings.json"
    # --check on absent file: a merge WOULD be needed → exit 1
    r = subprocess.run([sys.executable, str(MOD), "--settings", str(s),
                        "--claude-dir", CD, "--check"])
    assert r.returncode == 1
    sm.merge(s, CD)
    # after merge, --check → exit 0 (nothing to do)
    r = subprocess.run([sys.executable, str(MOD), "--settings", str(s),
                        "--claude-dir", CD, "--check"])
    assert r.returncode == 0
