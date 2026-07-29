"""The edit-loop detector's Read-after-Edit counter: what resets on a fresh Edit.

First tests for this path (2026-07-29). The defect: `files_warned_for_reread` and
`files_escalated_for_reread` get the file removed on a fresh Edit so a resumed
cycle "re-fires" (the L1741 comment's own words), but `read_after_edit_counts`
itself was never reset alongside them. Effect: a resumed cycle's first Read
reported the OLD cycle's lifetime count, which was already >= EDIT_LOOP_ESCALATION
after a first cycle reached escalation — so the warn tier could never fire again
and every resumed cycle jumped straight to escalation instead of re-firing
gradually the way the comment describes.
"""
import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_editloop", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _chained_state(monkeypatch, **fields):
    """Drive handle_pre_tool across MULTIPLE calls with state that actually
    persists between them, the way separate hook invocations really do via the
    on-disk state file. The read-block-tier fixture this is adapted from reloads
    the same fixed `base` on every call, which is fine for single-call tests but
    would hide this exact defect: it needs the count to carry across an Edit and
    several Reads, then across another Edit."""
    base = cd.new_state("s1")
    base.update(fields)
    saved = {}

    def _load(sid):
        return dict(base)

    def _save(st):
        saved.clear()
        saved.update(st)
        base.clear()
        base.update(st)

    monkeypatch.setattr(cd, "load_state", _load)
    monkeypatch.setattr(cd, "save_state", _save)
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    return saved


def _fire(capsys, tool, fp):
    cd.handle_pre_tool({"session_id": "s1", "tool_name": tool,
                        "tool_input": {"file_path": fp}})
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_fresh_edit_resets_the_count_not_just_the_warn_escalate_flags(monkeypatch, capsys):
    fp = "/tmp/x"
    saved = _chained_state(monkeypatch)

    _fire(capsys, "Edit", fp)
    for _ in range(cd.EDIT_LOOP_ESCALATION):
        _fire(capsys, "Read", fp)
    assert saved["read_after_edit_counts"][fp] == cd.EDIT_LOOP_ESCALATION
    assert fp in saved["files_escalated_for_reread"]

    _fire(capsys, "Edit", fp)
    assert fp not in saved.get("read_after_edit_counts", {}), (
        "a fresh Edit must clear the count itself, not just the warn/escalate "
        "membership lists — otherwise the next Read starts from the old cycle's "
        "lifetime count instead of zero"
    )
    assert fp not in saved["files_warned_for_reread"]
    assert fp not in saved["files_escalated_for_reread"]


def test_resumed_cycle_refires_gradually_instead_of_jumping_to_escalation(monkeypatch, capsys):
    """Pins the actual user-visible symptom: with the counter left unreset, the
    FIRST Read of a resumed cycle already sits at/above EDIT_LOOP_ESCALATION, so
    both tiers fire at once on that single Read. The fix must make a resumed
    cycle's first Read behave like a brand-new cycle's first Read: no tier fires
    yet, because the count is 1, not 6."""
    fp = "/tmp/x"
    saved = _chained_state(monkeypatch)

    _fire(capsys, "Edit", fp)
    for _ in range(cd.EDIT_LOOP_ESCALATION):
        _fire(capsys, "Read", fp)
    assert fp in saved["files_escalated_for_reread"]

    _fire(capsys, "Edit", fp)  # resumes the cycle

    payload = _fire(capsys, "Read", fp)  # first Read of the resumed cycle
    assert saved["read_after_edit_counts"][fp] == 1, (
        "a resumed cycle's first Read must count as 1, not continue from the "
        "prior cycle's lifetime total"
    )
    assert payload is None, (
        "neither tier should fire on a resumed cycle's first Read — the old "
        "behaviour fired warn AND escalation simultaneously here because the "
        "stale count (already >= EDIT_LOOP_ESCALATION) satisfied both conditions "
        "at once"
    )
    assert fp not in saved["files_warned_for_reread"]
    assert fp not in saved["files_escalated_for_reread"]


def test_resumed_cycle_still_reaches_warn_and_escalation_tiers_on_its_own_schedule(monkeypatch, capsys):
    """The reset must not disable the detector for a resumed cycle — it should
    just make it earn each tier again on the same schedule as any fresh cycle."""
    fp = "/tmp/x"
    saved = _chained_state(monkeypatch)

    _fire(capsys, "Edit", fp)
    for _ in range(cd.EDIT_LOOP_ESCALATION):
        _fire(capsys, "Read", fp)
    _fire(capsys, "Edit", fp)  # resumes the cycle

    warned_at = None
    escalated_at = None
    for i in range(1, cd.EDIT_LOOP_ESCALATION + 1):
        payload = _fire(capsys, "Read", fp)
        if payload and warned_at is None and fp in saved.get("files_warned_for_reread", []):
            warned_at = i
        if payload and escalated_at is None and fp in saved.get("files_escalated_for_reread", []):
            escalated_at = i

    assert warned_at == cd.EDIT_LOOP_THRESHOLD
    assert escalated_at == cd.EDIT_LOOP_ESCALATION
