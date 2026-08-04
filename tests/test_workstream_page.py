"""Read-before-work: the prompt names a workstream, the vault has a page, nobody opened it.

The measurement behind this check (2026-08-04, two vaults, one predicate): 3.58 and 4.05
writes per deliberate read. Writing has an external trigger — someone says "write this
down". Reading has none. So the point of these tests is less the happy path than the three
silent outcomes, because a check that only speaks when it finds something cannot be told
apart from one that was never wired up.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

# Loaded per-file via importlib, matching the convention the other suites use — the hook
# is a script with a hyphen in its name and is not importable as a module.
MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
_spec = importlib.util.spec_from_file_location("cost_discipline_workstream", MOD)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


# --------------------------------------------------------------------------- fixtures

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def vault(tmp_path):
    """A real git repo with a real committed page and index row.

    Built rather than mocked, and committed rather than merely written, because the scan
    reads from HEAD on purpose — a page that exists only on this machine is not one a
    fresh clone can open. A fixture that wrote files without committing them would pass
    against a scan that read the worktree and prove nothing about the scan we have.
    """
    repo = tmp_path / "wiki"
    (repo / "brain" / "proj").mkdir(parents=True)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Page 1 is findable by FILENAME.
    (repo / "brain" / "proj" / "PLAT-3113-rtbf-migration.md").write_text("# rtbf\n")
    # Page 2 is findable only through an INDEX ROW — the higher-recall half, and the case
    # a filename-only matcher silently misses. Named by topic, as most pages are.
    (repo / "brain" / "proj" / "kafka-gen4-cutover.md").write_text("# cutover\n")
    (repo / "brain" / "proj" / "_index.md").write_text(
        "| Page | What |\n|---|---|\n"
        "| [[brain/proj/kafka-gen4-cutover]] | the INE-857 cutover, gen-3 to gen-4 |\n"
        "| [[brain/proj/PLAT-3113-rtbf-migration]] | PLAT-3113 |\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pages")
    return repo


def _state(**kw):
    s = cd.new_state("s1")
    s.update(kw)
    return s


# --------------------------------------------------------------------------- key parsing

def test_keys_are_deduped_and_ordered():
    keys, truncated = cd.workstream_keys("PLAT-3113 then INE-857 and PLAT-3113 again")
    assert keys == ["PLAT-3113", "INE-857"]
    assert truncated == 0


def test_non_ticket_shapes_are_not_keys():
    """UTF-8 and SHA-256 are the shape of a ticket and are not tickets.

    The denylist is an optimisation, not the filter — the real filter is that a key with
    no page says nothing. This test pins the cheap half so a novel encoding name does not
    start costing a git scan on every prompt that mentions it.
    """
    keys, _ = cd.workstream_keys("re-encode as UTF-8, hash with SHA-256, per RFC-5322")
    assert keys == []


def test_single_digit_suffix_is_not_a_key():
    """`GPT-4` and `HTTP-2` are excluded by the shape rather than by name, which is what
    keeps the denylist short enough to stay honest."""
    keys, _ = cd.workstream_keys("run it on GPT-4 over HTTP-2")
    assert keys == []


def test_single_letter_prefix_is_not_a_key():
    """Caught by this suite's own first run: `A-11` does not match, because the prefix is
    required to be at least two characters. That is deliberate — one letter followed by
    digits is overwhelmingly a list marker, a version, or a coordinate, and real ticket
    prefixes are 2+ (PLAT, INE, PLT). The first version of the bound test below used
    `A-11` and failed for this reason; the regex was right and the test was wrong."""
    assert cd.workstream_keys("see A-11 and B-22")[0] == []


def test_key_bound_is_reported_not_silent():
    prompt = "AAA-11 BBB-22 CCC-33 DDD-44 EEE-55"
    keys, truncated = cd.workstream_keys(prompt)
    assert len(keys) == cd.WORKSTREAM_MAX_KEYS
    assert truncated == 5 - cd.WORKSTREAM_MAX_KEYS, \
        "a truncated key list must report its remainder — a silent cap reads as coverage"


# --------------------------------------------------------------------------- the scan

def test_scan_finds_a_page_by_filename(vault):
    hits = cd.workstream_page_scan(["PLAT-3113"], repo=vault)
    assert hits and any("PLAT-3113" in p for p in hits["PLAT-3113"])


def test_scan_finds_a_page_only_an_index_row_names(vault):
    """The page is called kafka-gen4-cutover; nothing in its filename says INE-857."""
    hits = cd.workstream_page_scan(["INE-857"], repo=vault)
    assert hits == {"INE-857": ["brain/proj/kafka-gen4-cutover.md"]}


def test_scan_returns_empty_dict_when_the_vault_has_nothing(vault):
    """{} is 'searched, found nothing'. It must not be None."""
    assert cd.workstream_page_scan(["ZZZ-999"], repo=vault) == {}


def test_scan_returns_none_when_it_could_not_look(tmp_path):
    """None is 'no search happened'. The two are different answers and the caller branches
    on the difference — this is the distinction that makes a check able to fail."""
    assert cd.workstream_page_scan(["PLAT-3113"], repo=tmp_path / "nope") is None
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.md").write_text("x")
    assert cd.workstream_page_scan(["PLAT-3113"], repo=plain) is None, \
        "a directory that is not a git repo has not been searched"


def test_scan_ignores_an_uncommitted_page(vault):
    """Read from HEAD, not the worktree. A page only this machine has is not a page the
    advisory should send anyone to."""
    (vault / "brain" / "proj" / "QQQ-42-secret.md").write_text("# not committed\n")
    assert cd.workstream_page_scan(["QQQ-42"], repo=vault) == {}


# --------------------------------------------------------------------------- the advisory

def test_fires_when_the_page_exists_and_was_not_opened(vault, monkeypatch):
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "save_state", lambda s: None)
    msg, outcome = cd.workstream_page_context(_state(), "start on PLAT-3113 please")
    assert outcome == "unopened"
    assert "PLAT-3113" in msg and "brain/proj/PLAT-3113-rtbf-migration.md" in msg


def test_silent_when_the_page_was_already_opened(vault, monkeypatch):
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "save_state", lambda s: None)
    st = _state(wiki_paths_read=[str(vault / "brain/proj/PLAT-3113-rtbf-migration.md")])
    msg, outcome = cd.workstream_page_context(st, "continue PLAT-3113")
    assert msg is None and outcome == "opened"


def test_silent_but_LOGGED_when_no_page_exists(vault, monkeypatch):
    """The whole point of the outcome field. A key with no page produces no message, and
    the run must still be distinguishable from a run that never happened."""
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "save_state", lambda s: None)
    msg, outcome = cd.workstream_page_context(_state(), "look at ZZZ-999")
    assert msg is None and outcome == "no-page"


def test_no_key_in_prompt_is_a_third_state_not_clean(vault, monkeypatch):
    """Nothing ran. Reporting that as an outcome would put a stream of meaningless 'clean'
    rows in the fire log and make the real ones unfindable."""
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    msg, outcome = cd.workstream_page_context(_state(), "just fix the typo")
    assert msg is None and outcome is None


def test_fires_once_per_key_not_once_per_prompt(vault, monkeypatch):
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "save_state", lambda s: None)
    st = _state()
    first, o1 = cd.workstream_page_context(st, "PLAT-3113 next")
    second, o2 = cd.workstream_page_context(st, "still on PLAT-3113")
    assert first and o1 == "unopened"
    assert second is None and o2 == "already-advised"


def test_a_key_with_no_page_is_also_marked_so_the_scan_does_not_repeat(vault, monkeypatch):
    """The scan is the expensive half and the no-page case is the common one. Marking only
    the keys that produced a message would re-scan on every prompt mentioning them."""
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "save_state", lambda s: None)
    st = _state()
    cd.workstream_page_context(st, "ZZZ-999?")
    assert "ZZZ-999" in st["workstream_keys_fired"]


def test_a_grep_over_the_vault_does_not_count_as_having_opened_the_page(vault, monkeypatch):
    """Under-crediting on purpose. A sweep is not a consultation, and letting one silence
    the advisory would reproduce the read-to-consult versus read-in-order-to-edit
    conflation that neither 2026-08-04 measurement could separate."""
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "save_state", lambda s: None)
    st = _state(wiki_read_count=9)          # nine sweeps, no Read of the page
    msg, outcome = cd.workstream_page_context(st, "PLAT-3113")
    assert outcome == "unopened" and msg


def test_could_not_look_is_not_reported_as_no_page(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "WIKI_DIR", tmp_path / "absent")
    msg, outcome = cd.workstream_page_context(_state(), "PLAT-3113")
    assert msg is None and outcome == "skipped", \
        "an unsearchable vault must not report the same outcome as a searched empty one"
