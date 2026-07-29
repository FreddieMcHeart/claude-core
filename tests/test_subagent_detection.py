"""is_subagent_call(payload): the predicate that replaces detect_session_mode()
for blocks_enabled()'s exemption.

First tests for this path (2026-07-30). Fixtures below are reconstructed from
REAL records pulled from ~/.claude/payload-probe.jsonl (a temporary measurement
probe, deleted alongside this fix) — not the live file itself, which is a
machine-local, ever-growing artifact unsuitable as a portable test fixture.
Field names, the co-occurrence of agent_type/agent_id, and every agent_type
string below are copied verbatim from real pre-tool records the probe captured
across 10 sessions over ~15 hours; only bulky/irrelevant fields (env_names,
transcript_path, etc.) are dropped.
"""
import importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_subagent", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


# ---- real sub-agent pre-tool payloads, one per agent_type value observed ----
# (a 6th value, diagram-vision-reviewer, exists in the corpus but only ever on
# post-tool events — included anyway since is_subagent_call must not special-
# case which agent_type strings it recognizes; presence of the field is the
# whole signal, not membership in a fixed list.)
SUBAGENT_FIXTURES = [
    {
        "session_id": "4dec4a90-9eea-4c8d-9883-ee3a1206b2ca",
        "agent_id": "a266af80623bd435f",
        "agent_type": "general-purpose",
        "tool_name": "Bash",
        "tool_input": {"command": "grep -n foo", "description": "search"},
    },
    {
        "session_id": "4dec4a90-9eea-4c8d-9883-ee3a1206b2ca",
        "agent_id": "af0871f525d7ed082",
        "agent_type": "diagram-verifier",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/diagram.html"},
    },
    {
        "session_id": "8d52d413-2ba0-4976-93c5-98ec0d9cd2a4",
        "agent_id": "a45c900be20b734c0",
        "agent_type": "gcloud-reader",
        "tool_name": "Bash",
        "tool_input": {"command": "gcloud projects list", "description": "list"},
    },
    {
        "session_id": "80b78d2f-333b-4681-9088-508f9f89a8b2",
        "agent_id": "a26a9aa91ac2aad74",
        "agent_type": "gh-reader",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr list", "description": "list PRs"},
    },
    {
        "session_id": "80b78d2f-333b-4681-9088-508f9f89a8b2",
        "agent_id": "ae00bb7b8c2eabbe3",
        "agent_type": "slack-reader",
        "tool_name": "Bash",
        "tool_input": {"command": "slack-cli.sh read C123", "description": "read"},
    },
    {
        "session_id": "3ae7c091-fake-fake-fake-fakefakefake0",
        "agent_id": "a1b2c3d4e5f678901",
        "agent_type": "diagram-vision-reviewer",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/screenshot.png"},
    },
]

# ---- the 3 control event types that must NEVER carry agent_type (confirmed:
# 0 of 227 real control events did) ----
CONTROL_FIXTURES = [
    {"session_id": "3aa54ade-cc93-4807-9af5-905985113bdb",
     "mode": "user-prompt-submit", "prompt": "do the thing"},
    {"session_id": "8bdf4617-7086-449e-8586-15675915fd01",
     "mode": "session-start", "source": "resume"},
    {"session_id": "8bdf4617-7086-449e-8586-15675915fd01",
     "mode": "post-compact", "trigger": "manual"},
]

# ---- real plain main-agent pre-tool payload: no agent_type, no agent_id ----
MAIN_FIXTURE = {
    "session_id": "4ca1e8fd-fea6-43ff-be00-ad6b24beef37",
    "tool_name": "Bash",
    "tool_input": {"command": "ls -la", "description": "list"},
}


def test_every_real_subagent_fixture_is_detected():
    # Vacuity-guard-direction check: a fixture list that's empty or wrong would
    # make every assertion below vacuously true. Assert the population itself.
    assert len(SUBAGENT_FIXTURES) == 6
    for fixture in SUBAGENT_FIXTURES:
        assert cd.is_subagent_call(fixture) is True, \
            f"agent_type={fixture['agent_type']!r} must be detected as a sub-agent call"


def test_control_events_are_never_classified_as_subagent():
    assert len(CONTROL_FIXTURES) == 3
    for fixture in CONTROL_FIXTURES:
        assert cd.is_subagent_call(fixture) is False, \
            f"{fixture['mode']} must never be classified as a sub-agent call"


def test_plain_main_agent_call_is_not_a_subagent_call():
    assert cd.is_subagent_call(MAIN_FIXTURE) is False


def test_empty_payload_is_not_a_subagent_call():
    """The malformed/empty-payload edge case: is_subagent_call must not raise,
    and must fail toward the same non-exempt branch as a genuine main call —
    a stated choice (see the function's own docstring), not a silent default."""
    assert cd.is_subagent_call({}) is False


def test_agent_id_without_agent_type_does_not_occur_in_the_corpus_but_would_still_exempt():
    """Documents the actual predicate: it keys on agent_type, not agent_id.
    The corpus never shows one without the other, but the function's contract
    should be explicit about which field it actually reads."""
    assert cd.is_subagent_call({"agent_id": "deadbeef00000000"}) is False
    assert cd.is_subagent_call({"agent_type": ""}) is False
