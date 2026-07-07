import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "lib" / "bump_plugin_version.py"
spec = importlib.util.spec_from_file_location("bump_plugin_version", MOD)
bpv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bpv)


def _write_manifest(tmp_path, data):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


def test_bumps_only_version_field(tmp_path):
    original = {
        "name": "claude-core-hooks",
        "version": "0.1.0",
        "description": "Cost-discipline hook for Claude Code sessions.",
    }
    manifest_path = _write_manifest(tmp_path, original)

    bpv.bump_version(manifest_path, "0.2.0")

    result = json.loads(manifest_path.read_text())
    assert result["version"] == "0.2.0"
    assert result["name"] == "claude-core-hooks"
    assert result["description"] == "Cost-discipline hook for Claude Code sessions."
    assert list(result.keys()) == list(original.keys())


def test_preserves_rest_of_file_byte_for_byte_except_version(tmp_path):
    original = {
        "name": "claude-core-hooks",
        "version": "0.1.0",
        "description": "Cost-discipline hook for Claude Code sessions.",
    }
    manifest_path = _write_manifest(tmp_path, original)
    before_text = manifest_path.read_text()

    bpv.bump_version(manifest_path, "1.2.3")

    after_text = manifest_path.read_text()
    before_no_version_line = "\n".join(
        line for line in before_text.splitlines() if '"version"' not in line
    )
    after_no_version_line = "\n".join(
        line for line in after_text.splitlines() if '"version"' not in line
    )
    assert before_no_version_line == after_no_version_line


def test_main_reads_new_version_env_var(tmp_path, monkeypatch):
    original = {"name": "claude-core-hooks", "version": "0.1.0", "description": "x"}
    manifest_path = _write_manifest(tmp_path, original)
    monkeypatch.setenv("NEW_VERSION", "9.9.9")
    monkeypatch.setattr(bpv, "PLUGIN_MANIFEST", manifest_path)

    exit_code = bpv.main()

    assert exit_code == 0
    result = json.loads(manifest_path.read_text())
    assert result["version"] == "9.9.9"


def test_main_fails_without_new_version_env_var(tmp_path, monkeypatch):
    original = {"name": "claude-core-hooks", "version": "0.1.0", "description": "x"}
    manifest_path = _write_manifest(tmp_path, original)
    monkeypatch.delenv("NEW_VERSION", raising=False)
    monkeypatch.setattr(bpv, "PLUGIN_MANIFEST", manifest_path)

    exit_code = bpv.main()

    assert exit_code == 1
    result = json.loads(manifest_path.read_text())
    assert result["version"] == "0.1.0"
