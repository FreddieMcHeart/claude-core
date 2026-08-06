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
    """Collects (rule, outcome, severity) triples instead of appending to the real fire log.

    Severity is log_fire's THIRD POSITIONAL argument. The first version of this stub swallowed
    it in `*a` and recorded only (rule, outcome), which made the severity untestable — and a
    stub that drops the field under test is how such a test looks green while asserting nothing.
    """
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
        lambda rule, *a, **k: fired.append(
            (rule, k.get("outcome"), a[1] if len(a) > 1 else None)
        ),
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


def test_a_seven_digit_issue_number_is_not_dropped():
    """`\\d{1,6}` plus a lookahead rejecting alnum meant every backtrack failed on a
    7-digit number, so PLAT-1234567 was dropped ENTIRELY rather than truncated. Large
    Jira instances reach seven digits; that is loss of a real key."""
    assert cd.workstream_keys("close PLAT-1234567")[0] == ["PLAT-1234567"]


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


def test_an_ambiguous_bare_stem_resolves_to_nothing(vault):
    """`stems` was a dict built by iterating a SET, so a colliding basename resolved
    to whichever path set iteration happened to yield — reproduced across three
    processes as z/dup.md, a/dup.md, z/dup.md for identical input. Determinism is not
    the fix: the advisory states the path as fact, so the wrong page reliably is worse
    than no page at all.

    CONTROL: BARE-11 is already in the `vault` fixture as a bare stem with exactly one
    match, and this test asserts it STILL resolves in the same scan call — otherwise
    this would pass equally under an implementation that resolves every bare stem, not
    just the colliding one, to nothing.
    """
    for d in ("one", "two"):
        (vault / "brain" / d).mkdir(exist_ok=True)
        (vault / "brain" / d / "dup.md").write_text("x")
    (vault / "brain" / "collide_index").mkdir(exist_ok=True)
    (vault / "brain" / "collide_index" / "_index.md").write_text(
        "| [[dup]] | COLL-11 |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "colliding stems")
    scan = cd.workstream_page_scan(["COLL-11", "BARE-11"], repo=vault)
    assert scan["hits"].get("COLL-11") is None, "the collision must not resolve to either page"
    assert scan["hits"]["BARE-11"] == ["brain/proj/deep/bare-stem-target.md"], \
        "an unrelated, unambiguous bare stem must still resolve"
    assert scan["ambiguous_keys"] == {"COLL-11"}


def test_scan_drops_an_index_row_whose_page_is_not_committed(vault):
    """The dangling-row case — the exact defect wiki_index_scan exists to detect, on the
    same tree. The first version advertised it as a page to open; reproduced as
    `GHOST-42 -> brain/proj/ghost-page.md`, a file that is not in HEAD."""
    assert cd.workstream_page_scan(["GHOST-42"], repo=vault)["hits"] == {}
    assert cd.workstream_page_scan(["GHOST-42"], repo=vault)["ambiguous_keys"] == set(), \
        "a dangling link is a definite no-page answer, not an ambiguous one"


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


def test_an_unterminated_fence_is_reported_not_swallowed(live, vault):
    """`_wikilinks_in` in this same file returns its fence state and `wiki_index_scan`
    records the path as unparsed; the copy here dropped that. Reproduced: a row below
    an unterminated fence is invisible AND truncated_indexes stays 0, so a partial
    read of one file reports full coverage."""
    (vault / "brain" / "proj" / "broken.md").write_text("# broken\n")
    (vault / "brain" / "fenced").mkdir(exist_ok=True)
    (vault / "brain" / "fenced" / "_index.md").write_text(
        "```\nnever closed\n\n| [[brain/proj/broken]] | LIVE-11 below the break |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "unterminated fence")
    scan = cd.workstream_page_scan(["LIVE-11"], repo=vault)
    assert scan["hits"] == {}, "the row below the fence is not readable"
    assert scan["truncated_indexes"] >= 1, "and the file must be counted as not fully read"


def test_scan_bounds_total_subprocess_time_not_just_each_call(vault, monkeypatch):
    """A 24-file cap at 3s per call composed to a 75s worst case on the single event where
    latency is most visible. The deadline is aggregate."""
    monkeypatch.setattr(cd, "WORKSTREAM_SCAN_BUDGET", 0.0)
    assert cd.workstream_page_scan(["PLAT-3113"], repo=vault) is None, \
        "an exhausted budget must read as 'could not look', never as 'found nothing'"


# --------------------------------------------------------------------------- the advisory

def test_fires_when_the_page_exists_and_was_not_opened(live):
    msg, outcome, _ = cd.workstream_page_context(_state(), "start on PLAT-3113 please")
    assert outcome == "unopened"
    assert "brain/proj/PLAT-3113-rtbf-migration.md" in msg


def test_silent_when_the_page_was_already_opened(live):
    st = _state(wiki_paths_read=["brain/proj/PLAT-3113-rtbf-migration.md"])
    assert cd.workstream_page_context(st, "continue PLAT-3113") == (None, "opened", {})


def test_a_read_of_a_suffix_lookalike_does_not_credit_the_page(live, vault):
    """`'snapshot.md'.endswith('hot.md')` is True. Unanchored suffix matching let a Read of
    an unrelated file silence the advisory — the over-crediting direction, which this check
    must not have.

    FIXTURE REBUILT 2026-08-06, because the original could not fail. It read
    `brain/proj/not-PLAT-3113-rtbf-migration.md` against the page
    `brain/proj/PLAT-3113-rtbf-migration.md` — inserting `not-` BETWEEN the directory and
    the filename, which breaks the bare-suffix collision instead of exhibiting it:
    `r.endswith(p)` is False there, so the naive pre-fix form produced the identical
    result and the test passed against the very bug it was named for. Confirmed by
    mutation: reverting the anchor to `any(r.endswith(p) for r in read)` left all 63
    tests in this file green — the whole suite, not just this one, was blind to it.

    The rebuilt fixture uses a page at the VAULT ROOT, which is the docstring's own
    example and the only shape where the collision is real: with `p = "PLAT-7-hot.md"`
    and `r = "snapshot-PLAT-7-hot.md"`, `r.endswith(p)` is True while
    `r.endswith("/" + p)` is False. The naive form credits the page, the anchored form
    does not, and the two arms now differ.
    """
    (vault / "PLAT-7-hot.md").write_text("# hot\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "a page at the vault root")

    # CONTROL: reading the page itself credits it, so the advisory stays silent.
    st_read = _state(wiki_paths_read=["PLAT-7-hot.md"])
    assert cd.workstream_page_context(st_read, "PLAT-7")[1] == "opened"

    # TREATMENT: a DIFFERENT file whose name merely ends with the page's name must not.
    st_look = _state(wiki_paths_read=["snapshot-PLAT-7-hot.md"])
    assert cd.workstream_page_context(st_look, "PLAT-7")[1] == "unopened", \
        "an unanchored endswith credits a file that is not the page"


def test_silent_but_LOGGED_when_no_page_exists(live):
    assert cd.workstream_page_context(_state(), "look at ZZZ-999") == (None, "no-page", {})


def test_an_incomplete_scan_is_not_reported_as_no_page(live, monkeypatch):
    """Two reviewers found this independently. With the index cap at 0 the scan reads
    no index file, so the higher-recall half never runs — yet the caller reported a
    confident `no-page` AND settled the key, making the miss permanent for the
    session. An absence claim over a population the code knows it did not enumerate.

    `details` (Finding 2, round 2): `no-page-partial` returns no message, so the cause
    has to travel some other way or a fire-log reader can't tell "raise the cap" from
    "rename a colliding page" apart. Here it's the index cap, so `details` names that.
    """
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st = _state()
    msg, outcome, details = cd.workstream_page_context(st, "INE-857")
    assert outcome == "no-page-partial"
    assert msg is None
    assert "INE-857" not in st["workstream_keys_fired"], "an incomplete scan must not settle"
    assert details == {"truncated_indexes": 1, "index_over_cap": 1}


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
    msg, outcome, _ = cd.workstream_page_context(st_control, "PLAT-3113 and INE-857")
    assert outcome == "unopened"
    assert "PLAT-3113" in msg and "INE-857" in msg
    assert "INE-857" not in st_control["workstream_keys_fired"]
    assert "PLAT-3113" not in st_control["workstream_keys_fired"]

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state()
    msg2, outcome2, _ = cd.workstream_page_context(st_trunc, "PLAT-3113 and INE-857")
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

    THE OUTCOME NAME, corrected 2026-08-06. This test used to assert `no-page-partial`
    here, and that assertion was wrong in the direction the whole branch is about: a
    page for PLAT-3113 exists in this fixture and was read this session, so a name
    meaning "the scan found nothing, and it was partial" states something false. The
    settling behaviour it was written to guard is unchanged and still asserted below;
    only the label moves, to `opened-partial` — which is what the complete path already
    calls this case (`opened`), qualified the same way truncation qualifies everything
    else. Found by probing the return site during review: `hits` was non-empty at the
    moment the code returned "no page".
    """
    read = ["brain/proj/PLAT-3113-rtbf-migration.md"]

    st_control = _state(wiki_paths_read=list(read))
    assert cd.workstream_page_context(st_control, "PLAT-3113") == (None, "opened", {})
    assert "PLAT-3113" in st_control["workstream_keys_fired"]

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state(wiki_paths_read=list(read))
    msg, outcome, details = cd.workstream_page_context(st_trunc, "PLAT-3113")
    assert outcome == "opened-partial", \
        "a page exists and was opened; only the enumeration was partial"
    assert msg is None
    assert "PLAT-3113" not in st_trunc["workstream_keys_fired"]
    assert details == {"truncated_indexes": 1, "index_over_cap": 1}


def test_a_truncated_scan_with_no_hit_at_all_is_still_no_page_partial(live, monkeypatch):
    """The other arm of the split above, and the reason it is a split rather than a
    rename: `opened-partial` must NOT swallow the genuinely-empty case.

    ZZZ-999 has no page anywhere in the fixture vault, so `hits` is empty. Same cap-0
    truncation as the test above, same branch, same `details` — and the outcome must
    still be `no-page-partial`. Without this arm, changing the production line to return
    `opened-partial` unconditionally would pass the test above and lose the distinction
    entirely, which is exactly the collapse being repaired.
    """
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st = _state()
    msg, outcome, details = cd.workstream_page_context(st, "ZZZ-999")
    assert outcome == "no-page-partial", "no page exists for this key; the name must say so"
    assert msg is None
    assert details == {"truncated_indexes": 1, "index_over_cap": 1}


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
    msg, outcome, _ = cd.workstream_page_context(st_control, "PLAT-3113 and ZQ-77")
    assert outcome == "unopened"
    assert "ZQ-77" in msg
    assert "PLAT-3113" in st_control["workstream_keys_fired"]

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state(wiki_paths_read=list(read))
    msg2, outcome2, _ = cd.workstream_page_context(st_trunc, "PLAT-3113 and ZQ-77")
    assert outcome2 == "unopened-partial"
    assert "ZQ-77" in msg2
    assert "PLAT-3113" not in st_trunc["workstream_keys_fired"], \
        "a truncated scan must not settle a key just because its only known page was already read"


def _collide(vault):
    """Commit a stem collision (`dup.md` in two directories) plus an index row that
    names it under COLL-11. Shared setup for the ambiguity tests below."""
    for d in ("one", "two"):
        (vault / "brain" / d).mkdir(exist_ok=True)
        (vault / "brain" / d / "dup.md").write_text("x")
    (vault / "brain" / "collide_index").mkdir(exist_ok=True)
    (vault / "brain" / "collide_index" / "_index.md").write_text(
        "| [[dup]] | COLL-11 |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "colliding stems")


def test_an_ambiguous_key_is_not_settled_and_not_claimed_no_page(live, vault):
    """The vault genuinely HAS a page for COLL-11 — two of them — so claiming 'no-page'
    would assert something false, and settling the key would make that false claim
    permanent for the session (workstream_keys_fired is never rechecked once a key is
    in it). The controller's ruling: ambiguity must read as UNPROVEN, the same way an
    incomplete index scan does, not as a confident 'no page here'.

    CONTROL: ZZZ-999, an ordinary key with genuinely no page anywhere in the vault,
    settles normally as 'no-page' in a SEPARATE call — proving the block is specific to
    the key whose own resolution was ambiguous, not a side effect of an ambiguous stem
    existing somewhere in the vault.

    `details` (Finding 2, round 2): the silent `no-page-partial` branch names its cause.
    """
    _collide(vault)

    st = _state()
    msg, outcome, details = cd.workstream_page_context(st, "COLL-11")
    assert msg is None
    assert outcome == "no-page-partial"
    assert "COLL-11" not in st["workstream_keys_fired"]
    assert details == {"ambiguous_keys": ["COLL-11"]}

    st_control = _state()
    assert cd.workstream_page_context(st_control, "ZZZ-999") == (None, "no-page", {})
    assert "ZZZ-999" in st_control["workstream_keys_fired"]


def test_ambiguity_in_one_key_does_not_hold_back_an_unrelated_key(live, vault, monkeypatch):
    """INVERTED (round 2, Finding 1) from this test's first version, which was named
    `..._holds_back_the_whole_batch_like_truncation_does` and asserted the opposite:
    that COLL-11's collision also blocked ZZZ-999, an unrelated key in the same prompt,
    from settling. That assertion followed directly from round 1's own ruling that
    ambiguity "joins the same gate" as `truncated_indexes` — the test faithfully
    encoded that ruling, and the ruling was wrong, confirmed by the coordinator's own
    execution. It is being replaced, not deleted, because the wrongness was in the
    ruling, not in the test's fidelity to it.

    THE INVARIANT, stated once here because a reader who sees only one arm might "fix"
    the asymmetry back: a truncated index enumeration is a property of the SCAN — some
    index files were never read, so every key in this batch has an unproven page list,
    hit or not. A colliding stem is a property of a KEY — it affects only the keys
    whose rows actually link it. Gating settlement on `ambiguous` being merely
    non-empty punished ZZZ-999, which never went near the collision. The two causes are
    deliberately different from here on; the truncation arm below is the contrast that
    makes the difference legible instead of looking like an oversight.
    """
    _collide(vault)

    # Ambiguity arm: COLL-11 collides, ZZZ-999 has no page anywhere. Both have zero
    # hits, so this lands in the "not unopened" branch. ZZZ-999 must still settle.
    st = _state()
    msg, outcome, details = cd.workstream_page_context(st, "COLL-11 and ZZZ-999")
    assert msg is None
    assert outcome == "no-page-partial"
    assert "COLL-11" not in st["workstream_keys_fired"], "the colliding key itself stays unproven"
    assert "ZZZ-999" in st["workstream_keys_fired"], \
        "an unrelated key in the same prompt must settle despite its batch-mate's collision"
    assert details == {"ambiguous_keys": ["COLL-11"]}

    # Truncation arm, same branch shape (INE-857 and ZZZ-999 both have zero hits when
    # the index half can't be read, so this also lands in "not unopened"): truncation
    # IS batch-wide and must still hold both keys back — the contrast this test exists
    # to keep visible.
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st_trunc = _state()
    msg2, outcome2, details2 = cd.workstream_page_context(st_trunc, "INE-857 and ZZZ-999")
    assert msg2 is None
    assert outcome2 == "no-page-partial"
    assert "INE-857" not in st_trunc["workstream_keys_fired"]
    assert "ZZZ-999" not in st_trunc["workstream_keys_fired"], \
        "a truncated index enumeration IS batch-wide and must still hold everyone back"
    assert details2 == {"truncated_indexes": 2, "index_over_cap": 2}, \
        "_collide added a second _index.md to the vault, so both are uncounted at cap 0"


def test_ambiguity_does_not_hold_back_an_already_read_batch_mate_in_the_unopened_branch(
    live, vault,
):
    """Same invariant as the test above (Finding 1), exercised on the OTHER settling
    site: the "unopened" branch's `_mark([k for k in fresh if k not in unopened])`,
    which used to be guarded by `scan["truncated_indexes"] or ambiguous` — batch-wide —
    and is now guarded by `scan["truncated_indexes"]` alone, with ambiguity filtered
    per key inside the `_mark` call. This is closer to the coordinator's own
    reproduction: a key with a real, unread hit (PLAT-3113) forces this branch; ZQ-77
    is a second, unrelated key with its own real hit that is ALREADY read and would
    ordinarily settle; COLL-11 is the colliding key. Before this fix, ZQ-77 never
    settled just because COLL-11, a batch-mate it has nothing to do with, collided.
    """
    (vault / "brain" / "proj" / "ZQ-77-notes.md").write_text("# zq\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "second page")
    _collide(vault)

    st = _state(wiki_paths_read=["brain/proj/ZQ-77-notes.md"])
    msg, outcome, details = cd.workstream_page_context(st, "PLAT-3113 and ZQ-77 and COLL-11")
    assert outcome == "unopened-partial"
    assert "PLAT-3113" in msg
    assert "PLAT-3113" not in st["workstream_keys_fired"], "unopened keys never settle, ambiguity or not"
    assert "ZQ-77" in st["workstream_keys_fired"], \
        "a clean, already-read batch-mate must settle despite COLL-11's collision"
    assert "COLL-11" not in st["workstream_keys_fired"], "the colliding key itself stays unproven"
    assert details == {}, "this branch reaches a message, which already names the cause in prose"


def test_ambiguity_is_reported_in_the_message_caveats(live, vault):
    """Ruling point 3: ambiguity must show up in `Incomplete coverage:`, in the same
    caveat list as the index-file and page-sample bounds, in the same voice — not fold
    silently into 'unopened'. PLAT-3113 has a real, unread hit (by filename, unaffected
    by the collision) so this exercises the branch that DOES produce a message."""
    _collide(vault)
    msg, outcome, details = cd.workstream_page_context(_state(), "PLAT-3113 and COLL-11")
    assert outcome == "unopened-partial"
    assert "PLAT-3113" in msg
    assert "COLL-11" not in msg, "COLL-11 has no resolved page to name"
    assert "Incomplete coverage" in msg
    assert "claimed by more than one committed page" in msg
    assert details == {}, "the message already carries the cause; no need to duplicate it"


def test_no_page_partial_names_its_cause_in_log_details(live, vault, monkeypatch):
    """Finding 2 (round 2): `no-page-partial` returns no message — the branch returns
    before the caveats are built — so before this fix a fire-log reader saw only the
    outcome name and could not tell a truncated index enumeration (remedy: raise
    WIKI_INDEX_CAP) apart from a colliding stem (remedy: rename a page); those are two
    different fixes. The cause now travels as the third return element, `details`, kept
    OUT of the outcome string on purpose — the vocabulary is already eight names long
    and the cause composes with several of them; a field composes, a name multiplies.
    Both causes exercised here so the two `details` shapes are visibly different, not
    just individually non-empty.
    """
    _collide(vault)
    _, outcome, details = cd.workstream_page_context(_state(), "COLL-11")
    assert outcome == "no-page-partial"
    assert details == {"ambiguous_keys": ["COLL-11"]}

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    _, outcome2, details2 = cd.workstream_page_context(_state(), "INE-857")
    assert outcome2 == "no-page-partial"
    assert details2 == {"truncated_indexes": 2, "index_over_cap": 2}, \
        "_collide added a second _index.md to the vault, so both are uncounted at cap 0"


def test_no_page_partial_cause_reaches_the_fire_log(live, vault, monkeypatch):
    """Finding 2's wiring half: `details` has to actually reach `log_fire`, not just the
    return value `workstream_page_context` hands back. Drives `handle_user_prompt_submit`
    for real — a test that only checked the return tuple would pass even if the call
    site forgot to unpack the third element and pass it through as `**details`."""
    _collide(vault)
    calls = []
    monkeypatch.setattr(
        cd, "log_fire",
        lambda rule, sid, action, **k: calls.append((rule, action, k)),
    )
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "work on COLL-11"})
    rows = [c for c in calls if c[0] == "workstream_page"]
    assert len(rows) == 1
    _, action, details = rows[0]
    assert action == "info", "no-page-partial produces no advisory, so this must log at info"
    assert details == {"outcome": "no-page-partial", "ambiguous_keys": ["COLL-11"]}


def test_no_key_in_prompt_is_a_third_state_not_clean(live):
    assert cd.workstream_page_context(_state(), "just fix the typo") == (None, None, {})


def test_fires_once_per_key_for_a_settled_key(live):
    st = _state()
    assert cd.workstream_page_context(st, "ZZZ-999?")[1] == "no-page"
    assert cd.workstream_page_context(st, "ZZZ-999 again")[1] == "already-settled"


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
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "already-settled"


def test_scan_count_has_a_session_backstop(live):
    st = _state(workstream_scans=cd.WORKSTREAM_MAX_SCANS)
    assert cd.workstream_page_context(st, "PLAT-3113")[1] == "scan-budget-spent"


def test_scan_budget_advances_regardless_of_whether_the_scan_settles(live, vault, monkeypatch):
    """`workstream_scans` is a cost budget on git subprocess work (a `git ls-tree` plus a
    `git show` per index file), not a counter of settled keys. It used to live inside
    `_mark`, which the `no-page-partial` / `unopened-partial` branches never call —
    reproduced before this fix: those branches ran a full scan and left the counter at
    0, so a vault permanently stuck in one of them (a permanently colliding stem, or a
    permanently over-cap index file count) re-ran the same subprocess work on every
    matching prompt for the rest of the session, and WORKSTREAM_MAX_SCANS never engaged.

    Arm 1 — ambiguous partial: no settling, budget still advances by one. Also the arm
    that reads the counter back through `cd.load_state("s1")` rather than only from the
    dict passed in (Finding 3, round 2): every prior assertion here read `st1[...]`,
    the dict THIS test built and passed in, never the copy the handler actually
    persists — so a mutation deleting the disk-persist block while leaving the
    in-memory `state["workstream_scans"] = ...` line untouched left every such
    assertion green while `WORKSTREAM_MAX_SCANS` silently stopped bounding anything
    across prompts, which is the exact failure round 1 existed to fix.
    Arm 2 — CONTROL, a complete scan that DOES settle: same one-count advance, proving
    the fix relocated the increment rather than duplicating it (both arms must move, or
    an implementation that still only increments on settling would pass Arm 2 alone).
    Arm 3 — truncated-index partial: the OTHER cause of a partial outcome, same fix.
    """
    _collide(vault)

    st1 = _state()
    _, outcome1, _ = cd.workstream_page_context(st1, "COLL-11")
    assert outcome1 == "no-page-partial"
    assert "COLL-11" not in st1["workstream_keys_fired"]
    assert st1["workstream_scans"] == 1
    assert cd.load_state("s1")["workstream_scans"] == 1, \
        "the counter the handler actually persists to disk, not the dict passed in"

    st2 = _state()
    _, outcome2, _ = cd.workstream_page_context(st2, "ZZZ-999")
    assert outcome2 == "no-page"
    assert "ZZZ-999" in st2["workstream_keys_fired"]
    assert st2["workstream_scans"] == 1

    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    st3 = _state()
    _, outcome3, _ = cd.workstream_page_context(st3, "INE-857")
    assert outcome3 == "no-page-partial"
    assert "INE-857" not in st3["workstream_keys_fired"]
    assert st3["workstream_scans"] == 1


def test_scan_budget_does_not_advance_when_every_key_is_already_settled(live):
    """This is the arm that catches 'increment everywhere': a prompt whose only key is
    already settled short-circuits at the `if not keys:` branch, before
    workstream_page_scan is ever called — no git work happens, so the budget must not
    move. An implementation that bumps the counter unconditionally at the top of the
    handler (rather than only when a scan actually runs) would pass the arms above and
    still fail this one.
    """
    st = _state(workstream_keys_fired=["PLAT-3113"])
    _, outcome, _ = cd.workstream_page_context(st, "PLAT-3113 again")
    assert outcome == "already-settled"
    assert st["workstream_scans"] == 0


def test_settled_keys_do_not_consume_the_per_prompt_bound(live):
    """Reproduced before the fix: a prompt naming four keys settles the first three,
    and the fourth — which HAS a committed page — is unreachable for the rest of the
    session, because the bound was applied before the settled-filter."""
    st = _state(workstream_keys_fired=["AAA-11", "BBB-22", "CCC-33"])
    keys, truncated = cd.workstream_keys(
        "AAA-11 BBB-22 CCC-33 PLAT-3113", exclude=st["workstream_keys_fired"])
    assert keys == ["PLAT-3113"]
    assert truncated == 0


def test_denylisted_token_alone_is_no_key_not_already_settled(live):
    """The 'no keys survived exclusion' branch used to decide already-settled-vs-nothing
    with a raw WORKSTREAM_KEY_RE.search, which applies the shape test only and skips the
    denylist workstream_keys applies. AES-256 is denylisted (SHA3-256, UTF-8, RFC-2119,
    CVE-2021-4034 are the same class): a fresh session naming only it named no real
    workstream key, so this must be silent and unlogged, not a false already-settled row
    written to the fire log for a key nobody was ever advised on."""
    assert cd.workstream_page_context(_state(), "explain AES-256 padding") == (None, None, {})


def test_denylisted_token_plus_settled_key_still_reports_already_settled(live):
    """The discriminating case: a denylisted token sits next to a real, already-settled
    key in the same prompt. A fix that simply deleted the already-settled branch (instead
    of re-deriving it from the denylist-aware extraction) would pass the test above alone
    while silently losing this one — the settled key must still produce already-settled,
    not None."""
    st = _state(workstream_keys_fired=["PLAT-3113"])
    assert cd.workstream_page_context(st, "AES-256 and PLAT-3113 again") == (
        None, "already-settled", {})


def test_truncation_is_reported_in_the_message(live, monkeypatch):
    monkeypatch.setattr(cd, "WORKSTREAM_MAX_KEYS", 1)
    msg, outcome, _ = cd.workstream_page_context(_state(), "PLAT-3113 and INE-857 and ZZZ-999")
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
    msg, outcome, _ = cd.workstream_page_context(st, "ZQ-11")
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
    assert ("workstream_page", "unopened", "warn") in fired


def _severity_for(fired):
    """(outcome, severity) of the single workstream_page row, asserting there is exactly one.

    The count assertion is load-bearing: an arm that logged twice, or not at all, would
    otherwise be read through whichever row happened to be first.
    """
    rows = [r for r in fired if r[0] == "workstream_page"]
    assert len(rows) == 1, f"expected exactly one row, got {rows}"
    return rows[0][1], rows[0][2]


def test_severity_marks_an_advisory_that_fired_whatever_the_outcome_is_called(
    live, fired, monkeypatch
):
    """Three arms, because a one-armed version of this passes under a predicate that
    returns "warn" unconditionally — a different bug wearing the same green.

    The severity answers "was an advisory SHOWN", and it used to be decided by comparing
    the outcome against the literal "unopened". When a truncated scan gained its own
    outcome name, a fired advisory started logging at info: the behaviour was right and
    the record of it was wrong, in a line the author of that change had no reason to open.
    """
    # Arm 1 — complete scan, advisory fires.
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "work on PLAT-3113"})
    assert _severity_for(fired) == ("unopened", "warn")

    # Arm 2 — truncated scan, advisory still fires. Same event, different outcome name.
    fired.clear()
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 0)
    cd.handle_user_prompt_submit({"session_id": "s2", "prompt": "work on PLAT-3113"})
    assert _severity_for(fired) == ("unopened-partial", "warn")

    # Arm 3 — truncated scan, nothing shown. The control: it must NOT be warn, or arms 1
    # and 2 would pass under a predicate that never says info.
    fired.clear()
    cd.handle_user_prompt_submit({"session_id": "s3", "prompt": "work on ZZZ-999"})
    assert _severity_for(fired) == ("no-page-partial", "info")


def test_a_transient_scan_failure_does_not_settle_the_key(live, monkeypatch):
    """A one-off timeout must not blind the feature for the rest of the session.

    `scan is None` used to settle unconditionally, justified by a comment about an
    install whose wiki_path points nowhere rescanning forever. That justification had
    outlived its condition: a later fix moved `workstream_scans` to increment on every
    call that reaches a scan, so rescanning is now bounded at WORKSTREAM_MAX_SCANS
    whether or not the key settles. Meanwhile the settle was doing real harm — a single
    slow `git` permanently suppressed the advisory for a key whose page exists and is
    unopened, which is the exact failure this check is built to prevent.

    CONTROL is the third arm and it carries the finding: with no failure at all the same
    key produces `unopened` WITH a message. Without it, `skipped` on both arms would
    read as correct behaviour.
    """
    st = _state()
    monkeypatch.setattr(cd, "WORKSTREAM_SCAN_BUDGET", 0.0)
    msg1, outcome1, details1 = cd.workstream_page_context(st, "PLAT-3113")
    assert outcome1 == "skipped-partial", "a transient failure is partial, not settled"
    assert details1 == {"reason": "scan_failed"}
    assert "PLAT-3113" not in st["workstream_keys_fired"], \
        "one slow git must not blind this key for the session"

    # RECOVERY: with the budget restored the key is still reachable and still advises.
    monkeypatch.setattr(cd, "WORKSTREAM_SCAN_BUDGET", 2.0)
    msg2, outcome2, _ = cd.workstream_page_context(st, "PLAT-3113")
    assert outcome2 == "unopened" and msg2 is not None

    # CONTROL: the same key, never failing, behaves identically — so the assertion above
    # is about recovery, not about the key being unusual.
    ctl = _state()
    msg3, outcome3, _ = cd.workstream_page_context(ctl, "PLAT-3113")
    assert outcome3 == "unopened" and msg3 is not None


def test_a_permanent_scan_failure_still_settles_the_key(live, monkeypatch, tmp_path):
    """The other arm of the split, and the reason it is a split.

    When the vault is not a usable git repo at all — the packaged default points nowhere
    — nothing will change within the session, so settling is right and the original
    comment's concern is real: without it every prompt naming anything ticket-shaped
    writes a fire-log row forever. Removing the settle wholesale would have traded one
    defect for that one.
    """
    monkeypatch.setattr(cd, "WIKI_DIR", tmp_path / "no-such-vault")
    st = _state()
    msg, outcome, details = cd.workstream_page_context(st, "PLAT-3113")
    assert outcome == "skipped", "an unusable vault is permanent; settle it"
    assert details == {"reason": "no_vault"}
    assert "PLAT-3113" in st["workstream_keys_fired"]


def test_an_evicted_read_record_downgrades_the_claim_it_can_no_longer_prove(live):
    """`wiki_paths_read` is read as proof of a NEGATIVE — "this path is not in the list,
    so the page was never opened". Once the cap has evicted anything, that inference is
    unsound, and the failure is not a mislabelled log row: it is a false advisory shown
    to the user about a page they did open.

    CONTROL: the page is recorded and under the cap -> `opened`, silent.
    TREATMENT: the same page was read, but eviction dropped it -> the code cannot prove
    the negative, so the outcome is qualified and the message says the record is partial.
    """
    real = "brain/proj/PLAT-3113-rtbf-migration.md"

    ctl = _state(wiki_paths_read=[real])
    assert cd.workstream_page_context(ctl, "PLAT-3113")[1] == "opened"

    st = _state(wiki_paths_read=[], wiki_paths_read_evicted=True)
    msg, outcome, _ = cd.workstream_page_context(st, "PLAT-3113")
    assert outcome == "unopened-partial", \
        "with an evicted record the code cannot claim the page was never opened"
    assert "record of what was opened is partial" in msg


def test_the_eviction_flag_tracks_the_event_not_the_cap(live, monkeypatch):
    """The flag must track the EVICTION, not the existence of a cap.

    Setting it on every append past the first would make every long-ish session
    permanently partial — the over-reporting mirror of the bug it fixes, and invisible,
    because `unopened-partial` is a plausible outcome that nobody would question.

    Drives the real `handle_pre_tool`, not a hand-built list: the predecessor of this
    whole family of tests set a field the function under test never read.
    CONTROL is at exactly the cap, TREATMENT is one past it.
    """
    monkeypatch.setattr(cd, "WIKI_READ_PATHS_CAP", 3)

    st = None
    for n in range(3):
        st = _pre_tool("Read", {"file_path": str(live / f"brain/p{n}.md")})
    assert len(st["wiki_paths_read"]) == 3
    assert not st.get("wiki_paths_read_evicted"), \
        "at exactly the cap nothing has been dropped yet"

    st = _pre_tool("Read", {"file_path": str(live / "brain/p3.md")})
    assert st["wiki_paths_read"] == ["brain/p1.md", "brain/p2.md", "brain/p3.md"]
    assert st.get("wiki_paths_read_evicted") is True, \
        "one entry was dropped, so the record is no longer proof of a negative"


def test_the_per_prompt_key_bound_is_reported_on_a_silent_outcome_too(live):
    """`truncated_keys` was computed for every call and reported at exactly one of the
    five return sites — the advisory branch, in prose. A prompt naming five keys whose
    first three all resolve `no-page` logged a clean verdict with no trace that two keys
    were never looked at.

    CONTROL: three keys, nothing truncated -> details empty, as before.
    """
    ctl = _state()
    assert cd.workstream_page_context(ctl, "AAA-11 BBB-22 CCC-33")[2] == {}

    st = _state()
    msg, outcome, details = cd.workstream_page_context(st, "AAA-11 BBB-22 CCC-33 DDD-44 EEE-55")
    assert msg is None and outcome == "no-page"
    assert details == {"truncated_keys": 2}, \
        "a bound that truncated must be visible to a fire-log reader"


# ============ coverage gaps found by a reviewer's mutation run, 2026-08-06 ============
# Seven predicates behaved CORRECTLY on HEAD and were pinned by nothing: 24 mutations
# applied, 17 killed, 7 survived. Measured against the real vault at the same time, the
# exposure of the four index-shape ones is currently ZERO — no `_index.md` in
# claude-core-wiki uses a tilde fence, an HTML-commented row, a second-cell wikilink or a
# code-wrapped wikilink. That is the argument for testing them rather than against it: the
# guards exist for a shape the vault does not have YET, so nothing but a test will notice
# when one of them is deleted.

def test_a_key_followed_by_a_letter_is_not_a_key():
    """The trailing lookahead. `PLAT-3113x` is a different token, not a truncated key."""
    assert cd.workstream_keys("see PLAT-3113x here")[0] == []


def test_a_key_preceded_by_a_letter_is_not_a_key():
    r"""The leading `\b`. `xPLAT-3113` is not a mention of PLAT-3113."""
    assert cd.workstream_keys("see xPLAT-3113 here")[0] == []


def test_a_tilde_fence_hides_index_rows_exactly_as_a_backtick_fence_does(live, vault):
    """`~~~` is a legal markdown fence and the walk handles both. Only the backtick half
    was pinned, so deleting the tilde half left the suite green."""
    (vault / "brain" / "proj" / "tilde-target.md").write_text("# t\n")
    (vault / "brain" / "tilde_index" ).mkdir(exist_ok=True)
    (vault / "brain" / "tilde_index" / "_index.md").write_text(
        "~~~\n| [[brain/proj/tilde-target]] | TILDE-11 |\n~~~\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "tilde fence")
    assert cd.workstream_page_scan(["TILDE-11"], repo=vault)["hits"] == {}


def test_an_html_commented_row_is_not_a_hit(live, vault):
    """The comment strip. A commented-out row is not a page anyone can open."""
    (vault / "brain" / "proj" / "commented-target.md").write_text("# c\n")
    (vault / "brain" / "cmt_index").mkdir(exist_ok=True)
    (vault / "brain" / "cmt_index" / "_index.md").write_text(
        "<!--\n| [[brain/proj/commented-target]] | CMT-11 |\n-->\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "commented row")
    assert cd.workstream_page_scan(["CMT-11"], repo=vault)["hits"] == {}


def test_a_wikilink_outside_the_first_cell_is_not_the_rows_subject(live, vault):
    """The existing first-cell test does not discriminate: its first-cell link is also the
    FIRST link on the line, so reading the whole row would give the same answer. This row
    has NO link in cell one and a link in cell two, which separates the two readings."""
    (vault / "brain" / "proj" / "second-cell-target.md").write_text("# s\n")
    (vault / "brain" / "sc_index").mkdir(exist_ok=True)
    (vault / "brain" / "sc_index" / "_index.md").write_text(
        "| SECOND-11 | see [[brain/proj/second-cell-target]] |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "second cell")
    assert cd.workstream_page_scan(["SECOND-11"], repo=vault)["hits"] == {}


def test_a_wikilink_inside_inline_code_is_not_a_link(live, vault):
    """Inline code is how documentation SHOWS a link without making one."""
    (vault / "brain" / "proj" / "code-target.md").write_text("# c\n")
    (vault / "brain" / "code_index").mkdir(exist_ok=True)
    (vault / "brain" / "code_index" / "_index.md").write_text(
        "| `[[brain/proj/code-target]]` | CODE-11 |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "code-wrapped")
    assert cd.workstream_page_scan(["CODE-11"], repo=vault)["hits"] == {}


def test_the_page_sample_bound_is_reported_when_it_truncates(live, vault):
    """WORKSTREAM_SAMPLE caps how many pages one key names. The count of what was dropped
    is computed and reaches the advisory text — pinned by nothing, so setting it to a
    constant zero left the suite green while the caveat silently disappeared."""
    for n in range(3):
        (vault / "brain" / "proj" / f"SAMP-11-page{n}.md").write_text("# p\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "three pages one key")
    msg, outcome, _ = cd.workstream_page_context(_state(), "SAMP-11")
    assert outcome == "unopened"
    assert "further page(s) not listed" in msg, "a bound that truncated must be reported"


def test_a_vault_with_no_markdown_is_searched_not_unreadable(live, vault, monkeypatch):
    """THREE vaults differing only in contents, because either one alone looks correct.

    A repo that has commits and whose `ls-tree` succeeds HAS been searched; that it holds
    no markdown is a result, not a failure to look. Returning the could-not-look sentinel
    merged the two states this function exists to separate — harmless while `scan is None`
    settled unconditionally, and costly once the transient/permanent split stopped it
    settling: the key then rescanned on every ticket-shaped prompt, 24 `git ls-tree` calls
    a session where there had been one.
    """
    import subprocess as _sp
    import tempfile as _tf
    def _vault(files):
        d = Path(_tf.mkdtemp()) / "wiki"
        d.mkdir(parents=True)
        _sp.run(["git", "init", "-q", str(d)], check=True, capture_output=True)
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        for name, body in files.items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "x")
        return d

    empty = _vault({"README.txt": "not markdown"})
    unrelated = _vault({"notes.md": "nothing relevant"})

    # SEARCHED, holds no markdown at all -> a result, and the key settles.
    monkeypatch.setattr(cd, "WIKI_DIR", empty)
    assert cd.workstream_page_scan(["PLAT-3113"], repo=empty)["hits"] == {}
    st_empty = _state()
    assert cd.workstream_page_context(st_empty, "PLAT-3113")[1] == "no-page"
    assert "PLAT-3113" in st_empty["workstream_keys_fired"], \
        "a searched vault settles the key; only an unreadable one may leave it open"

    # SEARCHED, holds markdown but no page for this key -> must agree with the above.
    monkeypatch.setattr(cd, "WIKI_DIR", unrelated)
    st_unrelated = _state()
    assert cd.workstream_page_context(st_unrelated, "PLAT-3113")[1] == "no-page"

    # COULD NOT LOOK — not a git repo at all. Still distinct from both.
    not_a_repo = Path(_tf.mkdtemp()) / "plain"
    not_a_repo.mkdir(parents=True)
    monkeypatch.setattr(cd, "WIKI_DIR", not_a_repo)
    assert cd.workstream_page_scan(["PLAT-3113"], repo=not_a_repo) is None
    assert cd.workstream_page_context(_state(), "PLAT-3113")[1] == "skipped"
