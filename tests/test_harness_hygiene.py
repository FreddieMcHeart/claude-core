"""Harness-hygiene pulse: scan, thresholds, throttle, and payload merging.

First test file to exercise cost-discipline.py's own logic (existing tests only
cover install/packaging), so it also pins the UserPromptSubmit contract that the
hygiene pulse now shares with the Layer-2 rlm-fanout advisory.
"""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

DAY = 86400


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    return tmp_path


def _dirty(repo, name, age_days=0):
    p = repo / name
    p.write_text("x")
    t = time.time() - age_days * DAY - 3600  # +1h so floor division is stable
    os.utime(p, (t, t))
    return p


# ---------------- hygiene_scan ----------------

def test_scan_returns_none_for_non_git_dir(tmp_path):
    assert cd.hygiene_scan(tmp_path) is None


def test_scan_clean_repo_is_zero(tmp_path):
    repo = _git_repo(tmp_path)
    assert cd.hygiene_scan(repo) == (0, 0, [])


def test_scan_counts_dirty_files(tmp_path):
    repo = _git_repo(tmp_path)
    for i in range(4):
        _dirty(repo, f"f{i}.txt")
    count, oldest, samples = cd.hygiene_scan(repo)
    assert count == 4
    assert oldest == 0
    assert len(samples) <= cd.HYGIENE_SAMPLE_COUNT


def test_scan_reports_oldest_age_from_mtime(tmp_path):
    repo = _git_repo(tmp_path)
    _dirty(repo, "fresh.txt", age_days=0)
    _dirty(repo, "stale.txt", age_days=9)
    count, oldest, samples = cd.hygiene_scan(repo)
    assert count == 2
    assert oldest == 9
    assert samples[0][0] == "stale.txt"  # oldest first


def test_scan_deleted_file_does_not_dominate_oldest(tmp_path):
    """A deleted path has no mtime; it must not be treated as infinitely old."""
    repo = _git_repo(tmp_path)
    p = _dirty(repo, "gone.txt")
    subprocess.run(["git", "-C", str(repo), "add", "gone.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add"], check=True)
    p.unlink()
    count, oldest, _ = cd.hygiene_scan(repo)
    assert count == 1
    assert oldest == 0


# ---------------- scan: the parse traps (regressions, all found in review) ----------------
# Each of these failed the first implementation, and all failed the SAME way:
# the entry silently read as age 0, so a stale file was invisible to the age
# threshold. A staleness detector that defaults unknown -> fresh is worse than none.

def test_scan_handles_paths_with_spaces(tmp_path):
    """porcelain quotes paths with spaces by default; -z must defeat that."""
    repo = _git_repo(tmp_path)
    _dirty(repo, "my old notes.txt", age_days=30)
    count, oldest, samples = cd.hygiene_scan(repo)
    assert count == 1
    assert oldest == 30, "a quoted path must still resolve to a real mtime"
    assert samples[0][0] == "my old notes.txt", "path must arrive unquoted"


def test_scan_handles_unicode_paths(tmp_path):
    """core.quotePath escapes non-ASCII as octal (\\303\\251); -z must defeat that."""
    repo = _git_repo(tmp_path)
    _dirty(repo, "café-notes.txt", age_days=12)
    count, oldest, samples = cd.hygiene_scan(repo)
    assert count == 1
    assert oldest == 12
    assert samples[0][0] == "café-notes.txt"


def test_scan_handles_renames(tmp_path):
    """A rename is TWO NUL records (new, then origin). The origin must be consumed,
    not counted as a second dirty file."""
    repo = _git_repo(tmp_path)
    p = _dirty(repo, "orig.txt")
    subprocess.run(["git", "-C", str(repo), "add", "orig.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "mv", "orig.txt", "renamed.txt"], check=True)
    count, _, samples = cd.hygiene_scan(repo)
    assert count == 1, "the rename origin must not be counted as its own entry"
    assert samples[0][0] == "renamed.txt"
    assert p.name not in [s[0] for s in samples]


def test_scan_expands_untracked_directories(tmp_path):
    """-unormal collapses an untracked dir to ONE entry and stats the dir (whose
    mtime moves on every write), hiding both count and age. -uall expands it."""
    repo = _git_repo(tmp_path)
    d = repo / "abandoned_skill"
    d.mkdir()
    for i in range(12):
        f = d / f"f{i}.txt"
        f.write_text("x")
        t = time.time() - 21 * DAY - 3600
        os.utime(f, (t, t))
    count, oldest, _ = cd.hygiene_scan(repo)
    assert count == 12, "each abandoned file must count, not the directory as one"
    assert oldest == 21, "the directory's own mtime must not mask the files' age"


def test_scan_ages_unmerged_conflict_file_that_still_exists(tmp_path):
    """A modify/delete conflict reports UD/DU but LEAVES OUR VERSION on disk.

    Regression: a `"D" in status -> age 0` shortcut looks reasonable (a deletion
    cannot be stale) but is wrong for exactly these states, and is redundant for
    every other D status — real deletions already fall to the OSError branch.
    The shortcut zeroed a file that exists and can be arbitrarily old, which is
    the "unknown -> fresh" failure this whole section exists to prevent.
    """
    repo = _git_repo(tmp_path)
    (repo / "f.txt").write_text("base")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "other"], check=True)
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "delete"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-"], check=True)
    (repo / "f.txt").write_text("ours")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "modify"], check=True)
    subprocess.run(["git", "-C", str(repo), "merge", "other"], capture_output=True)

    assert (repo / "f.txt").exists(), "fixture: the conflict must leave our version on disk"
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert "D" in status.split()[0], f"fixture: expected an unmerged D status, got {status!r}"

    t = time.time() - 40 * DAY - 3600
    os.utime(repo / "f.txt", (t, t))
    _, oldest, _ = cd.hygiene_scan(repo)
    assert oldest == 40, "an unmerged file that exists on disk must be aged, not zeroed"


def test_scan_mixed_pile_counts_and_ages_correctly(tmp_path):
    """End-to-end: the three traps together must not under-report."""
    repo = _git_repo(tmp_path)
    _dirty(repo, "plain.txt", age_days=1)
    _dirty(repo, "with space.txt", age_days=40)
    d = repo / "dir"
    d.mkdir()
    for i in range(3):
        _dirty(repo, f"dir/n{i}.txt", age_days=2)
    count, oldest, samples = cd.hygiene_scan(repo)
    assert count == 5
    assert oldest == 40
    assert samples[0] == ("with space.txt", 40)


# ---------------- thresholds ----------------

def _ctx(monkeypatch, count, oldest, prompts_seen=1):
    monkeypatch.setattr(cd, "hygiene_scan", lambda repo=None: (count, oldest, [("a.txt", oldest)]))
    return cd.hygiene_context({"prompts_seen": prompts_seen})


def test_under_both_thresholds_is_silent(monkeypatch):
    assert _ctx(monkeypatch, count=10, oldest=7) is None


def test_over_file_count_fires(monkeypatch):
    msg = _ctx(monkeypatch, count=11, oldest=0)
    assert msg is not None
    assert "11 uncommitted files" in msg
    assert "limit 10" in msg


def test_over_age_fires(monkeypatch):
    msg = _ctx(monkeypatch, count=1, oldest=8)
    assert msg is not None
    assert "oldest dirty 8d" in msg
    assert "limit 7d" in msg


def test_over_both_reports_both_reasons(monkeypatch):
    msg = _ctx(monkeypatch, count=14, oldest=9)
    assert "14 uncommitted files" in msg and "oldest dirty 9d" in msg


def test_nudge_warns_against_add_all(monkeypatch):
    """The pile may hold another session's work — the nudge must say so."""
    msg = _ctx(monkeypatch, count=14, oldest=9)
    assert "git add -A" in msg


def test_unscannable_repo_is_silent(monkeypatch):
    monkeypatch.setattr(cd, "hygiene_scan", lambda repo=None: None)
    assert cd.hygiene_context({"prompts_seen": 1}) is None


# ---------------- throttle ----------------

def test_fires_on_first_prompt_of_session(monkeypatch):
    assert _ctx(monkeypatch, count=99, oldest=99, prompts_seen=1) is not None


def test_silent_between_intervals(monkeypatch):
    for n in range(2, 11):
        assert _ctx(monkeypatch, count=99, oldest=99, prompts_seen=n) is None, n


def test_fires_again_on_eleventh_prompt(monkeypatch):
    assert _ctx(monkeypatch, count=99, oldest=99, prompts_seen=11) is not None


def test_zero_prompts_is_silent(monkeypatch):
    assert _ctx(monkeypatch, count=99, oldest=99, prompts_seen=0) is None


def test_throttle_skips_the_scan_entirely(monkeypatch):
    """Off-interval prompts must not shell out to git at all — the whole point of
    the throttle is that git never runs on the hot path."""
    calls = []
    monkeypatch.setattr(cd, "hygiene_scan", lambda repo=None: calls.append(1) or (99, 99, []))
    cd.hygiene_context({"prompts_seen": 5})
    assert calls == []


# ---------------- handler payload merging ----------------

def _run_handler(monkeypatch, capsys, hyg=None, rlm=None):
    monkeypatch.setattr(cd, "load_state", lambda sid: {"session_id": sid, "prompts_seen": 0})
    monkeypatch.setattr(cd, "save_state", lambda state: None)
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    monkeypatch.setattr(cd, "hygiene_context", lambda state: hyg)
    monkeypatch.setattr(cd, "rlm_fanout_context", lambda prompt: rlm)
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "hi"})
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_handler_silent_when_nothing_to_say(monkeypatch, capsys):
    assert _run_handler(monkeypatch, capsys) is None


def test_handler_emits_hygiene_only(monkeypatch, capsys):
    payload = _run_handler(monkeypatch, capsys, hyg="HYG")
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert payload["hookSpecificOutput"]["additionalContext"] == "HYG"


def test_handler_emits_rlm_only(monkeypatch, capsys):
    payload = _run_handler(monkeypatch, capsys, rlm=("RLM", ["a", "b"]))
    assert payload["hookSpecificOutput"]["additionalContext"] == "RLM"


def test_handler_merges_both_into_one_payload(monkeypatch, capsys):
    """The hook may emit only ONE JSON object — two advisories must merge, not
    race each other onto stdout."""
    payload = _run_handler(monkeypatch, capsys, hyg="HYG", rlm=("RLM", []))
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "HYG" in ctx and "RLM" in ctx
    assert ctx.index("HYG") < ctx.index("RLM")


def test_handler_survives_state_failure(monkeypatch, capsys):
    """Hygiene must never break the prompt: a state blow-up still lets rlm through."""
    def boom(sid):
        raise OSError("state gone")
    monkeypatch.setattr(cd, "load_state", boom)
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    monkeypatch.setattr(cd, "rlm_fanout_context", lambda prompt: ("RLM", []))
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "hi"})
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["hookSpecificOutput"]["additionalContext"] == "RLM"


def test_handler_counts_prompts(monkeypatch, capsys):
    saved = {}
    monkeypatch.setattr(cd, "load_state", lambda sid: {"session_id": sid, "prompts_seen": 4})
    monkeypatch.setattr(cd, "save_state", lambda state: saved.update(state))
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    monkeypatch.setattr(cd, "hygiene_context", lambda state: None)
    monkeypatch.setattr(cd, "rlm_fanout_context", lambda prompt: None)
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "hi"})
    assert saved["prompts_seen"] == 5


# ---------------- regression: Layer 2 unchanged ----------------

def test_rlm_context_silent_on_empty_prompt():
    assert cd.rlm_fanout_context("") is None


def test_new_state_has_prompt_counter():
    assert cd.new_state("s1")["prompts_seen"] == 0
