#!/usr/bin/env python3
"""
config_loader.py — load ~/.claude/platform.config.toml with documented defaults.

Absent file or missing field → falls back to the defaults below so callers
are always functional out of the box. Edit ~/.claude/platform.config.toml to
override for your project.

Usage (CLI):
    python3 ~/.claude/lib/config_loader.py project_root
    python3 ~/.claude/lib/config_loader.py wiki_path
    python3 ~/.claude/lib/config_loader.py jira.email
    python3 ~/.claude/lib/config_loader.py           # → full config as JSON

Usage (import):
    import sys; sys.path.insert(0, str(Path.home() / '.claude/lib'))
    from config_loader import config
    root = config['project_root']   # already expanduser()'d
"""

import json
import os
import sys
import tomllib
from pathlib import Path

CONFIG_PATH = Path("~/.claude/platform.config.toml").expanduser()

# Generic defaults — match platform.config.toml.example.
# Override by editing ~/.claude/platform.config.toml.
_DEFAULTS: dict = {
    "project_root":   "~/work/myproject",
    "wiki_path":      "~/work/myproject/docs",
    "workspace_glob": "~/work/*",
    "repos":          [],
    "jira": {
        "assignee": "you@example.com",
        "email":    "you@example.com",
        "epics":    [],
    },
    "cloud": {
        "aws_profiles": [],
        "gcp_clusters": [],
    },
    "slack": {
        "channels_ref": "~/.claude/skills/<your-slack-skill>/channels.yaml",
    },
}


def _expand(value: object) -> object:
    return os.path.expanduser(value) if isinstance(value, str) else value


def load() -> dict:
    raw: dict = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                raw = tomllib.load(f)
        except Exception:
            pass  # parse error → fall back to defaults silently

    result: dict = {}
    for key, default in _DEFAULTS.items():
        if isinstance(default, dict):
            section = raw.get(key, {})
            result[key] = {k: _expand(section.get(k, v)) for k, v in default.items()}
        else:
            result[key] = _expand(raw.get(key, default))
    return result


config: dict = load()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps(config, indent=2))
        sys.exit(0)

    key = sys.argv[1]
    if "." in key:
        section, subkey = key.split(".", 1)
        value = config.get(section, {}).get(subkey)
    else:
        value = config.get(key)

    if value is None:
        print(f"error: unknown key {key!r}", file=sys.stderr)
        sys.exit(1)

    if isinstance(value, list):
        for item in value:
            print(item)
    else:
        print(value)
