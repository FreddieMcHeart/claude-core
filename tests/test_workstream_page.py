"""Read-before-work: the prompt names a workstream, the vault has a page, nobody opened it.

The measurement behind the check (2026-08-04, two vaults, one predicate): 3.58 and 4.05
writes per deliberate read. Writing has an external trigger — someone says "write this
down". Reading has none.

Every test below whose name mentions a mechanism now ASSERTS that mechanism. The first
version of this file contained a test named "a grep over the vault does not count as having
opened the page" which set `wiki_read_count=9` on a hand-built state and called a function
that never reads that field. It passed for a reason unrelated to its name, and a mutation
deleting the guard it claimed to cover changed nothing. That is a check with no reachable
failing state, in the suite for a feature whose whole rationale is checks that cannot fail.
Its replacement drives `handle_pre_tool` for real.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

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
    """A real git repo with real committed pages.

    Committed, not merely written, because the scan reads HEAD on purpose — a fixture that
    only wrote files would pass against a scan that read the worktree and prove nothing
    about the scan we have.
    """
    repo = tmp_path / "wiki"
    (repo / "brain" / "proj" / "deep").mkdir(parents=True)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    (repo / "brain" / "proj" / "PLAT-3113-rtbf-migration.md").write_text("# rtbf\n")
    (repo / "brain" / "proj" / "kafka-gen4-cutover.md").write_text("# cutover\n")
    (repo / "brain" / "proj" / "deep" / "bare-stem-target.md").write_text("# bare\n")
    (repo / "brain" / "proj" / "_index.md").write_text(
        "| Page | What |\n|---|---|\n"
        # findable only through the row — the higher-recall half
        "| [[brain/proj/kafka-gen4-cutover]] | the INE-857 cutover, gen-3 to gen-4 |\n"
        "| [[brain/proj/PLAT-3113-rtbf-migration]] | PLAT-3113 |\n"
        # a BARE STEM under a subdirectory: resolvable only through the stem map
        "| [[bare-stem-target]] | BARE-11 lives here |\n"
        # a row whose target page is NOT committed — the dangling-row case
        "| [[brain/proj/ghost-page]] | GHOST-42 |\n"
        # two links on one row: the first CELL is the subject, the second cell is not.
        # The second link deliberately names a page NOT otherwise reachable, and the row
        # deliberately mentions no other key — an earlier fixture put "PLAT-3113" in this
        # row's body, which made PLAT-3113 legitimately match two pages and broke three
        # unrelated assertions. The fixture was wrong, not the code.
        "| [[brain/proj/kafka-gen4-cutover]] | TWO-22, see [[bare-stem-target]] |\n"
        "\n```\n| [[brain/proj/fenced-example]] | FENCE-99 |\n```\n"
        "<!-- | [[brain/proj/commented]] | HIDDEN-77 | -->\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pages")
    return repo


@pytest.fixture
def fired():
    """Collects (rule, outcome) pairs instead of appending to the real fire log."""
    return []


@pytest.fixture
def live(vault, monkeypatch, tmp_path, fired):
    """Point the module at the fixture vault, give it a real on-disk state dir so
    load_state/save_state round-trip through JSON, and STUB log_fire.

    Every sibling test file stubs log_fire; this one did not, so running pytest
    appended rows with session_id "s1" to ~/.claude/state/cost-discipline-log.jsonl —
    the instrument ROADMAP.md calls the only trustworthy number we have. A test suite
    that writes into the gauge it is measuring is not a test suite.
    """
    monkeypatch.setattr(cd, "WIKI_DIR", vault)
    monkeypatch.setattr(cd, "_WIKI_PATH", str(vault))
    monkeypatch.setattr(
        cd, "log_fire",
        lambda rule, *a, **k: fired.append((rule, k.get("outcome"))),
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(cd, "state_path", lambda sid: state_dir / f"{sid}.json")
    return vault


def _state(**kw):
    s = cd.new_state("s1")
    s.update(kw)
    return s


# --------------------------------------------------------------------------- key parsing

def test_keys_are_deduped_and_ordered():
    keys, truncated = cd.workstream_keys("PLAT-3113 then INE-857 and PLAT-3113 again")
    assert keys == ["PLAT-3113", "INE-857"]
    assert truncated == 0


def test_denylist_matches_the_prefix_with_digits_stripped():
    """`SHA3-256` walked past a denylist containing `SHA`, because the whole group was
    compared. Reproduced by execution before this test was written."""
    for s in ("SHA3-256", "SHA2-512", "HMAC-256", "UTF-8", "RFC-5322"):
        assert cd.workstream_keys(s)[0] == [], f"{s} must not be a key"


def test_underscore_suffix_is_still_a_key():
    r"""`\b` refused `PLAT-3113_notes`, because `_` is a word character — and that is how
    branch names and filenames spell it."""
    assert cd.workstream_keys("on PLAT-3113_notes")[0] == ["PLAT-3113"]


def test_single_digit_issue_numbers_are_keys():
    assert cd.workstream_keys("close PLAT-7")[0] == ["PLAT-7"]


def test_single_letter_prefix_is_not_a_key():
    """One letter followed by digits is overwhelmingly a list marker, a version, or a
    coordinate. The first version of the bound test used `A-11` and failed for this
    reason: the regex was right and the test was wrong."""
    assert cd.workstream_keys("see A-11 and B-22")[0] == []


def test_key_bound_is_reported_not_silent():
    keys, truncated = cd.workstream_keys("AAA-11 BBB-22 CCC-33 DDD-44 EEE-55")
    assert len(keys) == cd.WORKSTREAM_MAX_KEYS
    assert truncated == 5 - cd.WORKSTREAM_MAX_KEYS


def test_accumulation_is_bounded_during_the_loop_not_after():
    """A pasted CI log is the realistic input. The first version appended every distinct
    key to a list and did linear membership over it, so this was quadratic before any git
    call. The assertion is on the reported remainder, which only a bounded accumulator can
    still get right."""
    prompt = " ".join(f"AAA-{n}" for n in range(1000, 3000))
    keys, truncated = cd.workstream_keys(prompt)
    assert len(keys) == cd.WORKSTREAM_MAX_KEYS
    assert truncated == 2000 - cd.WORKSTREAM_MAX_KEYS


# --------------------------------------------------------------------------- the scan

def test_scan_finds_a_page_by_filename(vault):
    scan = cd.workstream_page_scan(["PLAT-3113"], repo=vault)
    assert scan["hits"] == {"PLAT-3113": ["brain/proj/PLAT-3113-rtbf-migration.md"]}, \
        "exact equality on purpose: a duplicate here ate the whole sample budget before"


def test_scan_does_not_duplicate_a_page_found_by_both_matchers(vault):
    """Reproduced before the fix: `target not in found` compared an un-suffixed target
    against a `.md`-suffixed list, so a page listed in its own index appeared twice and
    consumed both sample slots."""
    paths = cd.workstream_page_scan(["PLAT-3113"], repo=vault)["hits"]["PLAT-3113"]
    assert len(paths) == len(set(paths)) == 1


def test_scan_finds_a_page_only_an_index_row_names(vault):
    scan = cd.workstream_page_scan(["INE-857"], repo=vault)
    assert scan["hits"] == {"INE-857": ["brain/proj/kafka-gen4-cutover.md"]}


def test_scan_resolves_a_bare_stem_through_the_stem_map(vault):
    """`[[bare-stem-target]]` names a page in a subdirectory. The first version emitted
    `bare-stem-target.md` as a repo-root path, which resolves to nothing."""
    scan = cd.workstream_page_scan(["BARE-11"], repo=vault)
    assert scan["hits"] == {"BARE-11": ["brain/proj/deep/bare-stem-target.md"]}


def test_scan_drops_an_index_row_whose_page_is_not_committed(vault):
    """The dangling-row case — the exact defect wiki_index_scan exists to detect, on the
    same tree. The first version advertised it as a page to open; reproduced as
    `GHOST-42 -> brain/proj/ghost-page.md`, a file that is not in HEAD."""
    assert cd.workstream_page_scan(["GHOST-42"], repo=vault)["hits"] == {}


def test_scan_takes_the_link_from_the_rows_first_cell(vault):
    """A row with two links: the subject is in the first cell, the 'see' is not."""
    scan = cd.workstream_page_scan(["TWO-22"], repo=vault)
    assert scan["hits"] == {"TWO-22": ["brain/proj/kafka-gen4-cutover.md"]}


def test_scan_ignores_fenced_and_commented_rows(vault):
    """_wikilinks_in exists in this same file precisely because index files carry example
    links inside fences. The new scan bypassed it and read raw lines."""
    assert cd.workstream_page_scan(["FENCE-99"], repo=vault)["hits"] == {}
    assert cd.workstream_page_scan(["HIDDEN-77"], repo=vault)["hits"] == {}


def test_key_match_against_paths_is_bounded(vault):
    """`PLAT-311` matched `plat-3113-rtbf-migration.md` as a bare substring, so a real key
    that is a numeric prefix of another real key resolved to the wrong page."""
    assert cd.workstream_page_scan(["PLAT-311"], repo=vault)["hits"] == {}


def test_scan_returns_empty_hits_when_the_vault_has_nothing(vault):
    assert cd.workstream_page_scan(["ZZZ-999"], repo=vault)["hits"] == {}


def test_scan_returns_none_when_it_could_not_look(tmp_path):
    assert cd.workstream_page_scan(["PLAT-3113"], repo=tmp_path / "nope") is None
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.md").write_text("x")
    assert cd.workstream_page_scan(["PLAT-3113"], repo=plain) is None, \
        "a directory that is not a git repo has not been searched"


def test_scan_ignores_an_uncommitted_page(vault):
    (vault / "brain" / "proj" / "QQQ-42-secret.md").write_text("# not committed\n")
    assert cd.workstream_page_scan(["QQQ-42"], repo=vault)["hits"] == {}


def test_scan_reports_its_index_bound(vault, monkeypatch):
    """WIKI_INDEX_CAP's own comment in this file says the bound is REPORTED when hit. The
    first version sliced the same list and reported nothing."""
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    assert cd.workstream_page_scan(["INE-857"], repo=vault)["truncated_indexes"] == 1


def test_scan_bounds_total_subprocess_time_not_just_each_call(vault, monkeypatch):
    """A 24-file cap at 3s per call composed to a 75s worst case on the single event where
    latency is most visible. The deadline is aggregate."""
    monkeypatch.setattr(cd, "WORKSTREAM_SCAN_BUDGET", 0.0)
    assert cd.workstream_page_scan(["PLAT-3113"], repo=vault) is None, \
        "an exhausted budget must read as 'could not look', never as 'found nothing'"


# --------------------------------------------------------------------------- the advisory

def test_fires_when_the_page_exists_and_was_not_opened(live):
    msg, outcome = cd.workstream_page_context(_state(), "start on PLAT-3113 please")
    assert outcome == "unopened"
    assert "brain/proj/PLAT-3113-rtbf-migration.md" in msg


def test_silent_when_the_page_was_already_opened(live):
    st = _state(wiki_paths_read=["brain/proj/PLAT-3113-rtbf-migration.md"])
    assert cd.workstream_page_context(st, "continue PLAT-3113") == (None, "opened")


def test_a_read_of_a_suffix_lookalike_does_not_credit_the_page(live):
    """`'snapshot.md'.endswith('hot.md')` is True. Unanchored suffix matching let a Read of
    an unrelated file silence the advisory — the over-crediting direction, which this check
    must not have."""
    st = _state(wiki_paths_read=["brain/proj/not-PLAT-3113-rtbf-migration.md"])
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "unopened"


def test_silent_but_LOGGED_when_no_page_exists(live):
    assert cd.workstream_page_context(_state(), "look at ZZZ-999") == (None, "no-page")


def test_an_incomplete_scan_is_not_reported_as_no_page(live, monkeypatch):
    """Two reviewers found this independently. With the index cap at 0 the scan reads
    no index file, so the higher-recall half never runs — yet the caller reported a
    confident `no-page` AND settled the key, making the miss permanent for the
    session. An absence claim over a population the code knows it did not enumerate."""
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st = _state()
    msg, outcome = cd.workstream_page_context(st, "INE-857")
    assert outcome == "no-page-partial"
    assert msg is None
    assert "INE-857" not in st["workstream_keys_fired"], "an incomplete scan must not settle"


def test_a_hit_elsewhere_in_the_batch_does_not_mask_a_truncated_key(live, monkeypatch):
    """Fix round 1's guard (`scan["truncated_indexes"] and not hits`) is BATCH-level
    while settling is PER-KEY. WORKSTREAM_MAX_KEYS is 3, so two+ keys sharing one scan
    is the ordinary case, not a corner: PLAT-3113 matches by filename (unaffected by
    the index cap) and INE-857 is reachable ONLY through the vault's `_index.md` row.
    When the index half of the scan is truncated, INE-857 has no hit at all — but
    PLAT-3113 does, so `hits` is non-empty, the batch-level `not hits` guard never
    fires, and INE-857 used to be silently settled forever under an outcome that never
    named it.

    CONTROL arm (index cap at its normal value): both keys get a hit, both are named
    in the message, neither settles (unopened keys are never settled). TRUNCATED arm
    (cap 0): INE-857 has no hit and is not named — and must still not settle, because
    the fix gates settling on the scan's completeness, not on which keys in the batch
    happened to have a hit.
    """
    st_control = _state()
    msg, outcome = cd.workstream_page_context(st_control, "PLAT-3113 and INE-857")
    assert outcome == "unopened"
    assert "PLAT-3113" in msg and "INE-857" in msg
    assert "INE-857" not in st_control["workstream_keys_fired"]
    assert "PLAT-3113" not in st_control["workstream_keys_fired"]

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state()
    msg2, outcome2 = cd.workstream_page_context(st_trunc, "PLAT-3113 and INE-857")
    assert outcome2 == "unopened-partial"
    assert "PLAT-3113" in msg2
    assert "INE-857" not in msg2, "the truncated scan never found INE-857, so it cannot be named"
    assert "INE-857" not in st_trunc["workstream_keys_fired"], \
        "a truncated scan must not settle a key just because a batch-mate had a hit"
    assert "PLAT-3113" not in st_trunc["workstream_keys_fired"], \
        "unopened keys are never settled, truncated or not"

    assert (msg, outcome) != (msg2, outcome2), \
        "control and truncated arms must diverge, or this proves nothing about truncation"


def test_a_truncated_scan_does_not_settle_an_already_opened_hit_either(live, monkeypatch):
    """Branch-A ('not unopened') used to gate on `truncated_indexes and not hits` —
    batch-level on `hits` — so a key whose one known page was already read this
    session sailed straight through as 'opened' and got settled, even though the
    truncated index half might hold an index-only page for that SAME key that was
    never seen. The ruling: a truncated enumeration is partial for every key in the
    batch, including ones with a hit — no per-key carve-out.

    CONTROL (index cap normal): PLAT-3113's one known page is already read -> 'opened',
    settled. TRUNCATED (cap 0): same key, same already-read page, but the index half of
    the scan never ran -> must NOT settle, and 'opened' is not a claim the code can
    support with an unproven page list.
    """
    read = ["brain/proj/PLAT-3113-rtbf-migration.md"]

    st_control = _state(wiki_paths_read=list(read))
    assert cd.workstream_page_context(st_control, "PLAT-3113") == (None, "opened")
    assert "PLAT-3113" in st_control["workstream_keys_fired"]

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state(wiki_paths_read=list(read))
    msg, outcome = cd.workstream_page_context(st_trunc, "PLAT-3113")
    assert outcome == "no-page-partial"
    assert msg is None
    assert "PLAT-3113" not in st_trunc["workstream_keys_fired"]


def test_a_truncated_scan_does_not_settle_an_already_opened_key_on_the_message_path(
    live, vault, monkeypatch,
):
    """Site 2 (`_mark([k for k in fresh if k not in unopened])`, the message-path
    settle) has the same batch-level blindness as site 1: it used to settle every key
    NOT in `unopened` without checking whether the scan behind `unopened` was itself
    complete. PLAT-3113's one known page is already read, so it is never added to
    `unopened` — and used to be settled here even when a second key in the same batch
    was still unopened and the scan was truncated.

    ZQ-77 is a second filename-matched key added directly to the vault: unaffected by
    the index cap, so it stays unopened (and the message still fires) in BOTH arms —
    isolating the truncation's effect to the settling decision, not to whether a
    message is produced at all.
    """
    (vault / "brain" / "proj" / "ZQ-77-notes.md").write_text("# zq\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "second page")
    read = ["brain/proj/PLAT-3113-rtbf-migration.md"]

    st_control = _state(wiki_paths_read=list(read))
    msg, outcome = cd.workstream_page_context(st_control, "PLAT-3113 and ZQ-77")
    assert outcome == "unopened"
    assert "ZQ-77" in msg
    assert "PLAT-3113" in st_control["workstream_keys_fired"]

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state(wiki_paths_read=list(read))
    msg2, outcome2 = cd.workstream_page_context(st_trunc, "PLAT-3113 and ZQ-77")
    assert outcome2 == "unopened-partial"
    assert "ZQ-77" in msg2
    assert "PLAT-3113" not in st_trunc["workstream_keys_fired"], \
        "a truncated scan must not settle a key just because its only known page was already read"


def test_no_key_in_prompt_is_a_third_state_not_clean(live):
    assert cd.workstream_page_context(_state(), "just fix the typo") == (None, None)


def test_fires_once_per_key_for_a_settled_key(live):
    st = _state()
    assert cd.workstream_page_context(st, "ZZZ-999?")[1] == "no-page"
    assert cd.workstream_page_context(st, "ZZZ-999 again")[1] == "already-advised"


def test_an_unopened_key_is_NOT_settled_and_re_fires(live):
    """additionalContext has been measured being dropped mid-session while every stamp
    advanced. Settling here would burn the key on a message that may never have arrived —
    losing the first prompt about that workstream, which is the one this check is for."""
    st = _state()
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "unopened"
    assert cd.workstream_page_context(st, "PLAT-3113 still")[1] == "unopened"
    assert "PLAT-3113" not in st["workstream_keys_fired"]


def test_could_not_look_settles_the_key_so_it_does_not_rescan_forever(live, monkeypatch):
    """The packaged default wiki_path points at a directory most installs do not have. An
    unsettled 'skipped' writes a fire-log line on every ticket-mentioning prompt, forever,
    burying the outcomes the log exists to carry."""
    monkeypatch.setattr(cd, "WIKI_DIR", Path("/nonexistent-vault"))
    st = _state()
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "skipped"
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "already-advised"


def test_scan_count_has_a_session_backstop(live):
    st = _state(workstream_scans=cd.WORKSTREAM_MAX_SCANS)
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "scan-budget-spent"


def test_settled_keys_do_not_consume_the_per_prompt_bound(live):
    """Reproduced before the fix: a prompt naming four keys settles the first three,
    and the fourth — which HAS a committed page — is unreachable for the rest of the
    session, because the bound was applied before the settled-filter."""
    st = _state(workstream_keys_fired=["AAA-11", "BBB-22", "CCC-33"])
    keys, truncated = cd.workstream_keys(
        "AAA-11 BBB-22 CCC-33 PLAT-3113", exclude=st["workstream_keys_fired"])
    assert keys == ["PLAT-3113"]
    assert truncated == 0


def test_denylisted_token_alone_is_no_key_not_already_advised(live):
    """The 'no keys survived exclusion' branch used to decide already-advised-vs-nothing
    with a raw WORKSTREAM_KEY_RE.search, which applies the shape test only and skips the
    denylist workstream_keys applies. AES-256 is denylisted (SHA3-256, UTF-8, RFC-2119,
    CVE-2021-4034 are the same class): a fresh session naming only it named no real
    workstream key, so this must be silent and unlogged, not a false already-advised row
    written to the fire log for a key nobody was ever advised on."""
    assert cd.workstream_page_context(_state(), "explain AES-256 padding") == (None, None)


def test_denylisted_token_plus_settled_key_still_reports_already_advised(live):
    """The discriminating case: a denylisted token sits next to a real, already-settled
    key in the same prompt. A fix that simply deleted the already-advised branch (instead
    of re-deriving it from the denylist-aware extraction) would pass the test above alone
    while silently losing this one — the settled key must still produce already-advised,
    not None."""
    st = _state(workstream_keys_fired=["PLAT-3113"])
    assert cd.workstream_page_context(st, "AES-256 and PLAT-3113 again") == (None, "already-advised")


def test_truncation_is_reported_in_the_message(live, monkeypatch):
    monkeypatch.setattr(cd, "WORKSTREAM_MAX_KEYS", 1)
    msg, outcome = cd.workstream_page_context(_state(), "PLAT-3113 and INE-857 and ZZZ-999")
    assert outcome == "unopened"
    assert "Incomplete coverage" in msg and "not checked" in msg


def test_already_read_pages_do_not_consume_the_sample_budget(live, vault):
    """Reproduced before the fix: three pages for one key, two already read, both
    sample slots spent on them, and the third — the unread one — never named. The
    check was silenced by the two pages the agent happened to open first.

    Uses `ZQ-11` rather than the brief's literal `K-11`: a single-letter prefix is
    deliberately not a key (test_single_letter_prefix_is_not_a_key, same file) so
    `K-11` never reaches workstream_page_scan at all — it fails at key extraction,
    not at the sample bound this test exists to cover. `ZQ` is a two-letter prefix,
    not in WORKSTREAM_KEY_DENY, and unused by any other fixture in this file."""
    for name in ("ZQ-11-aaa", "ZQ-11-bbb", "ZQ-11-ccc"):
        (vault / "brain" / "proj" / f"{name}.md").write_text("x")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "three pages")
    st = _state(wiki_paths_read=["brain/proj/ZQ-11-aaa.md", "brain/proj/ZQ-11-bbb.md"])
    msg, outcome = cd.workstream_page_context(st, "ZQ-11")
    assert outcome == "unopened"
    assert "brain/proj/ZQ-11-ccc.md" in msg


def test_state_is_persisted_against_the_freshest_copy(live):
    """The mark re-loads before writing. Writing back the pre-scan snapshot would revert
    every counter a sub-agent advanced during the scan window — sub-agents share this
    session_id and do their own unlocked load-modify-save."""
    st = _state()
    cd.save_state(st)
    concurrent = cd.load_state("s1")
    concurrent["aggregate_reads"] = 99          # stands in for a concurrent writer
    cd.save_state(concurrent)
    cd.workstream_page_context(st, "ZZZ-999")   # settles a key, re-loads, saves
    after = cd.load_state("s1")
    assert after["aggregate_reads"] == 99, \
        "the concurrent counter must survive this handler's write"
    assert "ZZZ-999" in after["workstream_keys_fired"]


# --------------------------------------------------------------------------- recording site

def _pre_tool(tool_name, tool_input):
    cd.handle_pre_tool({"session_id": "s1", "tool_name": tool_name,
                        "tool_input": tool_input})
    return cd.load_state("s1")


def test_a_Read_of_a_vault_page_is_recorded_relative(live):
    """Drives the real handler. The predecessor of this test set a counter the function
    under test never reads, and a mutation deleting the guard changed nothing."""
    st = _pre_tool("Read", {"file_path": str(live / "brain/proj/kafka-gen4-cutover.md")})
    assert st["wiki_paths_read"] == ["brain/proj/kafka-gen4-cutover.md"]


def test_a_Grep_over_the_vault_is_NOT_recorded_as_opening_a_page(live):
    """A sweep is not a consultation. This is the assertion the first version's
    same-named test never made — it would have passed with the guard deleted."""
    st = _pre_tool("Grep", {"path": str(live / "brain"), "pattern": "x"})
    assert st["wiki_paths_read"] == []
    assert st["wiki_read_count"] == 1, "the sweep still counts as vault consumption"


def test_a_Read_through_the_docs_core_mount_is_recorded_too(live):
    """The same page is reachable at two absolute paths on this machine. Recording only
    the canonical one made the advisory assert 'you have not opened it' about a page the
    session had just opened."""
    st = _pre_tool("Read", {"file_path": "/x/dev/claude-core/docs/core/brain/proj/a.md"})
    assert st["wiki_paths_read"] == ["brain/proj/a.md"]


def test_recorded_paths_are_bounded(live, monkeypatch):
    monkeypatch.setattr(cd, "WIKI_READ_PATHS_CAP", 3)
    st = None
    for n in range(6):
        st = _pre_tool("Read", {"file_path": str(live / f"brain/p{n}.md")})
    assert st["wiki_paths_read"] == ["brain/p3.md", "brain/p4.md", "brain/p5.md"]


# --------------------------------------------------------------------------- the wiring

def test_the_advisory_reaches_additionalContext(live, capsys):
    """No test drove handle_user_prompt_submit at all. Given this repo's own
    fired-is-not-delivered finding, the check's entire evidence story was asserted by
    nothing."""
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "work on PLAT-3113"})
    out = capsys.readouterr().out.strip()
    assert out, "the handler emitted nothing"
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "Read before work" in ctx
    assert "brain/proj/PLAT-3113-rtbf-migration.md" in ctx


def test_a_failing_scan_never_breaks_the_prompt(live, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("git exploded")
    monkeypatch.setattr(cd, "workstream_page_scan", boom)
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "work on PLAT-3113"})
    out = capsys.readouterr().out.strip()
    assert "Read before work" not in out


def test_the_outcome_reaches_the_fire_log(live, fired):
    """Nothing asserted that the outcome was logged — the fire log is this check's
    only evidence channel, and ROADMAP.md calls it the one instrument we trust."""
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "work on PLAT-3113"})
    assert ("workstream_page", "unopened") in fired
