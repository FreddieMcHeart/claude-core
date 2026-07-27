"""Wiki index integrity: a committed index row pointing at an uncommitted page.

The defect is temporal, not logical: the row is correct when written — the author's
link resolves, because the page is on their disk — and dangling in every clone from
then on. So both sides of the comparison are read from HEAD, and the tests below
exist mostly to pin the ways this check could look clean without having looked.

Test order is deliberate. The "could not look" cases come FIRST, before any happy
path exists to make them seem redundant. Written the other way round, every one of
them would have been authored on a machine where the wiki repo happens to be
present and healthy — which is the one condition under which they cannot fail.
"""
import importlib.util
import subprocess
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "hooks" / "cost-discipline.py"
spec = importlib.util.spec_from_file_location("cost_discipline_wiki", MOD)
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _add(repo, rel, text):
    _write(repo, rel, text)
    _git(repo, "add", rel)


def _commit(repo, rel, text, msg="c"):
    _add(repo, rel, text)
    _git(repo, "commit", "-qm", msg)


# ---------------- could not look: must be None, never "clean" ----------------

def test_scan_returns_none_for_missing_path(tmp_path):
    assert cd.wiki_index_scan(tmp_path / "does-not-exist") is None


def test_scan_returns_none_for_non_git_dir(tmp_path):
    assert cd.wiki_index_scan(tmp_path) is None


def test_scan_returns_none_before_the_first_commit(tmp_path):
    """`ls-tree HEAD` fails in a repo with no commits. A fresh clone of an empty
    repo has no rows and no pages, and answering "clean" would be an instrument
    reporting on a tree it could not read."""
    assert cd.wiki_index_scan(_repo(tmp_path)) is None


def test_scan_returns_none_when_no_index_file_is_committed(tmp_path):
    """The empty-set vacuity case, and the most important test in this file.

    Point `wiki_path` at any repo without index files — the packaged default is
    ~/docs — and every per-row assertion passes because there are no rows. That
    is indistinguishable from a healthy wiki unless the scan refuses to answer.
    """
    repo = _repo(tmp_path)
    _commit(repo, "brain/page.md", "# a page, but nothing indexes it\n")
    assert cd.wiki_index_scan(repo) is None, "no index file means no measurement, not a pass"


# ---------------- the defect itself ----------------

def test_clean_when_row_and_page_are_both_committed(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "brain/topic/page.md", "# page\n")
    _add(repo, "brain/topic/_index.md", "| [[brain/topic/page]] | active |\n")
    _git(repo, "commit", "-qm", "both")
    dangling, links, indexes, truncated, unparsed = cd.wiki_index_scan(repo)
    assert dangling == []
    assert links == 1, "the link must actually have been examined"
    assert (indexes, truncated, unparsed) == (1, 0, [])


def test_dangling_when_target_page_is_untracked(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md", "| [[brain/topic/stranded]] | active |\n")
    _write(repo, "brain/topic/stranded.md", "# on disk only\n")
    dangling, _, _, _, _ = cd.wiki_index_scan(repo)
    assert dangling == [("brain/topic/_index.md", "brain/topic/stranded")]


def test_dangling_when_target_page_is_staged_but_not_committed(tmp_path):
    """Distinguishes HEAD from the index, which is the whole design choice.

    An `ls-files` implementation passes this test's fixture — the page IS in the
    staging area — and a clone still gets a dangling link, because a clone sees
    commits. The fixture asserts its own premise so it cannot quietly stop
    exercising the case.
    """
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md", "| [[brain/topic/staged]] | active |\n")
    _add(repo, "brain/topic/staged.md", "# staged\n")
    assert "brain/topic/staged.md" in _git(repo, "ls-files").stdout, \
        "fixture: the page must be in the staging area for this test to mean anything"
    dangling, _, _, _, _ = cd.wiki_index_scan(repo)
    assert dangling == [("brain/topic/_index.md", "brain/topic/staged")]


def test_uncommitted_index_row_is_not_reported(tmp_path):
    """A row and its page both still in the worktree are work in flight, not a
    defect. Reading the index from disk would invent a finding here — which is
    how a nudge earns being ignored."""
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md", "| header |\n")
    _write(repo, "brain/topic/_index.md", "| header |\n| [[brain/topic/draft]] |\n")
    _write(repo, "brain/topic/draft.md", "# draft\n")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert dangling == []
    assert links == 0, "the uncommitted row must not even be examined"


# ---------------- markup that only looks like a link ----------------
# A naive `[[` scan counts prose. The pattern this guards against has already
# fired twice in this codebase's history on `<rect` and `<script` occurrences that
# were entirely inside comments, so the check skipped itself in silence.

def test_link_inside_a_backtick_fence_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md",
            "rows below\n\n```\n| [[brain/topic/example]] | how to write a row |\n```\n")
    dangling, links, _, _, unparsed = cd.wiki_index_scan(repo)
    assert (dangling, links, unparsed) == ([], 0, [])


def test_link_inside_a_tilde_fence_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md", "~~~\n[[brain/topic/example]]\n~~~\n")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert (dangling, links) == ([], 0)


def test_link_inside_an_html_comment_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md",
            "<!--\ntemplate: | [[brain/topic/example]] | status |\n-->\n")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert (dangling, links) == ([], 0)


def test_link_inside_an_inline_code_span_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md", "write rows as `[[brain/topic/example]]` here\n")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert (dangling, links) == ([], 0)


def test_unterminated_fence_is_reported_not_swallowed(tmp_path):
    """Stopping at an unclosed fence would under-report — the permissive
    direction. The file is named as not fully scanned instead."""
    repo = _repo(tmp_path)
    _commit(repo, "brain/topic/_index.md",
            "```\nunclosed fence, and below it a real row\n| [[brain/topic/gone]] |\n")
    dangling, _, _, _, unparsed = cd.wiki_index_scan(repo)
    assert unparsed == ["brain/topic/_index.md"]
    assert dangling == [], "the row is inside the open fence; coverage is what is flagged"


# ---------------- link-form resolution (false positives are the real cost) ----------------

def test_alias_and_heading_fragments_are_stripped(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "brain/topic/page.md", "# page\n")
    _add(repo, "brain/topic/_index.md",
         "[[brain/topic/page|a nicer label]] and [[brain/topic/page#a-heading]]\n")
    _git(repo, "commit", "-qm", "aliases")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert (dangling, links) == ([], 2)


def test_bare_title_link_resolves_by_filename(tmp_path):
    """Obsidian resolves a link with no slash against filenames anywhere in the
    vault, so requiring a full path here would flag working links."""
    repo = _repo(tmp_path)
    _add(repo, "brain/deep/nested/page.md", "# page\n")
    _add(repo, "brain/_index.md", "[[page]]\n")
    _git(repo, "commit", "-qm", "bare")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert (dangling, links) == ([], 1)


def test_bare_title_link_with_no_matching_file_is_dangling(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/_index.md", "[[nothing-by-this-name]]\n")
    dangling, _, _, _, _ = cd.wiki_index_scan(repo)
    assert dangling == [("brain/_index.md", "nothing-by-this-name")]


def test_external_url_link_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/_index.md", "[[https://example.com/page]]\n")
    dangling, links, _, _, _ = cd.wiki_index_scan(repo)
    assert (dangling, links) == ([], 0)


def test_index_cap_is_reported_rather_than_silently_truncating(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "a/_index.md", "| no rows |\n")
    _add(repo, "b/_index.md", "| no rows |\n")
    _git(repo, "commit", "-qm", "two indexes")
    monkeypatch.setattr(cd, "WIKI_INDEX_CAP", 1)
    _, _, indexes, truncated, _ = cd.wiki_index_scan(repo)
    assert (indexes, truncated) == (1, 1), "a bound that is hit must be visible to the caller"


# ---------------- the advisory: silence must stay readable ----------------

def test_context_is_throttled_off_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(cd, "wiki_index_scan", lambda repo=None: calls.append(1))
    assert cd.wiki_index_context({"prompts_seen": 5}) == (None, None)
    assert calls == [], "off-interval prompts must not shell out to git"


def test_skipped_and_clean_are_different_outcomes(monkeypatch, tmp_path):
    """Both emit no advisory, and that is correct — but the fire log has to be
    able to tell "found nothing" from "never ran", or the check is unfalsifiable
    from the outside."""
    monkeypatch.setattr(cd, "WIKI_DIR", tmp_path / "not-a-repo")
    assert cd.wiki_index_context({"prompts_seen": 1}) == (None, "skipped")

    repo = _repo(tmp_path / "wiki")
    _add(repo, "brain/page.md", "# page\n")
    _add(repo, "brain/_index.md", "[[brain/page]]\n")
    _git(repo, "commit", "-qm", "clean")
    monkeypatch.setattr(cd, "WIKI_DIR", repo)
    assert cd.wiki_index_context({"prompts_seen": 1}) == (None, "clean")


def test_context_message_names_the_count_and_a_sample(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "brain/_index.md", "[[brain/stranded]]\n")
    monkeypatch.setattr(cd, "WIKI_DIR", repo)
    msg, outcome = cd.wiki_index_context({"prompts_seen": 1})
    assert outcome == "dangling"
    assert "1 committed index row" in msg
    assert "[[brain/stranded]]" in msg


def test_hygiene_failure_does_not_disarm_the_wiki_check(monkeypatch, capsys):
    """Regression for the shared-`except` shape (2026-07-26): a cheap sibling's
    exception reaching a common handler and taking a load-bearing check offline
    with it. Two advisories in one try block is that shape exactly."""
    def boom(state):
        raise RuntimeError("hygiene scan blew up")

    fired = []
    monkeypatch.setattr(cd, "load_state", lambda sid: {"session_id": sid, "prompts_seen": 0})
    monkeypatch.setattr(cd, "save_state", lambda state: None)
    monkeypatch.setattr(cd, "log_fire", lambda rule, *a, **k: fired.append(rule))
    monkeypatch.setattr(cd, "hygiene_context", boom)
    monkeypatch.setattr(cd, "rlm_fanout_context", lambda prompt: None)
    monkeypatch.setattr(cd, "wiki_index_context", lambda state: ("WIKI ADVISORY", "dangling"))

    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "hi"})
    out = capsys.readouterr().out.strip()
    assert "WIKI ADVISORY" in out, "the wiki check must survive a hygiene fault"
    assert "wiki_index_integrity" in fired
