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
    assert cd.reader_roster() == ([], False)


def test_an_empty_agents_dir_is_enumerated_and_empty(monkeypatch, tmp_path):
    """The other state: the directory WAS read and holds no readers. Same empty list, and the
    second element is what separates them — which is why the function returns two values."""
    (tmp_path / "agents").mkdir()
    monkeypatch.setattr(cd, "HARNESS_DIR", tmp_path)
    assert cd.reader_roster() == ([], True)


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
    assert cd.reader_roster() == (["gh-reader", "slack-reader"], True)


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
    assert cd.reader_roster() == ([], False)
