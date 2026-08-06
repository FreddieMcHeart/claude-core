# Workstream-Advisory Second Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair ten reproduced defects in the read-before-work advisory on branch `feat/workstream-page-before-work`, so PR #52 can go to a third independent review.

**Architecture:** All changes are confined to `hooks/cost-discipline.py` (six functions plus `new_state` and `handle_post_compact`) and `tests/test_workstream_page.py`. The through-line of the ten defects is **ordering and reporting**: three bounds are applied before the filters that should precede them, and three incomplete-search states are reported as complete ones. The repair moves every truncation to the last possible moment and gives every incomplete state its own outcome string.

**Tech Stack:** Python 3.11+/3.13, pytest, `subprocess` + `git` plumbing, no third-party deps. The hook is a standalone script loaded in tests via `importlib` (there is no `conftest.py` in this repo — each test file loads the module itself).

## Global Constraints

- Land via PR + CI. **Never push to `main`.** Branch `feat/workstream-page-before-work` already exists and is pushed; keep using it.
- The suite must stay green at every commit: `python3 -m pytest tests/` — currently **269 passed**.
- Every test must have a **reachable failing state**. Three tests just failed this bar in a file whose own docstring claims the class was eradicated, so Task 9 mutates the production code and asserts which tests die.
- **"Could not look" must stay distinct from "searched and found nothing."**
- **A bound that truncates must be REPORTED**, never applied silently.
- Verification is **by execution against a real committed git fixture**, never by reading. Each task re-runs the reproduction that established its defect.
- Everything written is English — code, comments, tests, commit messages.
- No AI attribution and no `Co-Authored-By` trailer in any commit message.
- **Out of scope, decided:** extracting shared helpers (`_content_lines`, `_resolve_target`, `_git_out`) from `_wikilinks_in` / `wiki_index_scan` / `hygiene_scan`. Defect 5 is the fourth divergence from those siblings and the extraction is correct in principle — but this branch has already been rewritten twice, and touching two working checks to serve a third that currently matches zero pages trades live correctness for latent tidiness. Task 11 records it in `ROADMAP.md` instead.
- **Out of scope, decided:** extending `WORKSTREAM_KEY_DENY` to cover `US-EAST`, `SOC`, `ERROR`. The list already produced one defect (`SHA3` slipping past `SHA`) and gives undiagnosable silence to any project whose key is `CI`/`MD`/`PY`/`ES`. After Tasks 2 and 4 a false-positive key costs one bounded scan and says nothing. Do not re-open this.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `hooks/cost-discipline.py` | the advisory: key parsing, vault scan, verdict, state lifecycle | 1–8, 10 |
| `tests/test_workstream_page.py` | fixture vault + all assertions for the advisory | 1–10 |
| `ROADMAP.md` | records the deferred helper extraction | 11 |

---

### Task 1: Stop the test suite writing into the production fire log

**Files:**
- Modify: `tests/test_workstream_page.py` — the `live` fixture
- Verify: `~/.claude/state/cost-discipline-log.jsonl` (outside the repo — cleaned, not committed)

**Interfaces:**
- Produces: `live` fixture additionally yields a `fired` list of `(rule, outcome)` tuples that later tasks assert against.
- Consumes: nothing.

This is first because it is the only defect that damages state **outside** the PR, and because every later task runs the suite repeatedly and would add more contamination.

- [ ] **Step 1: Confirm the contamination exists**

Run:
```bash
grep -c '"session_id": "s1"' ~/.claude/state/cost-discipline-log.jsonl
```
Expected: a non-zero count (24 at the time of writing). If it is 0, the log has been rotated — continue anyway; the fixture fix still applies.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_workstream_page.py`:

```python
def test_the_outcome_reaches_the_fire_log(live, fired):
    """Nothing asserted that the outcome was logged — the fire log is this check's
    only evidence channel, and ROADMAP.md calls it the one instrument we trust."""
    cd.handle_user_prompt_submit({"session_id": "s1", "prompt": "work on PLAT-3113"})
    assert ("workstream_page", "unopened") in fired
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_the_outcome_reaches_the_fire_log -v`
Expected: FAIL with `fixture 'fired' not found`.

- [ ] **Step 4: Add the stub to the `live` fixture and expose `fired`**

Replace the `live` fixture in `tests/test_workstream_page.py` with:

```python
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
```

- [ ] **Step 5: Run the new test and the whole file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS, 38 tests.

- [ ] **Step 6: Prove the suite no longer writes to the real log**

Run:
```bash
BEFORE=$(wc -l < ~/.claude/state/cost-discipline-log.jsonl)
python3 -m pytest tests/test_workstream_page.py -q > /dev/null
AFTER=$(wc -l < ~/.claude/state/cost-discipline-log.jsonl)
echo "before=$BEFORE after=$AFTER"
```
Expected: `before` equals `after`. This is the real-artifact check — the test above proves `log_fire` was called, this proves it was not the production one.

- [ ] **Step 7: Delete the contaminating rows**

Run:
```bash
L=~/.claude/state/cost-discipline-log.jsonl
cp "$L" "$L.bak-$(date +%Y%m%d-%H%M%S)"
grep -v '"session_id": "s1"' "$L" > "$L.tmp" && mv "$L.tmp" "$L"
grep -c '"session_id": "s1"' "$L" || echo "0 remaining"
```
A dated backup is taken first because this edits an append-only instrument outside the repo.

- [ ] **Step 8: Commit**

```bash
git add tests/test_workstream_page.py
git commit -m "fix(test): stub log_fire so the suite stops writing to the production fire log"
```

---

### Task 2: Filter settled keys before applying the per-prompt bound

**Files:**
- Modify: `hooks/cost-discipline.py` — `workstream_keys` (signature), `workstream_page_context` (call site)
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `workstream_keys(prompt, exclude=())` → `(keys, truncated)`. `exclude` is an iterable of already-settled keys skipped during accumulation, so the bound applies to the keys that can still be acted on. Existing single-argument calls keep working.
- Consumes: `live` fixture from Task 1.

- [ ] **Step 1: Write the failing test**

```python
def test_settled_keys_do_not_consume_the_per_prompt_bound(live):
    """Reproduced before the fix: a prompt naming four keys settles the first three,
    and the fourth — which HAS a committed page — is unreachable for the rest of the
    session, because the bound was applied before the settled-filter."""
    st = _state(workstream_keys_fired=["AAA-11", "BBB-22", "CCC-33"])
    keys, truncated = cd.workstream_keys(
        "AAA-11 BBB-22 CCC-33 PLAT-3113", exclude=st["workstream_keys_fired"])
    assert keys == ["PLAT-3113"]
    assert truncated == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_settled_keys_do_not_consume_the_per_prompt_bound -v`
Expected: FAIL with `TypeError: workstream_keys() got an unexpected keyword argument 'exclude'`.

- [ ] **Step 3: Add `exclude` to `workstream_keys`**

In `hooks/cost-discipline.py`, change the signature and the accumulation loop:

```python
def workstream_keys(prompt, exclude=()):
    """Ticket-shaped keys in the prompt, deduped, in order, bounded.

    `exclude` holds keys already settled this session. They are skipped DURING
    accumulation, not after, because the bound must apply to the keys that can still
    be acted on. Reproduced before this argument existed: a prompt naming four keys
    settled the first three on prompt 1, and the fourth — which had a committed page —
    was unreachable for the rest of the session, since `workstream_keys` truncated to
    three by appearance order and the caller filtered afterwards.

    Returns (keys, truncated_count). The bound is REPORTED by the caller when hit,
    because a silently truncated list is a coverage claim nobody can check.
    """
    excluded = set(exclude)
    seen, order = set(), []
    for m in WORKSTREAM_KEY_RE.finditer(prompt or ""):
        if m.group(1).rstrip("0123456789") in WORKSTREAM_KEY_DENY:
            continue
        key = m.group(0)
        if key in seen or key in excluded:
            continue
        seen.add(key)
        if len(order) < WORKSTREAM_MAX_KEYS:
            order.append(key)
    return order, max(0, len(seen) - len(order))
```

- [ ] **Step 4: Update the call site in `workstream_page_context`**

Replace the opening of `workstream_page_context`:

```python
    settled = state.setdefault("workstream_keys_fired", [])
    keys, truncated_keys = workstream_keys(prompt, exclude=settled)
    if not keys:
        # Either the prompt named no key at all, or every key it named is settled.
        # Those are different states and only the second is worth logging.
        return (None, "already-advised" if WORKSTREAM_KEY_RE.search(prompt or "") else None)
    if state.get("workstream_scans", 0) >= WORKSTREAM_MAX_SCANS:
        return (None, "scan-budget-spent")

    fresh = keys
```

Delete the old `fresh = [k for k in keys if k not in settled]` and the `if not fresh:` block that followed it — `exclude` now does that job before the bound.

- [ ] **Step 5: Run the new test and the whole file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS. `test_fires_once_per_key_for_a_settled_key` still passes — it asserts the `already-advised` outcome, which the new branch preserves.

- [ ] **Step 6: Re-run the original reproduction**

```bash
cd ~/dev/claude-core && python3 - <<'PY'
import importlib.util, pathlib, subprocess, tempfile
s = importlib.util.spec_from_file_location("cd", pathlib.Path("hooks/cost-discipline.py"))
cd = importlib.util.module_from_spec(s); s.loader.exec_module(cd)
t = pathlib.Path(tempfile.mkdtemp()); repo = t / "w"; (repo / "b").mkdir(parents=True)
subprocess.run(["git", "init", "-q", str(repo)], check=True)
for k, v in (("user.email", "t@t"), ("user.name", "t")):
    subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
(repo / "b" / "PLAT-3116-real.md").write_text("x")
subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "x"], check=True, capture_output=True)
cd.WIKI_DIR = repo; cd._WIKI_PATH = str(repo)
sd = t / "st"; sd.mkdir(); cd.state_path = lambda sid: sd / f"{sid}.json"
st = cd.new_state("s1")
p = "handle PLAT-3113, PLAT-3114, PLAT-3115 and PLAT-3116"
print("prompt 1 ->", cd.workstream_page_context(st, p)[1])
print("prompt 2 ->", cd.workstream_page_context(st, p)[1])
PY
```
Expected: prompt 2 now returns `unopened` and names `b/PLAT-3116-real.md`, where it previously returned `already-advised`.

- [ ] **Step 7: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): filter settled keys before applying the per-prompt bound"
```

---

### Task 3: Sample pages after the already-read filter, not before

**Files:**
- Modify: `hooks/cost-discipline.py` — `workstream_page_scan` (drop the slice), `workstream_page_context` (slice after filtering)
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `workstream_page_scan(...)["hits"]` now holds the **full** resolved path list per key, and `truncated_pages` is no longer computed there. `workstream_page_context` computes the truncation after filtering reads.
- Consumes: Task 1's `live` fixture.

- [ ] **Step 1: Write the failing test**

```python
def test_already_read_pages_do_not_consume_the_sample_budget(live, vault):
    """Reproduced before the fix: three pages for one key, two already read, both
    sample slots spent on them, and the third — the unread one — never named. The
    check was silenced by the two pages the agent happened to open first."""
    for name in ("K-11-aaa", "K-11-bbb", "K-11-ccc"):
        (vault / "brain" / "proj" / f"{name}.md").write_text("x")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "three pages")
    st = _state(wiki_paths_read=["brain/proj/K-11-aaa.md", "brain/proj/K-11-bbb.md"])
    msg, outcome = cd.workstream_page_context(st, "K-11")
    assert outcome == "unopened"
    assert "brain/proj/K-11-ccc.md" in msg
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_already_read_pages_do_not_consume_the_sample_budget -v`
Expected: FAIL — `outcome` is `"opened"` and `msg` is `None`.

- [ ] **Step 3: Remove the slice from the scan**

In `workstream_page_scan`, replace the tail of the per-key loop:

```python
        if found:
            hits[key] = found
    return {"hits": hits, "truncated_indexes": truncated_indexes}
```

Delete the `sample_truncated` accumulator and its initialiser. Update the docstring's return line to:

```python
    """{"hits": {key: [all resolved paths]}, "truncated_indexes": n}, or None.
```

and add to that docstring:

```python
    `hits` is NOT truncated here. The caller filters out pages this session already
    opened, and sampling before that filter let two already-read pages consume both
    slots and hide the unread one — the check silenced by the pages the agent happened
    to open first.
```

- [ ] **Step 4: Sample in the caller, after filtering**

In `workstream_page_context`, replace the `unopened` construction:

```python
    read = state.get("wiki_paths_read") or []
    unopened, truncated_pages = {}, 0
    for key, paths in hits.items():
        # Anchored: bare endswith matched across a path boundary, so a Read of
        # `snapshot.md` credited `hot.md` as opened and silenced the advisory.
        not_read = [p for p in paths if not any(r == p or r.endswith("/" + p) for r in read)]
        if not_read:
            truncated_pages += max(0, len(not_read) - WORKSTREAM_SAMPLE)
            unopened[key] = not_read[:WORKSTREAM_SAMPLE]
```

and change the caveat line that referenced the scan's field:

```python
    if truncated_pages:
        caveats.append(f"{truncated_pages} further page(s) not listed")
```

- [ ] **Step 5: Run the file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): sample pages after the already-read filter, not before"
```

---

### Task 4: An incomplete scan must not settle a key or claim "no-page"

**Files:**
- Modify: `hooks/cost-discipline.py` — `workstream_page_context`
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: a new outcome string `"no-page-partial"`, joining the existing vocabulary `no-page` / `opened` / `already-advised` / `skipped` / `scan-budget-spent` / `unopened`.
- Consumes: `scan["truncated_indexes"]` from Task 3's reshaped return, `truncated_pages` computed in the caller.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_an_incomplete_scan_is_not_reported_as_no_page -v`
Expected: FAIL — outcome is `"no-page"` and the key is in `workstream_keys_fired`.

- [ ] **Step 3: Branch on completeness before settling**

In `workstream_page_context`, replace the `if not unopened:` block:

```python
    incomplete = bool(scan["truncated_indexes"]) or bool(truncated_keys)
    if not unopened:
        if incomplete and not hits:
            # Do NOT settle. The scan did not finish enumerating the population, so
            # "no page exists" is an absence claim the code cannot support, and
            # settling would make it permanent for the session. The qualified outcome
            # follows the sibling vocabulary (`skipped:not_installed`).
            return (None, "no-page-partial")
        _mark(fresh)
        return (None, "opened" if hits else "no-page")
```

- [ ] **Step 4: Run the file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS.

- [ ] **Step 5: Update the outcome vocabulary in the docstring**

In `workstream_page_context`'s docstring, replace the enumeration line so it lists every value the function can return:

```python
    `outcome` is logged on every run that had a key to act on, including the silent
    ones (`no-page`, `no-page-partial`, `opened`, `already-advised`, `skipped`,
    `scan-budget-spent`), because a check that only speaks when it finds something is
    indistinguishable from one that was never wired up. When the prompt names no key
    at all, nothing ran and the outcome is None — a third state, and calling it
    "clean" would be the vacuity this repo has a page about.
```

- [ ] **Step 6: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): an incomplete scan no longer claims no-page or settles the key"
```

---

### Task 5: Report an unterminated fence instead of swallowing the rest of the file

**Files:**
- Modify: `hooks/cost-discipline.py` — `workstream_page_scan`, the per-index-file loop
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `truncated_indexes` now also counts index files whose fence never closed. No signature change.
- Consumes: Task 4's `incomplete` branch, which this feeds.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_an_unterminated_fence_is_reported_not_swallowed -v`
Expected: FAIL on the second assertion — `truncated_indexes == 0`.

- [ ] **Step 3: Hoist the index parse out of the per-key loop and report the fence**

This is also the fix for the per-key re-parse two reviewers flagged. In `workstream_page_scan`, replace the `index_text` collection and the per-key index walk with a single pre-parse:

```python
    index_paths = sorted(p for p in tracked if p.rsplit("/", 1)[-1] in WIKI_INDEX_NAMES)
    index_rows, unread = [], 0
    for path in index_paths[:WIKI_INDEX_CAP]:
        content = git("show", f"HEAD:{path}")
        if content is None:
            unread += 1
            continue
        # Parsed ONCE per file rather than once per key: the strip, the split and the
        # fence walk depend only on the content, and sitting inside `for key in keys`
        # re-ran them WORKSTREAM_MAX_KEYS times against the same 2s budget.
        body = _HTML_COMMENT_RE.sub("", content)
        fence, marker = False, ""
        for line in body.splitlines():
            stripped = line.lstrip()
            if fence:
                if stripped.startswith(marker):
                    fence, marker = False, ""
                continue
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence, marker = True, stripped[:3]
                continue
            if not stripped.startswith("|"):
                continue
            cells = stripped.split("|")
            first = cells[1] if len(cells) > 1 else ""
            m = _WIKILINK_RE.search(_INLINE_CODE_RE.sub("", first))
            if m:
                index_rows.append((stripped.lower(), m.group(1)))
        if fence:
            # An unterminated fence means the rest of the file was skipped. Reporting
            # it is the whole point: `_wikilinks_in` says "skipping the rest of the
            # file would under-report — the permissive direction — and silence from a
            # check that stopped reading is the failure this exists to avoid."
            unread += 1
    truncated_indexes = max(0, len(index_paths) - WIKI_INDEX_CAP) + unread
```

Then replace the per-key index walk with a filter over the pre-parsed rows:

```python
    hits = {}
    lowered = [(p, p.lower()) for p in tracked]
    for key in keys:
        kl = re.escape(key.lower())
        key_re = re.compile(r"(?<![a-z0-9])" + kl + r"(?![a-z0-9])")
        found = sorted(p for p, pl in lowered if key_re.search(pl))
        for row_lower, target in index_rows:
            if not key_re.search(row_lower):
                continue
            resolved = _resolve(target)
            if resolved and resolved not in found:
                found.append(resolved)
        if found:
            hits[key] = found
```

- [ ] **Step 4: Run the file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS. `test_scan_ignores_fenced_and_commented_rows` and `test_scan_takes_the_link_from_the_rows_first_cell` still pass — the parse moved, its rules did not.

- [ ] **Step 5: Re-run the original reproduction**

```bash
cd ~/dev/claude-core && python3 - <<'PY'
import importlib.util, pathlib, subprocess, tempfile
s = importlib.util.spec_from_file_location("cd", pathlib.Path("hooks/cost-discipline.py"))
cd = importlib.util.module_from_spec(s); s.loader.exec_module(cd)
t = pathlib.Path(tempfile.mkdtemp()); repo = t / "w"; (repo / "b").mkdir(parents=True)
subprocess.run(["git", "init", "-q", str(repo)], check=True)
for k, v in (("user.email", "t@t"), ("user.name", "t")):
    subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
(repo / "b" / "target.md").write_text("x")
(repo / "b" / "_index.md").write_text(
    "```\nexample fence never closed\n\n| [[b/target]] | LIVE-11 below the break |\n")
subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "x"], check=True, capture_output=True)
r = cd.workstream_page_scan(["LIVE-11"], repo=repo)
print("hits:", r["hits"], "truncated_indexes:", r["truncated_indexes"])
PY
```
Expected: `truncated_indexes: 1`, where it was `0`.

- [ ] **Step 6: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): report an unterminated fence, and parse each index once"
```

---

### Task 6: An ambiguous bare stem resolves to nothing

**Files:**
- Modify: `hooks/cost-discipline.py` — `workstream_page_scan`, the `stems` construction and `_resolve`
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `stems` becomes `{stem: path_or_None}` where `None` marks an ambiguous basename. `_resolve` returns `None` for those.
- Consumes: nothing new.

**Decision, and why not simply sorting:** determinism alone would make the scan pick the same wrong page every time. The advisory states the path as fact in the one sentence it exists to produce, so naming the wrong page is worse than naming none. Sorting converts a coin flip into a reliable falsehood; refusing converts it into a silence, which is the safe direction this file uses everywhere else.

- [ ] **Step 1: Write the failing test**

```python
def test_an_ambiguous_bare_stem_resolves_to_nothing(live, vault):
    """`stems` was a dict built by iterating a SET, so a colliding basename resolved
    to whichever path set iteration happened to yield — reproduced across three
    processes as z/dup.md, a/dup.md, z/dup.md for identical input. Determinism is not
    the fix: the advisory states the path as fact, so the wrong page reliably is worse
    than no page at all."""
    for d in ("one", "two"):
        (vault / "brain" / d).mkdir(exist_ok=True)
        (vault / "brain" / d / "dup.md").write_text("x")
    (vault / "brain" / "collide_index").mkdir(exist_ok=True)
    (vault / "brain" / "collide_index" / "_index.md").write_text(
        "| [[dup]] | COLL-11 |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "colliding stems")
    assert cd.workstream_page_scan(["COLL-11"], repo=vault)["hits"] == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_an_ambiguous_bare_stem_resolves_to_nothing -v`
Expected: FAIL — `hits` names one of the two colliding pages.

- [ ] **Step 3: Mark ambiguous stems**

In `workstream_page_scan`, replace the `stems` construction:

```python
    # {stem: path}, with an AMBIGUOUS stem mapped to None rather than to an arbitrary
    # winner. `tracked` is a set, so a dict comprehension over it let a colliding
    # basename resolve differently between processes — the same input answering
    # z/dup.md, a/dup.md, z/dup.md across three runs. Determinism would only make the
    # wrong answer reliable; the advisory states this path as fact, so an ambiguous
    # stem must resolve to nothing.
    stems = {}
    for p in tracked:
        stem = p[:-3].rsplit("/", 1)[-1]
        stems[stem] = None if stem in stems else p
```

- [ ] **Step 4: Run the file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS. `test_scan_resolves_a_bare_stem_through_the_stem_map` still passes — `bare-stem-target` is unique in the fixture.

- [ ] **Step 5: Re-run the cross-process reproduction**

```bash
cd ~/dev/claude-core && for i in 1 2 3; do PYTHONHASHSEED=random python3 - <<'PY'
import importlib.util, pathlib, subprocess, tempfile
s = importlib.util.spec_from_file_location("cd", pathlib.Path("hooks/cost-discipline.py"))
cd = importlib.util.module_from_spec(s); s.loader.exec_module(cd)
t = pathlib.Path(tempfile.mkdtemp()); repo = t / "w"
for d in ("a", "z"): (repo / d).mkdir(parents=True)
subprocess.run(["git", "init", "-q", str(repo)], check=True)
for k, v in (("user.email", "t@t"), ("user.name", "t")):
    subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
(repo / "a" / "dup.md").write_text("A"); (repo / "z" / "dup.md").write_text("Z")
(repo / "_index.md").write_text("| [[dup]] | COLL-11 |\n")
subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "x"], check=True, capture_output=True)
print("   ", cd.workstream_page_scan(["COLL-11"], repo=repo)["hits"])
PY
done
```
Expected: `{}` on all three runs.

- [ ] **Step 6: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): an ambiguous bare stem resolves to nothing, not to a coin flip"
```

---

### Task 7: Stop dropping seven-digit issue numbers

**Files:**
- Modify: `hooks/cost-discipline.py` — `WORKSTREAM_KEY_RE`
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `WORKSTREAM_KEY_RE` matches 1–9 digits. No signature change.

**Decision recorded, do not re-open:** `US-EAST-1` → `EAST-1`, `SOC-2` and `ERROR-500` are false positives and are NOT being fixed by extending `WORKSTREAM_KEY_DENY`. The list already produced one defect (`SHA3` slipping past a list containing `SHA`) and hands undiagnosable silence to any project whose key is `CI`, `MD`, `PY` or `ES` — a denied prefix yields no key, hence outcome `None`, hence no fire-log line at all. After Tasks 2 and 4 a false-positive key costs one bounded scan, settles, and says nothing.

- [ ] **Step 1: Write the failing test**

```python
def test_a_seven_digit_issue_number_is_not_dropped():
    """`\\d{1,6}` plus a lookahead rejecting alnum meant every backtrack failed on a
    7-digit number, so PLAT-1234567 was dropped ENTIRELY rather than truncated. Large
    Jira instances reach seven digits; that is loss of a real key."""
    assert cd.workstream_keys("close PLAT-1234567")[0] == ["PLAT-1234567"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_a_seven_digit_issue_number_is_not_dropped -v`
Expected: FAIL — `[] != ["PLAT-1234567"]`.

- [ ] **Step 3: Widen the digit bound**

In `hooks/cost-discipline.py`, replace the regex and extend its comment:

```python
# The trailing guard is (?![A-Za-z0-9]) rather than \b: `_` is a word character, so \b
# silently refused `PLAT-3113_notes`, which is how branch names and filenames spell it.
# The digit bound is 1-9: one digit is a real key (`PLAT-7`), and the earlier 1-6 bound
# did not TRUNCATE a longer number, it dropped it — every backtrack failed the
# lookahead, so `PLAT-1234567` produced no key at all. Large Jira instances reach seven.
WORKSTREAM_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,9})(?![A-Za-z0-9])")
```

- [ ] **Step 4: Run the file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS, including the existing `test_single_digit_issue_numbers_are_keys`.

- [ ] **Step 5: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): a seven-digit issue number is a key, not a dropped match"
```

---

### Task 8: Anchor the vault-path check and apply it at all three recording sites

**Files:**
- Modify: `hooks/cost-discipline.py` — `_vault_relative`, the recording block in `handle_pre_tool`
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `_vault_relative(path)` resolves against `_WIKI_PATH` and against a `docs/core` mount **that resolves to the same tree**, returning `None` otherwise. `handle_pre_tool` uses it for `Read`, `Grep`/`Glob` and `Bash`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_foreign_docs_core_path_is_not_credited_as_the_vault(live):
    """The `/docs/core/` branch was a bare substring, so ANY path in ANY repo
    containing that segment was credited as a vault page — the over-crediting
    direction the recording site says it must not have. A foreign
    docs/core/brain/proj/x.md would silence the advisory for a key whose page is x.md."""
    assert cd._vault_relative("/some/other/repo/docs/core/brain/proj/a.md") is None


def test_the_configured_vault_still_resolves(live):
    assert cd._vault_relative(str(live / "brain/proj/a.md")) == "brain/proj/a.md"
```

- [ ] **Step 2: Run them to verify the first fails**

Run: `python3 -m pytest tests/test_workstream_page.py -k vault_relative -v` (or run the two names above)
Expected: `test_a_foreign_docs_core_path_is_not_credited_as_the_vault` FAILS, returning `"brain/proj/a.md"`.

- [ ] **Step 3: Resolve instead of substring-matching**

Replace `_vault_relative`:

```python
def _vault_relative(path):
    """An absolute Read path → its vault-relative form, or None if it is not in the vault.

    Two roots, not one. `_WIKI_PATH` is the canonical clone; this repo also mounts the
    same vault at `docs/core/` (a symlink on this machine), so one page is reachable
    under two absolute paths, and recording only the first made the advisory assert
    "this session has not opened it" about a page the session had just opened.

    Both sides are RESOLVED, which is what makes the mount work WITHOUT hardcoding it:
    on this machine `docs/core` is a symlink to the canonical clone, so `.resolve()`
    collapses a path through the mount onto the same real path as the canonical one,
    and a single root comparison covers both. Under the separate-checkout layout that
    `~/dev/claude-core/CLAUDE.md` also documents, the mount is genuinely a different
    tree — and treating it as not-the-vault is then correct, not a miss.

    The first version tested `"/docs/core/" in path`, which credited any file in any
    repository whose path happened to contain that segment — over-crediting, the
    direction the recording site below says it must not have. Do not reintroduce a
    hardcoded mount literal here; resolve instead.
    """
    if not path:
        return None
    try:
        target = Path(path).resolve()
        base = Path(_WIKI_PATH).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        return str(target.relative_to(base))
    except ValueError:
        return None
```

- [ ] **Step 4: Apply the same seam to Grep/Glob and Bash**

In `handle_pre_tool`, replace the three-branch block so all three use one predicate:

```python
    _wiki_path_hit = False
    _wiki_read_target = ""
    if tool_name == "Read":
        _wiki_read_target = tool_input.get("file_path") or ""
        _wiki_path_hit = _vault_relative(_wiki_read_target) is not None
    elif tool_name in ("Grep", "Glob"):
        _wiki_path_hit = _vault_relative(tool_input.get("path") or "") is not None
    elif tool_name == "Bash":
        # Bash is a command STRING, not a path — there is nothing to resolve, so the
        # prefix test stays here and only here. Deliberately not extended with a
        # `/docs/core/` literal: that substring is what made the Read branch credit
        # foreign repositories, and a command string gives even less to anchor against.
        # The consequence is that a vault sweep issued through the mount is not counted;
        # under-counting is the safe direction, and it is stated rather than hidden.
        _wiki_path_hit = _WIKI_PATH in (tool_input.get("command") or "")
```

- [ ] **Step 5: Run the file and the whole suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS. `test_a_Read_through_the_docs_core_mount_is_recorded_too` will need its fictional `/x/dev/claude-core/docs/core/...` path replaced with a real resolvable one — if it fails, change it to build a mount under `tmp_path` with `Path.symlink_to(vault)` and assert through that.

- [ ] **Step 6: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): resolve vault paths instead of substring-matching the mount"
```

---

### Task 9: Repair the three tests that cannot fail, and mutate to prove it

**Files:**
- Modify: `tests/test_workstream_page.py` — the `vault` fixture and three tests
- Test: the mutation runs below

**Interfaces:**
- Consumes: everything above. Runs last among the code tasks because mutations must be run against the final production code.

- [ ] **Step 1: Commit the two pages the fence test depends on**

In the `vault` fixture, add before the `_git(repo, "add", "-A")` line:

```python
    # Committed on purpose. Without them `_resolve` returns None for both targets and
    # `test_scan_ignores_fenced_and_commented_rows` passes whether or not the fence
    # and comment machinery exists — a test that names a mechanism and asserts nothing
    # about it. Reproduced by deleting both guards and watching it still pass.
    (repo / "brain" / "proj" / "fenced-example.md").write_text("# fenced\n")
    (repo / "brain" / "proj" / "commented.md").write_text("# commented\n")
```

- [ ] **Step 2: Give the endswith test a discriminating case**

Replace `test_a_read_of_a_suffix_lookalike_does_not_credit_the_page`:

```python
def test_a_read_of_a_suffix_lookalike_does_not_credit_the_page(live, vault):
    """`'snapshot.md'.endswith('hot.md')` is True. The previous version of this test
    used a `not-` prefix sitting BEFORE the directory separator, so the unanchored
    form failed too and the test could not tell the two apart. The discriminating case
    needs a page at the vault ROOT, where the lookalike has no separator to save it."""
    (vault / "hot.md").write_text("# hot\n")
    (vault / "brain" / "proj" / "_index.md").write_text(
        (vault / "brain" / "proj" / "_index.md").read_text()
        + "| [[hot]] | ROOT-11 |\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "root page")
    st = _state(wiki_paths_read=["brain/proj/snapshot.md"])
    assert cd.workstream_page_context(st, "ROOT-11")[1] == "unopened"
```

- [ ] **Step 3: Give the first-cell test a discriminating row**

Add to the `vault` fixture's `_index.md`, and update the test:

```python
        # First cell holds NO link; a later cell does. The previous fixture row had the
        # subject link first on the line as well as first in the cell, so the test
        # passed whether the code sliced cells or searched the raw line.
        "| CELL-33 has no link here | see [[brain/proj/kafka-gen4-cutover]] |\n"
```

```python
def test_scan_takes_the_link_from_the_rows_first_cell(vault):
    """A row whose first cell has no link must yield nothing — the 'see also' in a
    later cell is not the row's subject."""
    assert cd.workstream_page_scan(["CELL-33"], repo=vault)["hits"] == {}
```

- [ ] **Step 4: Add the missing truncated_pages test**

```python
def test_the_page_bound_is_reported_in_the_message(live, vault, monkeypatch):
    """No test covered truncated_pages at all — of the three bounds the message claims
    to report, one was fully tested, one half, one not at all."""
    monkeypatch.setattr(cd, "WORKSTREAM_SAMPLE", 1)
    for name in ("P-77-one", "P-77-two"):
        (vault / "brain" / "proj" / f"{name}.md").write_text("x")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", "two pages")
    msg, outcome = cd.workstream_page_context(_state(), "P-77")
    assert outcome == "unopened"
    assert "1 further page(s) not listed" in msg
```

- [ ] **Step 5: Run the file**

Run: `python3 -m pytest tests/test_workstream_page.py -q`
Expected: PASS.

- [ ] **Step 6: Mutation pass — prove each repaired test can now fail**

Run this script. It copies the hook to scratch, applies one mutation at a time, and reports which tests die. A mutation that kills nothing is a test that asserts nothing.

```bash
cd ~/dev/claude-core && python3 - <<'PY'
import pathlib, shutil, subprocess, tempfile, os
src = pathlib.Path("hooks/cost-discipline.py").read_text()
MUTANTS = {
  "drop-fence-walk":      ('if stripped.startswith("```") or stripped.startswith("~~~"):',
                           'if False:'),
  "drop-comment-strip":   ('body = _HTML_COMMENT_RE.sub("", content)', 'body = content'),
  "unanchor-endswith":    ('r == p or r.endswith("/" + p)', 'r.endswith(p)'),
  "raw-line-not-cell":    ('m = _WIKILINK_RE.search(_INLINE_CODE_RE.sub("", first))',
                           'm = _WIKILINK_RE.search(stripped)'),
  "drop-page-bound":      ('unopened[key] = not_read[:WORKSTREAM_SAMPLE]',
                           'unopened[key] = not_read'),
}
tmp = pathlib.Path(tempfile.mkdtemp())
for name, (old, new) in MUTANTS.items():
    if src.count(old) != 1:
        print(f"{name}: ANCHOR occurs {src.count(old)} times, expected 1 — fix the anchor")
        continue
    work = tmp / name
    shutil.copytree(".", work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    (work / "hooks" / "cost-discipline.py").write_text(src.replace(old, new))
    r = subprocess.run(["python3", "-m", "pytest", "tests/test_workstream_page.py", "-q"],
                       cwd=work, capture_output=True, text=True)
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    print(f"{name:22} -> {tail[-1] if tail else 'no result'}")
PY
```
Expected: **every** mutation produces at least one failure. If any line reads `N passed` with no failures, the test for that mechanism still asserts nothing — fix it before continuing.

- [ ] **Step 7: Commit**

```bash
git add tests/test_workstream_page.py
git commit -m "fix(test): repair three tests that passed for reasons unrelated to their names"
```

---

### Task 10: Decide the compaction lifecycle for the three new state fields

**Files:**
- Modify: `hooks/cost-discipline.py` — `handle_post_compact`
- Test: `tests/test_workstream_page.py`

**Interfaces:**
- Produces: `handle_post_compact` resets `wiki_paths_read` and `workstream_keys_fired`; leaves `workstream_scans` and `wiki_read_count` alone.

**Decision, with the reasoning that settles it:** reset wholesale rather than recording a settle reason.

- `wiki_paths_read` **must** reset. It is context truth — after compaction the page's content is no longer in context, so "this session has it open" is false, and leaving it is the over-crediting direction the recording site forbids.
- `workstream_scans` **must not** reset. It is a per-session cost budget, not context. Resetting it grants a fresh 24 git scans after every compaction, which is the opposite of what a budget is for.
- `workstream_keys_fired` resets **wholesale**. Recording the settle reason would let `no-page` keys survive (the vault did not change) while `opened` keys reset — but that adds a second representation of settle state for the sake of avoiding a re-scan that is already bounded by `workstream_scans`, which does *not* reset. The cheaper correct choice is to reset the list and let the surviving budget bound the cost.
- `wiki_read_count` is deliberately untouched: it is a lifetime consumption counter feeding the wiki-first nudge, not context state.

- [ ] **Step 1: Write the failing test**

```python
def test_compaction_clears_context_derived_workstream_state(live):
    """handle_post_compact resets eight fields with the comment "compaction clears
    context" and reset none of these three. After a compaction the page content is
    gone, so "this session opened it" is false — leaving it is over-crediting."""
    st = _state(wiki_paths_read=["brain/proj/a.md"],
                workstream_keys_fired=["PLAT-3113"],
                workstream_scans=7,
                wiki_read_count=9)
    cd.save_state(st)
    cd.handle_post_compact({"session_id": "s1"})
    after = cd.load_state("s1")
    assert after["wiki_paths_read"] == [], "context truth must reset"
    assert after["workstream_keys_fired"] == [], "settled keys must reset"
    assert after["workstream_scans"] == 7, "a cost budget is not context and must survive"
    assert after["wiki_read_count"] == 9, "lifetime consumption is not context"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_workstream_page.py::test_compaction_clears_context_derived_workstream_state -v`
Expected: FAIL on the first assertion.

- [ ] **Step 3: Add the resets**

In `handle_post_compact`, next to the existing resets, add:

```python
    state["wiki_paths_read"] = []       # read-before-work: context truth. After a
                                        # compaction the page content is gone, so
                                        # "this session opened it" is false.
    state["workstream_keys_fired"] = [] # ditto — a key settled as "opened" is no
                                        # longer opened. Reset wholesale rather than
                                        # recording the settle reason: the re-scan cost
                                        # is already bounded by workstream_scans, which
                                        # deliberately does NOT reset, because a cost
                                        # budget is not context.
```

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/cost-discipline.py tests/test_workstream_page.py
git commit -m "fix(hook): reset context-derived workstream state on compaction"
```

---

### Task 11: Record the deferred helper extraction, then request review

**Files:**
- Modify: `ROADMAP.md`
- No test — documentation only.

- [ ] **Step 1: Add the ROADMAP entry**

Insert before the `## Tracked elsewhere (pointers, not duplicated here)` line:

```markdown
### The workstream advisory copies three helpers from its siblings, and the copies keep diverging

`workstream_page_scan` re-implements, inline, three things `_wikilinks_in`,
`wiki_index_scan` and `hygiene_scan` already do: the HTML-comment strip plus fence walk,
the wikilink-target resolution through `tracked`/`stems`, and the
`subprocess.run`-with-timeout wrapper. A reviewer counted roughly four omissions per
forty-five copied lines, and each omission was a real defect fixed after the fact —
bare-stem resolution, tracked-at-HEAD resolution, fence and comment stripping, and
unterminated-fence reporting. The fourth was found on 2026-08-05, after the first three
had already been repaired.

Rule-of-three does not apply cleanly: the copies are *deliberately* slightly different
(`tracked` is `.md`-only in one and all-files in the other; `stems` is a dict in one and
a set in the other), and those intended differences are exactly what camouflaged the
unintended ones.

Deferred from PR #52 on purpose. That branch had already been rewritten twice, and
touching two working checks to serve a third that currently matches zero pages in the
configured vault trades live correctness for latent tidiness. The extraction is three
units — `_content_lines(text) -> (lines, unterminated)`, `_resolve_target(target,
tracked, stems) -> path | None`, and a module-level `_git_out(repo, *args, timeout)` —
and wants its own branch, its own tests, and its own review pass.
```

- [ ] **Step 2: Run the whole suite one final time**

Run: `python3 -m pytest tests/ -q`
Expected: PASS. Record the number.

- [ ] **Step 3: Push and confirm CI**

```bash
git add ROADMAP.md
git commit -m "docs(roadmap): record the deferred helper extraction from PR #52"
git push
sleep 60 && gh pr view 52 --json mergeable,statusCheckRollup \
  --jq '"mergeable: \(.mergeable)\nchecks: " + ([.statusCheckRollup[]? | "\(.name)=\(.conclusion // .status)"] | join(", "))'
```
Expected: all four checks SUCCESS.

- [ ] **Step 4: Request the third independent review**

Hot-path code gets an independent pass before merge — that rule is what produced both prior rounds. Dispatch `feature-dev:code-reviewer` (that exact `subagent_type`; a bare `code-reviewer` does not exist and the dispatch fails).

**Note for whoever dispatches:** that agent's tool list is `Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite, WebSearch, KillShell, BashOutput` — `BashOutput` without `Bash`, so **it cannot execute anything**. Do not ask it to run code; ask it to produce hand-traces with exact reproduction commands, and run those commands yourself. Every finding from both prior rounds needed that step before it could be trusted.

- [ ] **Step 5: Do not merge**

Report the review outcome and wait for the user's decision. The user has merged nothing on this branch yet and has asked to be told plainly whether it should merge.
