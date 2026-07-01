import os
import subprocess
import sys
from pathlib import Path

LOADER = str(Path(__file__).resolve().parents[1] / "lib" / "config_loader.py")

def _run(key, home):
    env = {**os.environ, "HOME": home}
    return subprocess.run([sys.executable, LOADER, key], capture_output=True, text=True, env=env)

def test_wiki_url_default_is_empty_when_no_config(tmp_path):
    # No ~/.claude/platform.config.toml present → falls back to default.
    r = _run("wiki_url", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""

def test_wiki_url_read_from_config(tmp_path):
    cfg = tmp_path / ".claude" / "platform.config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('wiki_url = "git@github.com:me/wiki.git"\n')
    r = _run("wiki_url", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "git@github.com:me/wiki.git"
