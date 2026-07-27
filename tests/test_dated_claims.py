"""Dated-claim re-validation: statements that are true until a date.

The failure this guards is silent by construction — a dated claim decays without
raising, no test goes red, the prose simply stops being true. So the tests here
care less about the happy path than about the two ways the trigger could quietly
stop protecting anything: a malformed date being skipped, and the claim table
itself drifting out of shape.

`today` is injected rather than mocked at the clock, which is what makes the
boundary testable at all. A dated check verifiable only on the day it fires is a
check nobody ever verifies.
"""
import importlib.util
import json
from datetime import date
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_dated", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

FIRST_PROMPT = {"prompts_seen": 1}


def _claim(expires):
    return {"expires": expires, "what": "a test claim", "recheck": "do the thing"}


# ---------------- status classification ----------------

def test_far_future_claim_is_current(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2099-01-01")])
    assert cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 7, 27)) == (None, "current")


def test_claim_inside_the_lead_window_is_due(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2026-08-31")])
    msg, outcome = cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 8, 20))
    assert outcome == "due"
    assert "expires in 11d" in msg


def test_last_valid_day_is_due_not_expired(monkeypatch):
    """`expires` means valid THROUGH that date, so the boundary day is not a miss."""
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2026-08-31")])
    _, outcome = cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 8, 31))
    assert outcome == "due"


def test_day_after_expiry_is_expired(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2026-08-31")])
    msg, outcome = cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 9, 1))
    assert outcome == "expired"
    assert "EXPIRED 1d ago" in msg
    assert "do the thing" in msg, "the advisory must carry the re-check instruction"


def test_outside_the_lead_window_stays_silent(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2026-08-31")])
    assert cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 8, 1)) == (None, "current")


# ---------------- the unknown case: a broken row must not read as coverage ----------------

def test_malformed_expiry_is_surfaced_not_skipped(monkeypatch):
    """Skipping an unparseable date is unknown -> permissive: a typo silently
    retires the trigger while the row still LOOKS like protection."""
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("31/08/2026")])
    msg, outcome = cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 7, 27))
    assert outcome == "malformed"
    assert "unparseable expiry" in msg


def test_missing_expiry_key_is_malformed(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [{"what": "no date at all"}])
    _, outcome = cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 7, 27))
    assert outcome == "malformed"


def test_expired_outranks_due_in_the_outcome(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2026-09-30"), _claim("2026-08-31")])
    msg, outcome = cd.dated_claims_context(FIRST_PROMPT, today=date(2026, 9, 20))
    assert outcome == "expired", "the worst status present must be the reported one"
    assert "EXPIRED" in msg and "expires in 10d" in msg


# ---------------- the shipped table itself ----------------

def test_every_shipped_claim_is_well_formed():
    """The mechanism is only as good as its rows. A row with a bad date or no
    re-check instruction is dead weight that reads as coverage."""
    assert cd.DATED_CLAIMS, "the table must not be empty — it is the whole feature"
    for claim in cd.DATED_CLAIMS:
        status, _ = cd._claim_status(claim, date(2026, 7, 27))
        assert status != "malformed", f"unparseable expiry in shipped row: {claim!r}"
        assert claim.get("what"), f"shipped row has no description: {claim!r}"
        assert claim.get("recheck"), f"shipped row has no re-check instruction: {claim!r}"


def test_sonnet_intro_rate_claim_is_present():
    """Pins the specific claim this feature was built for, so a future edit that
    drops the row fails loudly instead of silently removing the only trigger."""
    assert any("2026-08-31" == c.get("expires") and "Sonnet 5" in c.get("what", "")
               for c in cd.DATED_CLAIMS)


# ---------------- throttle + handler wiring ----------------

def test_throttled_off_interval(monkeypatch):
    monkeypatch.setattr(cd, "DATED_CLAIMS", [_claim("2020-01-01")])
    assert cd.dated_claims_context({"prompts_seen": 5}) == (None, None)


def test_handler_emits_the_advisory_and_logs_the_outcome(monkeypatch, capsys):
    fired = []
    monkeypatch.setattr(cd, "load_state", lambda sid: {"session_id": sid, "prompts_seen": 0})
    monkeypatch.setattr(cd, "save_state", lambda state: None)
    monkeypatch.setattr(cd, "log_fire", lambda rule, *a, **k: fired.append((rule, k.get("outcome"))))
    monkeypatch.setattr(cd, "hygiene_context", lambda state: None)
    monkeypatch.setattr(cd, "wiki_index_context", lambda state: (None, None))
    monkeypatch.setattr(cd, "rlm_fanout_context", lambda prompt: None)
    monkeypatch.setattr(cd, "dated_claims_context", lambda state: ("DATED ADVISORY", "expired"))

    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "hi"})
    payload = json.loads(capsys.readouterr().out.strip())
    assert "DATED ADVISORY" in payload["hookSpecificOutput"]["additionalContext"]
    assert ("dated_claim", "expired") in fired


def test_a_sibling_advisory_failure_does_not_disarm_this_one(monkeypatch, capsys):
    """Third instance of the shared-`except` shape. Each advisory owns its own
    handler precisely so a fault in one cannot silently retire another."""
    def boom(state):
        raise RuntimeError("wiki scan blew up")

    monkeypatch.setattr(cd, "load_state", lambda sid: {"session_id": sid, "prompts_seen": 0})
    monkeypatch.setattr(cd, "save_state", lambda state: None)
    monkeypatch.setattr(cd, "log_fire", lambda *a, **k: None)
    monkeypatch.setattr(cd, "hygiene_context", lambda state: None)
    monkeypatch.setattr(cd, "wiki_index_context", boom)
    monkeypatch.setattr(cd, "rlm_fanout_context", lambda prompt: None)
    monkeypatch.setattr(cd, "dated_claims_context", lambda state: ("DATED SURVIVED", "expired"))

    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "hi"})
    assert "DATED SURVIVED" in capsys.readouterr().out
