#!/usr/bin/env python3
"""Patch .claude-plugin/plugin.json's version field from semantic-release's NEW_VERSION."""
import json
import os
import sys
from pathlib import Path

PLUGIN_MANIFEST = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"


def bump_version(manifest_path: Path, new_version: str) -> None:
    data = json.loads(manifest_path.read_text())
    data["version"] = new_version
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")


def main() -> int:
    new_version = os.environ.get("NEW_VERSION")
    if not new_version:
        print("bump_plugin_version.py: NEW_VERSION env var not set", file=sys.stderr)
        return 1
    bump_version(PLUGIN_MANIFEST, new_version)
    print(f"bump_plugin_version.py: set version to {new_version} in {PLUGIN_MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
