"""The reader-agent roster is DERIVED from disk, not recited in the message.

The failure this guards already happened and cost nine days. The post-compact reminder
carried a hand-written list of five readers. `gcloud-reader` was added to the fleet on
2026-07-28 — as the fix for an incident whose established root cause was that gcloud was the
one tool with no reader — and the list was never told, so the remedy existed with nothing
pointing at it. `notion-reader` would have repeated it on 2026-08-06.

Nothing could have caught that. The roster was a string literal in one repository and the
agent definitions are files in another, with no shared source of truth, so a test could only
have asserted the string parsed. Deriving the roster is what makes it testable at all — that
is the point of this file, more than any single assertion in it.

Test order is deliberate: the two "could not look" / "looked and found nothing" cases come
FIRST, before any happy path exists to make them look redundant. They are the cases where a
roster claim would be a claim about a population nobody bounded.
"""
import importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
_spec = importlib.util.spec_from_file_location("cost_discipline_roster", MOD)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


def _reminder(monkeypatch, tmp_path, capsys, agents=None, make_dir=True):
    """Drive the real handle_post_compact and return its emitted text."""
    harness = tmp_path / "harness"
    if make_dir:
        d = harness / "agents"
        d.mkdir(parents=True)
        for name in (agents or []):
            (d / f"{name}.md").write_text("---\nname: x\n---\n")
    else:
        harness.mkdir(parents=True)
    monkeypatch.setattr(cd, "HARNESS_DIR", harness)
    monkeypatch.setattr(cd, "state_path", lambda sid: tmp_path / f"{sid}.json")
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    monkeypatch.setattr(cd, "COST_LEDGER_DIR", tmp_path / "ledger")
    cd.handle_post_compact({"session_id": "s1"})
    return capsys.readouterr().out


# ---- could not look, and looked-and-found-nothing: distinct, and neither is a roster ----

def test_an_unreadable_agents_dir_is_not_a_claim_that_no_readers_exist(monkeypatch, tmp_path):
    """A missing directory bounds no population. Reporting it as an empty roster would be a
    coverage claim over a set that was never enumerated."""
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path / "does-not-exist")
    assert cd.reader_roster() == ([], False, 0)


def test_an_empty_agents_dir_is_enumerated_and_empty(monkeypatch, tmp_path):
    """The other state: the directory WAS read and holds no readers. Same empty list, and the
    second element is what separates them — which is why the function returns two values."""
    (tmp_path / "agents").mkdir()
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    assert cd.reader_roster() == ([], True, 0)


def test_the_three_states_produce_three_different_messages(monkeypatch, tmp_path, capsys):
    """Distinguishable in the OUTPUT, not only in the return value. A caller that collapsed
    the first two would print 'no readers are installed' about a directory it could not open."""
    unreadable = _reminder(monkeypatch, tmp_path / "a", capsys, make_dir=False)
    empty = _reminder(monkeypatch, tmp_path / "b", capsys, agents=[])
    populated = _reminder(monkeypatch, tmp_path / "c", capsys, agents=["gh-reader"])

    assert "could NOT be enumerated" in unreadable
    assert "no reader agents are installed" not in unreadable, \
        "an unreadable directory must not be reported as an empty one"
    assert "no reader agents are installed" in empty
    assert "could NOT be enumerated" not in empty
    assert "`gh-reader`" in populated
    assert "could NOT be enumerated" not in populated
    assert "no reader agents are installed" not in populated


# ---- the derivation itself ----

def test_only_reader_shaped_agents_are_listed(monkeypatch, tmp_path):
    """`<tool>-reader.md` is the contract, observed from the six that already existed rather
    than imposed. A reviewer agent or a scout is not a reader and must not be offered as one."""
    d = tmp_path / "agents"
    d.mkdir()
    for name in ("gh-reader", "slack-reader", "security-reviewer", "file-finder", "notreader"):
        (d / f"{name}.md").write_text("x")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    # `notreader` is counted as a near-miss and that is deliberate: the pattern requires the
    # hyphen, and a stem containing "reader" that fails it is likelier a misnamed agent than a
    # coincidence. `security-reviewer` and `file-finder` contain no "reader" and are ignored
    # entirely, which is what keeps the heuristic from warning on the whole non-reader fleet.
    assert cd.reader_roster() == (["gh-reader", "slack-reader"], True, 1)


def test_a_reader_added_to_the_fleet_appears_with_no_code_change(monkeypatch, tmp_path, capsys):
    """THE REGRESSION TEST FOR THE ACTUAL INCIDENT, driven end to end.

    Two arms differing only by one file on disk. `gcloud-reader` was invisible to the
    hand-written list for nine days; here it appears because the message reads the fleet
    instead of restating it. If this test ever needs a code change to accommodate a new
    reader, the derivation has been reverted.
    """
    before = _reminder(monkeypatch, tmp_path / "before", capsys,
                       agents=["gh-reader", "kubectl-reader"])
    after = _reminder(monkeypatch, tmp_path / "after", capsys,
                      agents=["gh-reader", "kubectl-reader", "gcloud-reader"])

    assert "`gcloud-reader`" not in before
    assert "`gcloud-reader`" in after, \
        "a reader present on disk must reach the reminder without editing this repo"


def test_the_roster_is_ordered_so_the_message_is_stable(monkeypatch, tmp_path):
    """Filesystem order is not defined. An unordered roster would make the reminder differ
    between runs on identical input, which turns any future diff of it into noise."""
    d = tmp_path / "agents"
    d.mkdir()
    for name in ("slack-reader", "aa-reader", "gh-reader"):
        (d / f"{name}.md").write_text("x")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    assert cd.reader_roster()[0] == ["aa-reader", "gh-reader", "slack-reader"]


def test_the_roster_never_raises_out_of_the_reminder(monkeypatch, tmp_path, capsys):
    """Post-compact runs on every compaction. A roster that raises would take the whole
    checkpoint with it, and the checkpoint is the reason the hook exists at this event."""
    class Exploding:
        def __truediv__(self, other):
            raise OSError("permission denied")
    monkeypatch.setattr(cd, "HARNESS_DIR", Exploding())
    assert cd.reader_roster() == ([], False, 0)


# ============ review findings, 2026-08-06: the derivation trusted the filesystem ============
# A glob matches by NAME. The first version checked nothing else, so three things reached a
# `systemMessage` the model reads as trusted harness text. Each is verified here against the
# thing itself rather than against a stand-in.

def test_a_directory_named_like_a_reader_is_not_an_installed_reader(monkeypatch, tmp_path):
    """`glob` matches names, not file types. A directory called `x-reader.md` was reported as
    installed WITH enumerated=True — stated with confidence — so the model would be told a
    remedy exists and find out at dispatch time that it does not. That is the exact failure
    this function was written to remove, one layer down."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "gh-reader.md").write_text("x")
    (d / "dir-reader.md").mkdir()
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    names, ok, rejected = cd.reader_roster()
    assert names == ["gh-reader"], "a directory is not an agent definition"
    assert rejected == 1, "and its absence must be reported, not silent"


def test_a_broken_symlink_is_not_an_installed_reader(monkeypatch, tmp_path):
    """`is_file()` resolves symlinks, which is why one check covers this and the directory
    case. A dangling link is the likelier of the two in a directory install scripts write."""
    import os
    d = tmp_path / "agents"
    d.mkdir()
    (d / "gh-reader.md").write_text("x")
    os.symlink(tmp_path / "no-such-target", d / "broken-reader.md")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    names, ok, rejected = cd.reader_roster()
    assert names == ["gh-reader"]
    assert rejected == 1


def test_a_hostile_filename_never_reaches_the_message(monkeypatch, tmp_path, capsys):
    """The stem is interpolated into a systemMessage the model reads as trusted. A backtick
    escapes the inline-code span; a newline — legal in a POSIX filename — injects a line break
    into one item of a numbered list. Asserted on the EMITTED TEXT, because the return value
    is not where the damage would land."""
    out = _reminder(monkeypatch, tmp_path, capsys,
                    agents=["gh-reader", "tick`quote-reader", "line\nbreak-reader"])
    assert "`gh-reader`" in out
    # Assert on the exact stems. An earlier version of this test used the substring "tick",
    # which collides with the word "ticket" elsewhere in the same reminder — a fixture that
    # fails for a reason unrelated to its name is the mirror of one that passes for one.
    assert "tick`quote-reader" not in out, "a backtick-bearing name must not be passed through"
    assert "break-reader" not in out, "a newline-bearing name must not be passed through"
    assert "were NOT listed" in out, "and the rejection has to be visible"


def test_a_misnamed_reader_is_counted_rather_than_silently_absent(monkeypatch, tmp_path):
    """The convention is now load-bearing, so a reader named `notion_reader.md` is invisible.
    Invisible-and-silent is the failure this whole function exists to end: it reads as 'you
    have no such reader' when the truth is 'yours is misnamed'."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "gh-reader.md").write_text("x")
    (d / "notion_reader.md").write_text("x")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    names, ok, rejected = cd.reader_roster()
    assert names == ["gh-reader"]
    assert rejected == 1, "a near-miss must be reported, or the convention hides its own victims"


def test_an_agent_that_is_not_a_reader_is_neither_listed_nor_counted(monkeypatch, tmp_path):
    """The near-miss heuristic must not fire on the twenty-odd non-reader agents. Counting
    `security-reviewer` as a rejection would put a permanent false warning in every
    post-compact message, which is how a report becomes noise and then gets ignored."""
    d = tmp_path / "agents"
    d.mkdir()
    for name in ("gh-reader", "security-reviewer", "file-finder", "code-explorer"):
        (d / f"{name}.md").write_text("x")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    assert cd.reader_roster() == (["gh-reader"], True, 0)


def test_the_rejection_count_is_exact_and_not_double_counted(monkeypatch, tmp_path):
    """REGRESSION. The first version of this fix walked `*-reader.md` and then `*.md` in two
    passes, so any entry both globs matched was counted twice: five planted bad entries
    reported as seven rejections. Caught by reading the number rather than trusting it — an
    inflated count errs in the direction that looks like thoroughness."""
    import os
    d = tmp_path / "agents"
    d.mkdir()
    (d / "gh-reader.md").write_text("x")
    (d / "dir-reader.md").mkdir()
    os.symlink(tmp_path / "no-such-target", d / "broken-reader.md")
    (d / "tick`quote-reader.md").write_text("x")
    (d / "notion_reader.md").write_text("x")
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    names, ok, rejected = cd.reader_roster()
    assert names == ["gh-reader"]
    assert rejected == 4, f"four bad entries planted, {rejected} reported"


# ---- two gaps a second reviewer found in the mutation coverage above ----

def test_a_directory_that_exists_but_cannot_be_ENUMERATED(monkeypatch, tmp_path):
    """The production message says "`~/.claude/agents` unreadable", and until now nothing
    exercised that case.

    Every could-not-look test above reaches its branch through `is_dir()` returning False —
    a directory that does not EXIST — or by making the `/` operator raise. Neither is the
    condition the message names: a directory that exists, passes `is_dir()`, and then fails
    when its contents are listed. A permission-denied `.glob()` is the realistic form.

    So a mutation that narrowed the guard to cover only the path construction, leaving the
    enumeration unprotected, would have survived every test in this file. Verified by running
    it. This test forces the failure at the enumeration itself.
    """
    class UnenumerableDir:
        def is_dir(self):
            return True

        def glob(self, pattern):
            raise PermissionError(13, "Permission denied")

    class Harness:
        def __truediv__(self, other):
            return UnenumerableDir()

    monkeypatch.setattr(cd, "HARNESS_DIR", Harness())
    assert cd.reader_roster() == ([], False, 0), \
        "a directory that cannot be listed is could-not-look, not an empty fleet"


def test_the_roster_reaches_the_message_as_a_readable_list(monkeypatch, tmp_path, capsys):
    """The separator was unpinned, and a second reviewer caught it by asking what the six
    settled mutations did NOT cover.

    Every prior multi-reader assertion checked only that one name was PRESENT or ABSENT, and
    the three-state test's populated arm has exactly one reader — so it is insensitive to the
    separator by construction. Changing `", ".join(...)` to `"".join(...)` was run and
    SURVIVED all thirteen tests, producing "Installed: `a``b``c`" in a message the model reads.

    Asserted on the joined text through the real handler, with three readers so the separator
    appears twice.
    """
    out = _reminder(monkeypatch, tmp_path, capsys,
                    agents=["aa-reader", "bb-reader", "cc-reader"])
    assert "`aa-reader`, `bb-reader`, `cc-reader`" in out, \
        "the roster must render as a comma-separated list, in order"
