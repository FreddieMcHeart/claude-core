"""The rlm-fanout advisory: when it fires, and how it speaks.

Neither had a test. `match_repos`, `has_cross_repo_phrase` and
`rlm_fanout_context` are UserPromptSubmit hot path — they run on every prompt in
every session on this machine — and the suite was green while the detector fired
on 26.12% of the 8,947 real prompts in this machine's transcripts.

Two separate defects are pinned here, because they failed independently:

  TRIGGER   a repo name matched anywhere in prose. Four of the 46 names under
            ~/mama are `agent`, `skills`, `hooks`, `commands` — the most common
            nouns in this harness's conversation about itself.

  PHRASING  the advisory instructed the agent to open its reply with a fixed
            sentence and "proceed unless the user objects", i.e. to read silence
            from someone never shown the question as assent.
"""
import importlib.util
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_rlm", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

# The real collision set, as measured on this machine, plus two names that are
# not English words so the tests can tell "rejected as prose" from "rejected".
REPOS = {"agent", "skills", "hooks", "commands", "mcp", "profile",
         "cryptobot", "helm-charts", "env-conf", "smart-payments"}


@pytest.fixture
def pinned_repos(monkeypatch):
    """Pin enumerate_repos() for tests that go through rlm_fanout_context().

    match_repos takes its repo set as an argument, so those tests are already
    hermetic. rlm_fanout_context does not — it calls enumerate_repos() itself,
    which walks the real ~/mama. Four tests therefore verified this machine's
    filesystem rather than the code, and one of them passed VACUOUSLY anywhere
    ~/mama is absent: with no repos there is nothing to match, so "does not
    fire" is satisfied by having nothing to fire on.
    """
    monkeypatch.setattr(cd, "enumerate_repos", lambda: set(REPOS))
    return REPOS


# --------------------------------------------------------------------------
# TRIGGER — the incident, reproduced
# --------------------------------------------------------------------------

def test_bare_english_words_are_not_repo_mentions():
    """THE INCIDENT. This exact shape fired the advisory in three sessions in one
    day, twice in a single payload. Two ordinary nouns and it claimed a
    cross-repo investigation."""
    prompt = "the agent and the hooks and the skills and the commands"
    assert cd.match_repos(prompt, REPOS) == set()


def test_relay_prose_naming_two_collision_words_does_not_fire(pinned_repos):
    """PINNED. Calling rlm_fanout_context reaches the real enumerate_repos(),
    which scans this machine's ~/mama. On a machine without one it returns the
    empty set and this test passes because there was nothing to match — a
    vacuous pass, in a suite whose subject is checks that cannot fail."""
    prompt = ("Reviewed your agent dispatch and the hooks it registers; "
              "the skills look right to me.")
    assert cd.rlm_fanout_context(prompt) is None


def test_harness_own_paths_are_not_repo_mentions():
    """~/.claude/skills/ is a path and is not a repo. Excluded before the path
    test, or every self-reference in the harness counts as a repository."""
    for prompt in (
        "Base directory for this skill: /Users/me/.claude/skills/models-router",
        "see ~/.claude/hooks/cost-discipline.py and ~/.claude/commands/x.md",
        "node_modules/agent/index.js and dist/skills/build.js",
    ):
        assert cd.match_repos(prompt, REPOS) == set(), prompt


# --------------------------------------------------------------------------
# TRIGGER — what must still be caught. A rule that fires on nothing scores
# perfectly on every test above.
# --------------------------------------------------------------------------

def test_mama_paths_still_match():
    prompt = ("how does auth flow across ~/mama/x/agent and "
              "~/mama/x/skills?")
    assert cd.match_repos(prompt, REPOS) == {"agent", "skills"}


def test_trailing_punctuation_does_not_defeat_a_path_match():
    """A hand-written trailing character class lost `app-conf?` to the question
    mark in an earlier draft. The name is classified by the token it sits in,
    not by what follows the token."""
    assert cd.match_repos("compare ~/mama/x/agent and ~/mama/x/skills?", REPOS) \
        == {"agent", "skills"}


def test_bare_directory_reference_still_matches():
    prompt = "compare how helm-charts/ and env-conf/ handle secrets"
    assert cd.match_repos(prompt, REPOS) == {"helm-charts", "env-conf"}


def test_repo_word_adjacency_promotes_a_bare_name():
    assert cd.match_repos("trace the call chain from the cryptobot repo "
                          "into the smart-payments repo", REPOS) \
        == {"cryptobot", "smart-payments"}
    assert cd.match_repos("the repo agent and the repo skills", REPOS) \
        == {"agent", "skills"}


def test_hyphen_awareness_is_preserved():
    """The original NEW-1 property: `agent` must not match inside `agent-ui`,
    while `helm-charts` matches as itself."""
    assert cd.match_repos("~/mama/x/agent-ui is the frontend", REPOS) == set()
    assert "helm-charts" in cd.match_repos("~/mama/x/helm-charts/values.yaml", REPOS)


# --------------------------------------------------------------------------
# TRIGGER — the phrase path, which was a second, independent leak
# --------------------------------------------------------------------------

def test_end_to_end_is_not_a_cross_repo_phrase():
    """175 fires on the real corpus. It is a routine engineering adjective."""
    assert not cd.has_cross_repo_phrase("I verified it end-to-end")
    assert not cd.has_cross_repo_phrase("an end to end test of the flow")


def test_cross_repo_is_not_a_trigger_word():
    """370 fires — and it is this feature's OWN vocabulary. models-router and
    delegation-discipline both use it, so the detector fired on documentation
    describing the detector."""
    assert not cd.has_cross_repo_phrase(
        "the cross-repo investigation pattern is in the skill")


def test_genuine_cross_repo_phrases_still_fire():
    assert cd.has_cross_repo_phrase("how does auth work across services")
    assert cd.has_cross_repo_phrase("trace this across repos")


# --------------------------------------------------------------------------
# PHRASING — a hook may inform, recommend, or block. It may not manufacture
# consent on the user's behalf.
# --------------------------------------------------------------------------

def _advisory_texts():
    """Callers must take the `pinned_repos` fixture — see its docstring."""
    out = []
    for prompt in ("what talks to what across services",
                   "how does data move between ~/mama/x/agent and ~/mama/x/skills"):
        r = cd.rlm_fanout_context(prompt)
        assert r is not None, f"fixture stopped firing: {prompt!r}"
        out.append(r[0])
    return out


def test_advisory_does_not_manufacture_consent(pinned_repos):
    """The banned construction, verbatim from the version this replaces:
    "Open your response with 'Running rlm-fanout — Esc to stop' and proceed
    unless the user objects." """
    for text in _advisory_texts():
        low = text.lower()
        assert "unless the user objects" not in low, text
        assert "open your response with" not in low, text
        assert "esc to stop" not in low, text


def test_advisory_says_a_workflow_needs_the_users_request(pinned_repos):
    """The harness forbids Workflow without an explicit user request. The old
    text told sessions to start one anyway, so the advisory and the harness
    disagreed on every fire."""
    for text in _advisory_texts():
        assert "explicit request" in text.lower(), text


def test_advisory_still_names_the_workflow_and_the_repos(pinned_repos):
    """Softening the instruction must not cost the information. If the advisory
    stops naming rlm-fanout or the repos it saw, it has become decoration."""
    r = cd.rlm_fanout_context(
        "how does data move between ~/mama/x/agent and ~/mama/x/skills")
    assert r is not None
    text, repos = r
    assert "rlm-fanout" in text
    assert repos == ["agent", "skills"]
    assert "agent" in text and "skills" in text
