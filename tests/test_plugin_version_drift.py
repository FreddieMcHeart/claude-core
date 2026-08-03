"""Plugin version drift: the installed claude-core-hooks copy vs. the repository
it was built from.

The failure this guards is the fourth recurrence of the same shape: the repo
moves ahead of the install and nothing compares them, so the gap is only ever
noticed after it has already misled someone. Test order is deliberate — the
"could not look" cases come first, before any happy path exists to make them
look redundant, because a check that silently finds nothing is indistinguishable
from a check that never ran.

Fixtures are entirely synthetic (fabricated installed_plugins.json /
known_marketplaces.json / plugin.json, real git repos under tmp_path). The
live drift on this machine (repo currently ahead of the installed copy) is
verified separately, by hand, once — never as an automated test, because a
test that only passes due to today's machine state is the same "assertion
with no reachable failing state" shape this repo already has a page about.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_plugin_drift", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

FIRST_PROMPT_BASE = {"prompts_seen": 1}
KEY = f"{cd.PLUGIN_NAME}@test-marketplace"


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _commit_plugin_json(repo, version):
    (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": cd.PLUGIN_NAME, "version": version}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _installed_plugins_file(tmp_path, key, entries):
    """Real on-disk shape: {"version": 2, "plugins": {"name@market": [...]}} —
    NOT the "name@market" keys at the top level. Getting this fixture wrong
    once already produced a false 'skipped' against live state; see the
    docstring on _resolve_installed_plugin_record."""
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({"version": 2, "plugins": {key: entries}}))
    return p


def _registry_file(tmp_path, marketplace, repo_path):
    p = tmp_path / "known_marketplaces.json"
    p.write_text(json.dumps(
        {marketplace: {"source": {"source": "directory", "path": str(repo_path)}}}))
    return p


def _prompt(cwd=None):
    d: dict = dict(FIRST_PROMPT_BASE)
    if cwd is not None:
        d["cwd"] = str(cwd)
    return d


# ---------------- could-not-look: must never guess ----------------

def test_missing_installed_plugins_file_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "also-missing.json")
    assert cd.plugin_version_drift_context(_prompt()) == (None, "skipped:not_installed")


def test_plugin_key_absent_is_skipped(tmp_path, monkeypatch):
    installed = _installed_plugins_file(tmp_path, "some-other-plugin@marketplace",
                                        [{"version": "1.0.0"}])
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "missing.json")
    assert cd.plugin_version_drift_context(_prompt()) == (None, "skipped:not_installed")


def test_malformed_installed_plugins_json_is_skipped(tmp_path, monkeypatch):
    p = tmp_path / "installed_plugins.json"
    p.write_text("{not valid json")
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", p)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "missing.json")
    assert cd.plugin_version_drift_context(_prompt()) == (None, "skipped:not_installed")


def test_missing_plugins_wrapper_key_is_skipped(tmp_path, monkeypatch):
    """Valid JSON, but missing the top-level "plugins" wrapper the real schema
    always has — must not be treated as if the flat body were the plugin map."""
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({"version": 2, KEY: [{"version": "1.0.0"}]}))
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", p)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "missing.json")
    assert cd.plugin_version_drift_context(_prompt()) == (None, "skipped:not_installed")


def test_unlocatable_repo_is_skipped_never_guesses_a_path(tmp_path, monkeypatch):
    """No registry entry, and cwd (an unrelated dir) has no plugin.json above it
    anywhere. Must report 'skipped', not invent a path."""
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": "a" * 40}])
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "missing.json")
    unrelated = tmp_path / "unrelated" / "nested"
    unrelated.mkdir(parents=True)
    assert cd.plugin_version_drift_context(_prompt(cwd=unrelated)) == (None, "skipped:repo_unresolved")


def test_missing_plugin_json_at_registry_path_is_skipped(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()  # no .claude-plugin/plugin.json inside
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": "a" * 40}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    assert cd.plugin_version_drift_context(_prompt()) == (None, "skipped:repo_unresolved")


def test_registry_path_with_mismatched_plugin_name_is_skipped(tmp_path, monkeypatch):
    """The marketplace directory a registry entry points to can host a DIFFERENT
    plugin — a registry hit only says "where the marketplace is", not "is this
    the plugin". Must be rejected exactly like a walk-up name mismatch, not
    trusted just because the registry named the directory."""
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    (other_repo / ".claude-plugin").mkdir()
    (other_repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "some-unrelated-plugin", "version": "9.9.9"}))
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": "a" * 40}])
    registry = _registry_file(tmp_path, "test-marketplace", other_repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    assert cd.plugin_version_drift_context(_prompt()) == (None, "skipped:repo_unresolved")


# ---------------- throttle: shares the pulse's cadence ----------------

def test_throttle_gates_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "missing.json")
    assert cd.plugin_version_drift_context({"prompts_seen": 2}) == (None, None)


# ---------------- repo located via the registry (primary, no cwd needed) ----------------

def test_registry_resolves_repo_without_any_cwd(tmp_path, monkeypatch):
    """The registry path is authoritative and global — it must work even when
    the session's cwd carries no information at all (state has no 'cwd' key)."""
    repo = _repo(tmp_path)
    sha = _commit_plugin_json(repo, "1.0.0")
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": sha}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    assert cd.plugin_version_drift_context(_prompt(cwd=None)) == (None, "in_sync")


# ---------------- cwd walk-up fallback: verified by name match, not assumed ----------------

def test_walkup_fallback_when_registry_has_no_directory_entry(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    sha = _commit_plugin_json(repo, "1.0.0")
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": sha}])
    # Registry entry exists but is NOT a directory source (e.g. github-sourced) ->
    # must fall through to the walk-up, not stop at "found an entry".
    reg = tmp_path / "known_marketplaces.json"
    reg.write_text(json.dumps({"test-marketplace": {"source": {"source": "github", "repo": "x/y"}}}))
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", reg)
    nested = repo / "sub" / "dir"
    nested.mkdir(parents=True)
    assert cd.plugin_version_drift_context(_prompt(cwd=nested)) == (None, "in_sync")


def test_walkup_rejects_a_name_mismatched_manifest(tmp_path, monkeypatch):
    """A plugin.json exists above cwd but belongs to a DIFFERENT plugin — must not
    be accepted just because something was found; the name has to match."""
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    (other_repo / ".claude-plugin").mkdir()
    (other_repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "some-unrelated-plugin", "version": "9.9.9"}))
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": "a" * 40}])
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", tmp_path / "missing.json")
    nested = other_repo / "sub"
    nested.mkdir()
    assert cd.plugin_version_drift_context(_prompt(cwd=nested)) == (None, "skipped:repo_unresolved")


# ---------------- comparison: gitCommitSha primary ----------------

def test_sha_match_is_in_sync(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    sha = _commit_plugin_json(repo, "1.0.0")
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": sha}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    assert cd.plugin_version_drift_context(_prompt()) == (None, "in_sync")


def test_sha_mismatch_reports_drift_with_commit_distance(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    old_sha = _commit_plugin_json(repo, "1.0.0")
    new_sha = _commit_plugin_json(repo, "1.0.1")
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": old_sha}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    msg, outcome = cd.plugin_version_drift_context(_prompt())
    assert outcome == "drifted"
    assert "1.0.0" in msg and "1.0.1" in msg
    assert "1 commit" in msg
    assert new_sha != old_sha


def test_the_remedy_command_is_marketplace_qualified(tmp_path, monkeypatch):
    """The emitted `claude plugin update …` must name `plugin@marketplace`.

    The bare plugin name is REJECTED by the CLI — measured 2026-08-03:

        $ claude plugin update claude-core-hooks
        ✘ Failed to update plugin "claude-core-hooks": Plugin "…" not found
        $ claude plugin update claude-core-hooks@claude-core-local
        ✔ updated from 0.11.7 to 0.12.1

    So the first version of this advisory printed a command that could not
    work: the check fired correctly, named a real problem, and handed over a
    remedy that fails. A nudge whose only actionable line is broken is worse
    than no nudge, because the reader concludes the diagnosis was wrong.

    The assertion carries the CLOSING BACKTICK on purpose. Without it this
    test cannot fail on the old code — `claude plugin update <name>@<market>`
    contains `claude plugin update <name>` as a substring, so a naive `in`
    check passes either way. The backtick is what makes the bare form and the
    qualified form distinguishable.
    """
    repo = _repo(tmp_path)
    _commit_plugin_json(repo, "1.0.0")
    _commit_plugin_json(repo, "1.0.1")
    installed = _installed_plugins_file(tmp_path, KEY, [{"version": "1.0.0"}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    msg, outcome = cd.plugin_version_drift_context(_prompt())
    assert outcome == "drifted"
    assert "@" in KEY, "fixture must use a qualified key or this proves nothing"
    assert f"`claude plugin update {KEY}`" in msg
    assert f"`claude plugin update {cd.PLUGIN_NAME}`" not in msg


def test_diverged_commit_is_drifted_without_a_false_distance_claim(tmp_path, monkeypatch):
    """installed_sha is NOT an ancestor of repo HEAD (e.g. history rewritten) —
    must report drift honestly without claiming a specific commit distance."""
    repo = _repo(tmp_path)
    _commit_plugin_json(repo, "1.0.0")
    fake_sha = "f" * 40  # never existed in this repo's history
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": fake_sha}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    msg, outcome = cd.plugin_version_drift_context(_prompt())
    assert outcome == "drifted"
    assert "commit" in msg.lower()


# ---------------- comparison: version fallback when sha unavailable ----------------

def test_missing_sha_falls_back_to_version_and_reports_in_sync(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit_plugin_json(repo, "1.0.0")
    installed = _installed_plugins_file(tmp_path, KEY, [{"version": "1.0.0"}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    assert cd.plugin_version_drift_context(_prompt()) == (None, "in_sync")


def test_missing_sha_falls_back_to_version_and_reports_drift(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit_plugin_json(repo, "1.0.1")
    installed = _installed_plugins_file(tmp_path, KEY, [{"version": "1.0.0"}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    msg, outcome = cd.plugin_version_drift_context(_prompt())
    assert outcome == "drifted"
    assert "1.0.0" in msg and "1.0.1" in msg
    assert "version" in msg.lower(), "must say which signal it used"


# ---------------- the nudge must state which two states it compared ----------------

def test_advisory_names_the_states_it_compared_not_the_running_process(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    old_sha = _commit_plugin_json(repo, "1.0.0")
    _commit_plugin_json(repo, "1.0.1")
    installed = _installed_plugins_file(tmp_path, KEY,
                                         [{"version": "1.0.0", "gitCommitSha": old_sha}])
    registry = _registry_file(tmp_path, "test-marketplace", repo)
    monkeypatch.setattr(cd, "INSTALLED_PLUGINS_FILE", installed)
    monkeypatch.setattr(cd, "KNOWN_MARKETPLACES_FILE", registry)
    msg, _ = cd.plugin_version_drift_context(_prompt())
    assert "restart" in msg.lower()
