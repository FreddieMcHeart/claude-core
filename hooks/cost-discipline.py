#!/usr/bin/env python3
# Cost-discipline hook for Claude Code.
# Single consolidated script wired into PreToolUse, PostToolUse, SessionStart, PostCompact.
# Mode is selected via the first CLI argument (matches obsidian-hot-cache-inject.sh convention).
# Usage: cost-discipline.py {pre-tool|post-tool|session-start|post-compact}
#
# Phase 1 — warning-only.  Hard blocks come in Phase 3 after calibration.
# Fails open: any exception → stderr + exit 0, no output, never blocks tools.
#
# State file: /tmp/cc-discipline-<session_id>.json
# Auto-cleans on reboot.  SessionStart wipes any stale file.
#
# Rule sources (skill content stays the source of truth):
#   ~/.claude/skills/delegation-discipline/references/hard-rules.md
#   ~/.claude/skills/models-router/references/sub-agent-routing.md

import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path("/tmp")

# ---- Cross-session cost ledger (2026-07-17) ----
# Every session writes a compact, durable per-session cost summary here so the whole
# session tree can be collected in one place (`cost-ledger/<session_id>.json`). This is
# separate from the /tmp working state (which auto-cleans on reboot): the cost ledger is
# the persistent, collectable record. It is machine-local runtime state, NOT part of the
# repo. Writing it is best-effort and must never block or fail a tool call.
COST_LEDGER_DIR = Path.home() / ".claude" / "cost-ledger"

# ---- Harness hygiene (2026-07-15) ----
# The harness repo (~/.claude) is edited from many concurrent sessions, and sessions
# rarely end cleanly — they are left open or killed — so any "commit on exit" nudge
# fires for almost nobody. Instead we pulse on prompt submit, which is the one event
# that reliably happens while the human is actually there.
#
# Throttle: fire on the FIRST prompt of a session and every Nth prompt after. Because
# handle_session_start() wipes state, prompts_seen restarts at 0 each session, so the
# first-prompt fire doubles as the session-start backstop — one mechanism covers both
# "you have been working a while without committing" and "you walked into an old pile".
#
# Thresholds are pile-level, not session-level, on purpose: the pile is what the human
# reasons about, and every file in it belongs to the same person regardless of which
# session created it.
HARNESS_DIR = Path.home() / ".claude"
HYGIENE_PROMPT_INTERVAL = 10   # check on prompt 1, 11, 21, ...
HYGIENE_MAX_FILES = 10         # dirty-file count above which we nudge
HYGIENE_MAX_AGE_DAYS = 7       # oldest dirty-file age above which we nudge
HYGIENE_GIT_TIMEOUT = 3        # seconds — a slow repo must never delay a prompt
HYGIENE_SAMPLE_COUNT = 3       # how many oldest paths to name in the nudge
# Session JSONLs live at ~/.claude/projects/<cwd-slug>/<session_id>.jsonl; the slug is
# project-specific, so jsonl_size() globs across all projects rather than hardcoding one.

# Global append-only fire log — survives reboot, used by Phase 2+ audits.
# One JSON line per fire event. ~150 bytes/line.
LOG_FILE = Path.home() / ".claude" / "state" / "cost-discipline-log.jsonl"

# Agent definition directories — scanned at SessionStart for frontmatter `model:`
# defaults. If a dispatch's subagent_type matches an agent here AND the agent
# has a frontmatter model, the dispatch is allowed without explicit
# `tool_input.model` (Phase 1.7, 2026-05-06).
# ---------- Portable config (defaults reproduce current project paths) ----------
# Read ~/.claude/platform.config.toml via the shared loader; any failure → today's
# hardcoded values (fail-open: byte-identical on this machine, never breaks if the
# loader/config is absent).
try:
    sys.path.insert(0, str(Path.home() / ".claude" / "lib"))
    from config_loader import config as _PLATFORM_CFG
    _PROJECT_ROOT = _PLATFORM_CFG.get("project_root") or str(Path.home())
    _WIKI_PATH = _PLATFORM_CFG.get("wiki_path") or str(Path.home() / "docs")
except Exception:
    _PROJECT_ROOT = str(Path.home())
    _WIKI_PATH = str(Path.home() / "docs")

AGENT_DIRS = [
    Path.home() / ".claude" / "agents",
    Path(_PROJECT_ROOT) / ".claude" / "agents",
]

# ---- Wiki index integrity (2026-07-27) ----
# An index row committed while its target page is still untracked is CORRECT when
# written and dangling from the next clone onward: the author sees a working link
# because the file is on their disk, so nothing looks wrong from where they stand.
# The population the link resolves against changed after the row was written — the
# same temporal-coverage shape as a dated reference file decaying, and the third
# recorded instance of it. See
# docs/core/brain/claude-core/unknown-treated-as-permissive-2026-07-15.md.
#
# It lives in the pulse rather than in a document on purpose: the check is cheap,
# mechanical, and has to run REPEATEDLY. A page can only teach it; a rule that must
# hold at every commit belongs where something fires at every commit.
#
# Both sides of the comparison are read from HEAD, never from the worktree. That is
# the whole correctness of the check: an uncommitted row pointing at an uncommitted
# page is legitimate work in flight, and reading either side from disk would either
# hide the real defect or invent one. HEAD-vs-HEAD is what a fresh clone sees.
WIKI_DIR = Path(_WIKI_PATH)
# Scope, stated rather than implied: per-topic index files only. `hot.md` carries
# wikilinks too and has the identical property, but it is a churning
# fast-orientation cache whose rows are routinely written ahead of the pages they
# point at, so nudging on it would fire on normal work. A frozenset, not a string,
# so widening the scope is a data change and stays visible here.
WIKI_INDEX_NAMES = frozenset({"_index.md"})
WIKI_MAX_DANGLING = 0     # any dangling row is a defect; raise only for a deliberate amnesty
WIKI_SAMPLE_COUNT = 3     # how many dangling rows to name in the nudge
WIKI_INDEX_CAP = 24       # index files inspected per scan — a bound, and it is REPORTED when hit

# ---- Read-before-work (2026-08-04) ----
# Writing to the vault has an external trigger — the operator says "write this down".
# Reading it has none: it is entirely the agent's own initiative. Measured across two
# vaults on 2026-08-04, under the one predicate that needs no heuristics and is identical
# in both harnesses (Read tool vs Write+Edit): 3.58 writes per deliberate read in one,
# 4.05 in the other. Within 13% of each other, so the asymmetry is a property of the
# mechanism rather than of either vault.
#
# The fix is NOT a prose rule saying "consult the wiki first". This repo already documents
# why: the read floor is enforced by a hook that hard-blocks, the over-delegation ceiling
# is prose, and the audit's own finding is that the one with teeth works and the one
# without does not. A rule that fires only when the agent remembers to consult it has the
# same defect as a check with no failing input.
#
# So this arrives BEFORE the decision, unprompted, naming the exact path — which is the
# property the prose lacks, and is deliberately NOT called enforcement. It cannot block:
# blocking a user's prompt would be hostile and UserPromptSubmit is the wrong place for a
# gate. What it changes is WHEN the knowledge arrives, which is the whole of the argument
# in the cloud-auth precedent: knowledge must load before the decision; enforcement only
# fires on the attempt.
# The trailing guard is (?![A-Za-z0-9]) rather than \b: `_` is a word character, so \b
# silently refused `PLAT-3113_notes`, which is how branch names and filenames spell it.
# The digit bound is 1-9: one digit is a real key (`PLAT-7`), and the earlier 1-6 bound
# did not TRUNCATE a longer number, it dropped it — every backtrack failed the
# lookahead, so `PLAT-1234567` produced no key at all. Large Jira instances reach seven.
WORKSTREAM_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,9})(?![A-Za-z0-9])")
# Prefixes that are never ticket keys. This list is an OPTIMISATION, not the filter — the
# real filter is that a key with no page produces silence, so a novel false positive costs
# one bounded scan and says nothing. Keeping it short on purpose: a long denylist would
# start suppressing real project prefixes nobody remembered to check.
#
# Matched against the prefix with TRAILING DIGITS STRIPPED, because the first version
# compared the whole group and so `SHA3-256`, `SHA2-512` and `HMAC-256` walked straight
# past a list containing `SHA`. Verified by execution, not by reading the regex.
WORKSTREAM_KEY_DENY = frozenset({
    "UTF", "SHA", "HMAC", "AES", "RSA", "ECDSA", "ISO", "RFC", "GPT", "HTTP", "IPV",
    "SSE", "TLS", "SSL", "MD", "CVE", "UTC", "ASCII", "BASE", "FIPS", "NIST", "RS", "RJ",
    "MPEG", "X", "ARM", "RTX", "GTX", "COVID", "PEP", "Q", "H", "CI", "PY", "ES", "USB",
})
WORKSTREAM_MAX_KEYS = 3    # keys acted on per prompt — a bound, and it is REPORTED when hit
WORKSTREAM_SAMPLE = 2      # pages named per key — also REPORTED when it truncates
WORKSTREAM_SCAN_BUDGET = 2.0   # seconds for the WHOLE scan, not per subprocess. The
                               # per-call HYGIENE_GIT_TIMEOUT bounds one `git show`; with a
                               # 24-file index cap that composed to a 75s worst case on the
                               # single event where latency is most visible. An aggregate
                               # deadline is the bound that actually matters.
WORKSTREAM_MAX_SCANS = 24      # backstop: scans per session. Once-per-key already bounds
                               # this by distinct keys; this catches a pathological prompt
                               # stream that the per-key rule cannot.
WIKI_READ_PATHS_CAP = 60   # vault-RELATIVE paths, not absolute. The state doc is
                           # re-serialised on every PreToolUse, so this list is re-written
                           # on every tool call — the exact growth pattern the ledger's
                           # bucket comment rejects. 60 relative paths is ~2KB, not ~16KB.

# ---- Dated claims (2026-07-27) ----
# Some statements in this repo are true on a date and false afterwards, and they
# decay SILENTLY: nothing raises, no test fails, the prose just stops being true.
# `pricing.md` was correct on 2026-06-10 and wrong the moment Opus 5 shipped. The
# session reminder's Opus:Sonnet ratio holds only while Sonnet 5's introductory
# rate is in force. That is the temporal-coverage mechanism applied to docs —
# see docs/core/brain/claude-core/unknown-treated-as-permissive-2026-07-15.md.
#
# The mitigation that works is a trigger that FIRES, not a note someone has to
# remember to read. Each row declares what expires, when, and what to re-check.
# Adding a claim is one row; that is the whole point of making it a table instead
# of hardcoding the one case that prompted it.
DATED_CLAIM_LEAD_DAYS = 14   # start nudging this far ahead, so the fix can land before it bites
DATED_CLAIMS = [
    {
        "expires": "2026-08-31",
        "what": "Sonnet 5 introductory rate ($2/$10 per MTok) — list rate $3/$15 applies from 2026-09-01",
        "recheck": "confirm Anthropic actually moved to list price, then re-check every "
                   "Opus:Sonnet ratio stated as prose: FORCE_LOAD_RULES in this file and "
                   "skills/models-router/references/main-agent-routing.md (~2.5x becomes ~1.7x). "
                   "The dated rows in skills/claude-cost-audit/references/pricing.md already "
                   "price per-turn correctly and need confirmation, not editing.",
    },
]

# ---- Plugin version drift (2026-07-31) ----
# Fourth recurrence: the repository moves ahead of the installed hook copy and
# nothing compares them, so the gap is only noticed after it has already misled
# someone. See ROADMAP.md, "Nothing compares the installed plugin version to
# the repository", for the full history.
PLUGIN_NAME = "claude-core-hooks"   # this hook's own plugin identity — a fact, not a guess
PLUGINS_DIR = Path.home() / ".claude" / "plugins"
INSTALLED_PLUGINS_FILE = PLUGINS_DIR / "installed_plugins.json"
KNOWN_MARKETPLACES_FILE = PLUGINS_DIR / "known_marketplaces.json"

READ_TOOLS = {"Bash", "Read", "Grep", "Glob"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
DISPATCH_TOOLS = {"Agent", "Task"}

AGGREGATE_THRESHOLD = 15
AGGREGATE_ESCALATION = 25
STREAK_THRESHOLD = 4
# Hard-block tier (2026-06-27 self-audit: discipline warnings ignored 82-88%).
# Only bites pathological inline-read runs; resets on any Agent/Task/Workflow
# dispatch or /clear, and is fully disabled via CC_DISCIPLINE_BLOCK=0. The warns
# above still fire first; these floors sit well past them.
STREAK_BLOCK_THRESHOLD = 10      # consecutive read-only calls → block
AGGREGATE_BLOCK_THRESHOLD = 40   # session-wide read-only count → block
TOOL_COUNT_WARN = 50
TOOL_COUNT_ESCALATE = 75
JSONL_SIZE_WARN = 3 * 1024 * 1024  # 3 MB
# Phase 2 follow-up (2026-05-07): require 2+ Read-after-Edit transitions on same
# file before the edit-loop rule fires. The original threshold of 1 produced false
# positives on legitimate "edit then verify" patterns (20 fires in one session of
# multi-pass infra refactoring, none of which were actual cache thrashing). Real
# cache-thrashing requires repeat alternation, not a single verification read.
EDIT_LOOP_THRESHOLD = 2
# Phase 2 follow-up (2026-05-07): two-tier escalation, mirroring the aggregate
# warn(15)/escalate(25) pattern. Threshold replay against the 16-session corpus
# showed no threshold cleanly separates real cache-thrashing from legitimate
# long-session edit-validate cycles (1d422172 alone fires 65× even at t=5 — it's
# session-shape, not user-action). Hard-blocking was rejected; instead the
# escalation tier raises the warning's volume on persistent loops without
# refusing legitimate work. See docs/brain/cost-discipline-hook-architecture.md.
EDIT_LOOP_ESCALATION = 5
# 2026-05-18 — Mechanical-Bash-on-Opus streak. Audit of 20 recent sessions found
# ~$4.74/week wasted on routine git/gh/fs Bash calls running on Opus, with 54%
# of those calls happening in runs of 3+ consecutive — a strong signal that
# model-switch nudges at run-of-3 capture the bulk of the waste. Threshold-8
# escalation matches edit_loop's two-tier pattern.
MECH_BASH_THRESHOLD = 3
MECH_BASH_ESCALATION = 8

FORCE_LOAD_RULES = """**Cost discipline reminder (auto-loaded each session):**

- Default session main is now **Opus 5** {{EFFORT}} at $5/$25 per MTok — same rate as Opus 4.8, so the 4.8→5 move is cost-neutral per token. Opus ≈ **2.5× Sonnet 5** per token today, ~1.7× once Sonnet 5's intro rate ends **2026-08-31** — switch to `/model sonnet` for routine/mechanical phases; reserve Opus for architecture, cross-repo synthesis, root-cause. Delegation discipline pays most on an Opus main.
- Effort: `ultrathink` in a prompt is an in-context nudge only — it does NOT raise the effort level. For a bigger reasoning budget use `/effort high|xhigh`. Hooks cannot change effort.
- After **15 inline mechanical reads** (Bash/Read/Grep/Glob) → next read goes to a Haiku scout. Hook will warn at 15.
- After **4 consecutive read-only calls** → delegate or write something concrete. Hook will warn.
- Never **Read → Edit → Read** the same file. Read once, batch edits, apply as one pass. Hook will warn.
- Every `Agent` dispatch passes explicit `model:` (haiku for scouts, sonnet for multi-file work, opus only for cross-repo synthesis).
- Jira reads (getJiraIssue / searchJiraIssuesUsingJql / transitions) → dispatch `jira-reader` (returns key+status+summary, strips description bodies/JSON). Jira WRITE-backs (transition/comment/create) echo the full issue JSON — keep only the status/key confirmation, discard the rest.
- Symbol questions (where is X defined / who calls X / what implements X) in Go/Ruby/Python/TS/Terraform → native `LSP` tool FIRST (load via ToolSearch; documentSymbol/findReferences/incomingCalls), not Grep. Skip LSP for files <1KB or declaration-dense configs.
- `/clear` at phase boundaries (50+ tool calls or 3 MB JSONL). Hook will warn.

Full rules: skills `models-router`, `delegation-discipline`. Hook: `~/.claude/hooks/cost-discipline.py`.
"""


def write_session_pid_marker(session_id):
    """Write /tmp/cc-session-by-pid-<claude_pid>.txt so Bash-tool subprocesses
    (e.g. ~/.claude/relay/relay.py) can find the calling session_id by
    walking their process tree.

    The hook is invoked directly by Claude Code, so os.getppid() returns
    the Claude Code process PID — stable for the session lifetime.

    Failure-isolated: any exception is swallowed; the hook never fails on this.
    """
    if not session_id:
        return
    try:
        claude_pid = os.getppid()
        marker = Path(f"/tmp/cc-session-by-pid-{claude_pid}.txt")
        tmp = marker.with_suffix(marker.suffix + ".tmp")
        tmp.write_text(session_id)
        tmp.replace(marker)
    except Exception:
        pass


# In-process memoization only: each hook event is a fresh process (same reason
# enumerate_repos() below gives up on caching entirely — CR-2), so this dict is
# reset to None at the start of every invocation and the 60s TTL never actually
# elapses within one event's lifetime. What it buys: several call sites within a
# single event read the same settings.json (blocks_enabled, log_fire, and the
# is_expensive_main_model callers below) — the TTL check dedupes those, it does
# not persist anything across events.
_EXPENSIVE_MODEL_CACHE = {"value": None, "name": None, "effort": None, "checked_at": 0.0}
_EXPENSIVE_MODEL_TTL = 60.0  # seconds — intra-invocation dedupe window, not cross-event

_KNOWN_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _parse_effort(settings):
    """Effort level from a settings dict → a known level, or unset|unknown.

    Deliberately raise-safe and separate from the model parse. Effort is the less
    critical of the two facts, and a malformed value must never be able to poison
    model detection — which is exactly what happened when this was inlined in
    _refresh_model_cache's try block: a non-string `effortLevel` (e.g. `3`) raised
    on .strip(), fell into the shared except, and reset value/name to
    False/"unknown", silently disarming every expensive-model gate on an Opus
    session. Same silent-disarm class as the 2026-06-10 Fable incident noted in
    _refresh_model_cache; caught in review 2026-07-26 before it shipped.

    An unrecognised level returns "unknown" rather than being echoed: Claude Code
    falls back to its own default on a value it doesn't recognise, so repeating a
    typo like "hgih" would state a level the session is not running at.
    """
    try:
        raw = str(settings.get("effortLevel") or "").strip().lower()
    except Exception:
        return "unknown"
    if not raw:
        return "unset"
    return raw if raw in _KNOWN_EFFORT_LEVELS else "unknown"


def _refresh_model_cache():
    """Read settings.json, populate cache with both bool (is-opus) and name string.
    Returns immediately if cache is fresh. Failure-isolated.
    """
    import time
    now = time.time()
    if (_EXPENSIVE_MODEL_CACHE["value"] is not None
            and now - _EXPENSIVE_MODEL_CACHE["checked_at"] < _EXPENSIVE_MODEL_TTL):
        return
    try:
        settings_path = Path.home() / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        raw = (settings.get("model") or "").lower()
        # Normalise to family name for fire log grouping.
        # Fable check FIRST: settings strings like "claude-fable-5[1m]" must not
        # fall through to verbatim/unknown — that silently disarmed the
        # expensive-model detectors when Fable became the default (2026-06-10).
        if "fable" in raw or "mythos" in raw:
            name = "fable"
        elif "opus" in raw:
            name = "opus"
        elif "sonnet" in raw:
            name = "sonnet"
        elif "haiku" in raw:
            name = "haiku"
        elif raw:
            name = raw  # custom/unknown — log verbatim
        else:
            name = "unset"
        # Fable 5 is $10/$50 MTok — 2x Opus 4.8. Both count as expensive.
        _EXPENSIVE_MODEL_CACHE["value"] = name in ("opus", "fable")
        _EXPENSIVE_MODEL_CACHE["name"] = name
        # Reasoning effort multiplies token volume the way the tier multiplies
        # price-per-token, so the session reminder quotes it. Read it rather than
        # hardcoding it: a hardcoded value goes stale the moment anyone runs
        # `/effort`, and a reminder that misstates the live config trains the
        # reader to distrust the rest of it. _parse_effort is raise-safe — see
        # the note there for why that isolation is load-bearing.
        _EXPENSIVE_MODEL_CACHE["effort"] = _parse_effort(settings)
        _EXPENSIVE_MODEL_CACHE["checked_at"] = now
    except Exception:
        # Don't cache a read failure for the full TTL — leave checked_at=0 so a
        # transiently-missing/unreadable settings.json self-heals on the next call
        # instead of pinning "unknown" (and disarming the model-tier gates) for 60s.
        _EXPENSIVE_MODEL_CACHE["value"] = False
        _EXPENSIVE_MODEL_CACHE["name"] = "unknown"
        _EXPENSIVE_MODEL_CACHE["effort"] = "unknown"
        _EXPENSIVE_MODEL_CACHE["checked_at"] = 0.0


def is_expensive_main_model():
    """True iff settings.json main model is Opus.

    Memoized within this invocation only, not across hook events — see the
    comment on _EXPENSIVE_MODEL_CACHE above for why the 60s TTL never actually
    elapses in practice.
    """
    _refresh_model_cache()
    return _EXPENSIVE_MODEL_CACHE["value"]


def get_main_model_name():
    """Normalised model-family name from settings.json: opus|sonnet|haiku|unset|unknown.
    Used to stamp fire log entries so audits can split heed-rate by model.
    """
    _refresh_model_cache()
    return _EXPENSIVE_MODEL_CACHE["name"]


def get_main_effort_level():
    """Reasoning effort from the USER settings.json `effortLevel`:
    low|medium|high|xhigh|max, or unset|unknown. Memoized with the model within
    this invocation only — see is_expensive_main_model for why "60s" never
    actually elapses across hook events.

    Reads ~/.claude/settings.json only. A project `.claude/settings.json` or
    `settings.local.json` takes precedence over it, so this is the user-level
    value, not necessarily the session's effective one — which is why the
    reminder attributes it to user settings rather than asserting it flatly.
    Implementing the full precedence chain would mean guessing Claude Code's
    merge order, and a confidently-wrong banner is worse than a scoped one.

    Only the effort is derivable this way — the model *version* is not, because
    settings carries a family alias ("opus[1m]"). The reminder's model name
    therefore stays hardcoded and needs a hand edit on a tier release.
    """
    _refresh_model_cache()
    return _EXPENSIVE_MODEL_CACHE["effort"]


def force_load_rules():
    """FORCE_LOAD_RULES with the live effort level substituted for {{EFFORT}}.

    Substituted with str.replace, not str.format: the reminder body is markdown
    and may grow literal braces, which .format would raise on.

    The phrase names its source ("per user settings") because a project-level
    settings file can override it — see get_main_effort_level. Unset/unknown
    degrades to the neutral phrase rather than emitting a level we can't stand
    behind.
    """
    effort = get_main_effort_level()
    phrase = (
        "(effort per the Claude Code default)"
        if effort in ("unset", "unknown", "")
        else f"({effort} effort per user settings)"
    )
    return FORCE_LOAD_RULES.replace("{{EFFORT}}", phrase)


# Mechanical-Bash classifier: returns True for routine commands a cheap model
# could handle without judgment. Conservative — false negatives OK (won't fire
# the warning), false positives soft (warning only). Covers the dominant
# patterns found in the 2026-05-18 audit: git read/write verbs, gh read
# subjects+actions, file-system inspection, `cd <path> && <mechanical>`.
_MECH_GIT_READ_VERBS = {
    "status", "diff", "log", "fetch", "branch", "rev-parse", "show",
    "ls-files", "ls-tree", "remote", "describe", "shortlog", "blame", "stash",
    "config",
}
_MECH_GIT_WRITE_VERBS = {"add", "commit", "push", "checkout", "pull", "restore", "tag"}
_MECH_FS_COMMANDS = {
    "pwd", "ls", "tree", "wc", "head", "tail", "cat", "echo", "which",
    "type", "whoami", "date", "find", "du", "stat", "file",
}
_MECH_GH_READ_SUBJECTS_NO_ACTION = {"api"}
_MECH_GH_READ_SUBJECTS_WITH_ACTION = {
    "pr", "run", "repo", "issue", "release", "workflow", "search",
}
_MECH_GH_READ_ACTIONS = {"view", "list", "diff", "checks", "status"}


def is_mechanical_bash(cmd):
    """Classify a Bash command string as mechanical (cheap-model-appropriate)
    or judgment-required. Used by the bash_on_opus_routine streak detector.
    """
    if not cmd:
        return False
    s = cmd.lstrip()
    # Pipes into data-interpreting tools (jq/grep/awk/sed/sort/uniq/xargs) are
    # judgment-required regardless of the first command — they're one-off
    # pipelines whose semantic content is in the filter, not the source.
    if "|" in s and any(f" {t} " in f" {s} " or f"| {t}" in s for t in
                       ("jq", "grep", "awk", "sed", "sort", "uniq", "xargs")):
        return False
    # Strip leading env-var assignments (e.g. "DD_SITE=eu gcloud ...")
    import re as _re
    while _re.match(r"^[A-Z_][A-Z0-9_]*=\S*\s+", s):
        s = s.split(maxsplit=1)[1]
    parts = s.split(maxsplit=2)
    if not parts:
        return False
    first = parts[0]
    second = parts[1] if len(parts) >= 2 else ""

    # cd <path> && <inner> — recurse on the inner command
    if first == "cd" and "&&" in s:
        after_and = s.split("&&", 1)[1].strip()
        return is_mechanical_bash(after_and)

    # git <verb>
    if first == "git":
        return second in _MECH_GIT_READ_VERBS or second in _MECH_GIT_WRITE_VERBS

    # gh <subject> [<action>]
    if first == "gh":
        third = parts[2].split(maxsplit=1)[0] if len(parts) >= 3 else ""
        if second in _MECH_GH_READ_SUBJECTS_NO_ACTION:
            # api alone is mechanical; api with -X is judgment (write)
            return "-X " not in s
        if second in _MECH_GH_READ_SUBJECTS_WITH_ACTION:
            return third in _MECH_GH_READ_ACTIONS
        return False

    # fs commands
    if first in _MECH_FS_COMMANDS:
        return True

    return False


def detect_session_mode():
    """Detect whether this Claude session is interactive CLI or agent/background-job.

    Signal: $CLAUDE_JOB_DIR is set by the Claude Code harness for background jobs.
    This is the canonical detection per feedback_auth_self_run.md and the
    architecture doc; skills should match this exact check so all layers agree.

    Returns "agent" or "interactive".
    """
    return "agent" if os.environ.get("CLAUDE_JOB_DIR") else "interactive"


def write_session_mode_marker(mode):
    """Write /tmp/cc-session-mode-by-pid-<claude_pid>.txt so Bash subprocesses
    and any skill content can detect agent vs interactive mode via a simple
    `cat /tmp/cc-session-mode-by-pid-$PPID.txt` lookup — no env-var inheritance
    required. PID-keyed so multiple parallel Claude sessions don't collide.

    Idempotent and failure-isolated.
    """
    try:
        claude_pid = os.getppid()
        marker = Path(f"/tmp/cc-session-mode-by-pid-{claude_pid}.txt")
        tmp = marker.with_suffix(marker.suffix + ".tmp")
        tmp.write_text(mode)
        tmp.replace(marker)
    except Exception:
        pass


def state_path(session_id):
    return STATE_DIR / f"cc-discipline-{session_id}.json"


def load_state(session_id):
    p = state_path(session_id)
    if not p.exists():
        return new_state(session_id)
    try:
        return json.loads(p.read_text())
    except Exception:
        return new_state(session_id)


def new_state(session_id):
    return {
        "session_id": session_id,
        "aggregate_reads": 0,    # flat, session-wide, ledger-owned — see _agent_scope
        "agent_counters": {},    # per-scope {"read_streak", "agent_reads", "warnings_fired"}
        "recent_tools": [],
        "files_edited": [],
        "files_warned_for_reread": [],
        "files_escalated_for_reread": [],
        "read_after_edit_counts": {},
        "tool_calls_total": 0,
        "warnings_fired": [],
        "dispatches_blocked_no_model": 0,
        "agent_models": {},
        "wiki_read_count": 0,
        "wiki_paths_read": [],       # read-before-work: WHICH vault pages were opened, as
                                     # vault-RELATIVE paths. The count alone cannot answer
                                     # "did you open the page for THIS ticket", which is the
                                     # only question the advisory needs. Relative, not
                                     # absolute, because this list is re-serialised on every
                                     # PreToolUse. Bounded by WIKI_READ_PATHS_CAP.
        "wiki_paths_read_evicted": False,  # read-before-work: has the cap above ever
                                     # DROPPED an entry this session. The list is read as
                                     # proof of a negative ("this page is not in it, so it
                                     # was never opened"), and once anything has been
                                     # evicted that inference is unsound. Recorded rather
                                     # than papered over with a larger cap, which would
                                     # only move the same defect further into the session.
        "workstream_keys_fired": [], # read-before-work: keys SETTLED this session — looked
                                     # for and nothing further to say. An unopened key is
                                     # deliberately absent, so it re-fires until the page is
                                     # opened rather than being burned on a message that may
                                     # not have been delivered.
        "workstream_scans": 0,       # read-before-work: git scans run, capped by
                                     # WORKSTREAM_MAX_SCANS as a backstop under once-per-key
        "mech_bash_on_opus_streak": 0,
        "repo_roots_seen": [],   # Layer 3: distinct "<group>/<repo>" roots read inline
        "rlm_active": False,     # Layer 3 suppressor: a Workflow dispatch happened (NEW-2)
        "compactions_seen": 0,   # PostCompact: count compactions to nudge toward /handoff
        "tool_result_chars": 0,  # L4: cumulative tool result chars accumulated in context
        "metered_results": 0,    # cost-ledger: count of results whose chars we summed (the
                                 # denominator that matches tool_result_chars; unlike
                                 # tool_calls_total it also counts mcp__ tools, which
                                 # PostToolUse meters but PreToolUse does not increment)
        "tool_result_chars_by_tool": {},  # cost-ledger: per-tool char breakdown (which tool floods)
        "dispatches_by_type": {},  # cost-ledger: {subagent_type: count}, sourced from
                                   # tool_input.subagent_type at the dispatch site. BY TYPE,
                                   # not a bare total — "delegated downward to what?" is the
                                   # question a single integer cannot answer.
        "result_bytes_buckets": {},  # cost-ledger: {lower_edge_str: count} log-scaled
                                     # histogram of per-result sizes; bounded so the hot-path
                                     # state doc stays O(1) (see LEDGER_BYTE_BUCKET_EDGES)
        "last_ledger_sig": None,  # cost-ledger: counters as of the last ledger write, so an
                                  # unchanged write can be skipped losslessly. Stored as a
                                  # list, not a tuple — state round-trips through JSON.
        "segment_base_tool_calls": 0,  # cost-ledger: tool_calls_total at this segment's start.
                                     # tool_calls_total must stay MONOTONIC (the router nudge
                                     # uses it as an index, and PostCompact deliberately keeps
                                     # it), so the segment stores the DELTA against this base.
                                     # Without it, summing segments would double-count every
                                     # call made before a compaction — the same running-total
                                     # -vs-delta trap that segments exist to avoid.
        "cwd": None,             # cost-ledger: working dir, captured at session start
        "started_at": None,      # cost-ledger: ISO8601, captured at session start
        "reader_violations": {},  # I4: per-family inline-read violation count (post-first-fire)
        "last_router_call": -999, # tool_calls_total index when models-router Skill last ran
        "prompts_seen": 0,        # UserPromptSubmit count; throttles the harness-hygiene pulse
    }


def scan_agent_models():
    """Return {agent_name: model} for every agent .md with both `name:` and
    `model:` in its frontmatter, across user-level and project-level agent dirs.
    Failure-isolated — bad files are skipped silently.
    """
    result = {}
    for agent_dir in AGENT_DIRS:
        try:
            if not agent_dir.exists():
                continue
            for f in agent_dir.glob("*.md"):
                try:
                    lines = f.read_text().splitlines()
                    if not lines or lines[0].strip() != "---":
                        continue
                    name = model = None
                    for line in lines[1:]:
                        s = line.strip()
                        if s == "---":
                            break
                        if s.startswith("name:"):
                            name = s.split(":", 1)[1].strip().strip('"').strip("'")
                        elif s.startswith("model:"):
                            model = s.split(":", 1)[1].strip().strip('"').strip("'")
                    if name and model:
                        result[name] = model
                except Exception:
                    pass
        except Exception:
            pass
    return result


def save_state(state):
    # ---- Published view for external consumers (the statusline HUD) ----
    #
    # `aggregate_reads` counts EVERY inline read in the session, including a
    # sub-agent's — sub-agents share their dispatcher's session_id, so their
    # calls land in the same flat counter. The HUD read that field, so
    # dispatching a scout made the displayed pressure go UP. The gauge moved
    # against the one action the discipline exists to encourage, which is worse
    # than no gauge: it argued for the opposite of the rule it was showing.
    #
    # `main_agent_reads` is the main scope's own count, and the name says whose
    # it is so nobody has to infer the scope from a bare number again.
    #
    # DERIVED here rather than incremented alongside the counters it summarises.
    # A second increment site is a second thing that has to stay in step, and
    # the reason this field exists at all is that a consumer was reading a
    # number that meant something other than it appeared to. Recomputing from
    # the authoritative counter on every write makes divergence unrepresentable
    # instead of merely unlikely.
    #
    # `aggregate_threshold` is published because the HUD had the constant
    # hardcoded in two places. A duplicated constant does not fail when it
    # drifts — it just quietly displays a ratio against a limit that is no
    # longer the limit.
    state["main_agent_reads"] = (
        ((state.get("agent_counters") or {}).get("main") or {}).get("agent_reads", 0)
    )
    state["aggregate_threshold"] = AGGREGATE_THRESHOLD

    p = state_path(state["session_id"])
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


# Cost estimate calibration (matches the L4 context-ledger message):
# ~3.5 chars/token, ~$22.5 per 1M cache-read tokens re-paid each turn.
LEDGER_CHARS_PER_TOKEN = 3.5
LEDGER_USD_PER_MTOK = 22.5

# Per-result size histogram. Bounded and log-scaled ON PURPOSE: the state doc is
# re-serialised on every tool call, so an O(calls) list of per-call sizes would be
# re-written on every subsequent call — quadratic over a session, and the ROADMAP
# already flags the segments list making that write bigger. Buckets are O(1) per
# segment and still answer the tail question ("how often does a result blow past
# 64k?"), which a mean cannot. Keys are the lower edge as a string so the JSON
# sorts numerically after int().
LEDGER_BYTE_BUCKET_EDGES = (0, 256, 1024, 4096, 16384, 65536, 262144, 1048576)

# Throttle for the PostToolUse ledger write (ROADMAP: "raises the priority of
# throttling that write from nice to do it in the same PR").
#
# It skips writes whose reported counters are UNCHANGED, not every Nth write. A
# count-based throttle (write when metered_results % 5 == 0) was implemented first and
# rejected on measurement: a session with fewer than 5 metered results then holds only
# the zeroed segment SessionStart opened, so the ledger reads ZEROS for a session that
# did work — the exact state the ROADMAP names as "indistinguishable from the bug this
# entry exists to fix". Six existing cost-ledger tests failed on it and were right to.
#
# Skipping unchanged writes is lossless by construction: if any reported counter moved,
# the write happens. What it removes is the redundant write on paths where PostToolUse
# fires without changing anything the ledger reports (dispatch branches, unmetered tools).
# Every element must be JSON-NATIVE and the result a list, not a tuple. state round-trips
# through JSON, so a nested tuple comes back as a nested list and would never compare
# equal to a freshly built one — the throttle would silently never fire while still
# looking implemented. Caught by test_throttle_signature_survives_a_json_round_trip,
# which failed against the first version of this function.
def _ledger_signature(state):
    dispatches = state.get("dispatches_by_type", {}) or {}
    return [
        state.get("tool_calls_total", 0),
        state.get("metered_results", 0),
        state.get("tool_result_chars", 0),
        state.get("aggregate_reads", 0),
        json.dumps(sorted(dispatches.items())),  # exact, and a plain str
    ]


def _bytes_bucket(size):
    """Lower edge of the log-scaled bucket `size` falls in, as a str key."""
    edge = LEDGER_BYTE_BUCKET_EDGES[0]
    for e in LEDGER_BYTE_BUCKET_EDGES:
        if size >= e:
            edge = e
        else:
            break
    return str(edge)


def cost_ledger_path(session_id):
    return COST_LEDGER_DIR / f"{session_id}.json"


def read_cost_ledger(session_id):
    """The ledger as currently on disk, or {} if absent/unreadable. The ledger
    OUTLIVES /tmp state, which is the whole reason segments exist: state resets at
    SessionStart, the file does not, and the prior segments are the only surviving
    record of everything before that boundary."""
    try:
        return json.loads(cost_ledger_path(session_id).read_text())
    except Exception:
        return {}


def _segment_from_state(state):
    """This session-arc's counters as one segment. Everything here is a running
    total for the CURRENT arc only — state was zeroed at the last SessionStart."""
    chars = state.get("tool_result_chars", 0)
    by_tool_chars = state.get("tool_result_chars_by_tool", {}) or {}
    return {
        "started_at": state.get("started_at"),
        # state["tool_calls_total"] is also read elsewhere for unrelated purposes (the
        # 300k/600k context-ledger warn message, the post-compact >=60 /handoff nudge)
        # and must never be repurposed or reset for ledger bookkeeping (carried over
        # from PR #38).
        # DELTA against the segment base, not the raw counter: tool_calls_total is
        # monotonic across compaction by design, so storing it raw would make
        # _sum_segments count every pre-compaction call twice.
        "tool_calls_total": max(
            0, state.get("tool_calls_total", 0) - state.get("segment_base_tool_calls", 0)),
        "metered_results": state.get("metered_results", 0),
        "tool_result_chars": chars,
        "aggregate_reads": state.get("aggregate_reads", 0),
        "by_tool": dict(by_tool_chars),
        # Sourced from tool_input.subagent_type at the dispatch site, counted BY TYPE:
        # "did this session delegate, and downward to what?" — a bare total cannot
        # answer that. See the DISPATCH_TOOLS branch in handle_pre_tool.
        "dispatches": dict(state.get("dispatches_by_type", {}) or {}),
        "result_bytes_buckets": dict(state.get("result_bytes_buckets", {}) or {}),
    }


def _sum_segments(segments):
    """Derive every total from the stored segments ALONE. Nothing else may be a
    source: a stored total that cannot be re-derived from its own segments is a
    second source of truth and will drift from the first."""
    chars = sum(s.get("tool_result_chars", 0) for s in segments)
    tokens = int(chars / LEDGER_CHARS_PER_TOKEN)
    by_tool_chars = {}
    dispatches = {}
    buckets = {}
    for s in segments:
        for name, c in (s.get("by_tool") or {}).items():
            by_tool_chars[name] = by_tool_chars.get(name, 0) + c
        for t, n in (s.get("dispatches") or {}).items():
            dispatches[t] = dispatches.get(t, 0) + n
        for b, n in (s.get("result_bytes_buckets") or {}).items():
            buckets[b] = buckets.get(b, 0) + n
    return {
        "segment_count": len(segments),
        "tool_calls_total": sum(s.get("tool_calls_total", 0) for s in segments),
        "metered_results": sum(s.get("metered_results", 0) for s in segments),
        "tool_result_chars": chars,
        "tool_result_tokens_est": tokens,
        "cache_reread_usd_per_turn_est": round(tokens * LEDGER_USD_PER_MTOK / 1e6, 2),
        "aggregate_reads": sum(s.get("aggregate_reads", 0) for s in segments),
        "tool_result_chars_by_tool": {
            name: {"chars": c, "tokens": int(c / LEDGER_CHARS_PER_TOKEN)}
            for name, c in sorted(by_tool_chars.items(), key=lambda kv: kv[1], reverse=True)
        },
        "dispatches": dict(sorted(dispatches.items())),
        "result_bytes_buckets": {k: buckets[k] for k in sorted(buckets, key=int)},
    }


def build_cost_ledger(state, prior=None):
    """Derive the collectable per-session summary as SEGMENTS plus derived totals.
    Pure function (no I/O) so it is trivially testable — `prior` is the ledger already
    on disk, passed in rather than read here.

    The last segment is REPLACED with this arc's running totals, never added to one:
    write_cost_ledger is called on every PostToolUse with a running total, so summing
    on write would multiply (1+2+...+n) instead of accumulating. Replace-then-sum is
    what makes a repeat write idempotent.

    SCOPE DECISION (explicit, per the gauge design's per-scope requirement): totals here
    are SUMMED ACROSS SCOPES, and `dispatches` is broken out BY TYPE. Bytes are
    deliberately NOT per-scope — the metering site in handle_post_tool sees a tool result
    with no agent scope attached, so a per-scope byte figure would be fabricated rather
    than measured. Per-scope read/streak state stays where it lives, in
    state["agent_counters"], and is not re-derived here. If per-scope bytes are ever
    wanted, the scope has to be threaded to the metering site first."""
    prior = prior or {}
    segments = list(prior.get("segments") or [])
    current = _segment_from_state(state)
    if segments:
        segments[-1] = current
    else:
        segments = [current]
    return {
        "session_id": state.get("session_id"),
        "cwd": state.get("cwd"),
        "started_at": (segments[0].get("started_at")
                       if segments[0].get("started_at") else state.get("started_at")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "main_model": get_main_model_name(),
        "segments": segments,
        "totals": _sum_segments(segments),
    }


def open_cost_ledger_segment(state):
    """Append a fresh segment for a new session arc, preserving every prior one.
    Called at SessionStart, AFTER state has been reset: from here on the running
    totals in state describe only this arc, and the closed segments carry the rest.
    Empty segments are NOT suppressed — a session that started and did nothing is a
    real observation, and filtering it to keep the file tidy would be a vacuity trap."""
    try:
        session_id = state.get("session_id")
        if not session_id:
            return
        prior = read_cost_ledger(session_id)
        segments = list(prior.get("segments") or [])
        segments.append(_segment_from_state(state))
        ledger = dict(prior)
        ledger.update({
            "session_id": session_id,
            "cwd": state.get("cwd") or prior.get("cwd"),
            "started_at": prior.get("started_at") or state.get("started_at"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "main_model": get_main_model_name(),
            "segments": segments,
            "totals": _sum_segments(segments),
        })
        _atomic_write_ledger(session_id, ledger)
    except Exception:
        pass


def _atomic_write_ledger(session_id, ledger):
    COST_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    p = cost_ledger_path(session_id)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=2))
    tmp.replace(p)


def write_cost_ledger(state, force=False):
    """Best-effort: persist the per-session cost summary to the cross-session ledger dir.
    Never raises — a ledger write must not block or fail a tool call.

    Skips writes whose reported counters are unchanged since the last one (see
    _ledger_signature for why that throttle is lossless and a count-based one is not).
    force=True bypasses it for boundary writes."""
    try:
        session_id = state.get("session_id")
        if not session_id:
            return
        sig = _ledger_signature(state)
        if not force and state.get("last_ledger_sig") == sig:
            return
        _atomic_write_ledger(
            session_id, build_cost_ledger(state, read_cost_ledger(session_id)))
        state["last_ledger_sig"] = sig
    except Exception:
        pass


def emit(msg):
    """Emit a single PreToolUse warning to stdout."""
    sys.stdout.write(json.dumps({"systemMessage": msg}) + "\n")
    sys.stdout.flush()


def emit_block(reason):
    """Emit a PreToolUse block decision. Tool call is refused; Claude sees `reason` as feedback."""
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    sys.stdout.flush()


# ---- Hard-block gating + forbidden-Bash-read detection (2026-06-27 self-audit) ----
def is_subagent_call(payload):
    """True iff THIS tool-call payload was made by an Agent-tool sub-agent, not
    the session's main agent.

    A different question from detect_session_mode(), which asks "is this
    process a background job?" for its own 3 call sites — do not merge the
    two (see blocks_enabled below for why this predicate exists as a sibling,
    not a replacement).

    Signal: `agent_type` is present on sub-agent dispatch payloads and absent
    on main-agent payloads. Measured via a temporary payload-shape probe
    (2026-07-28..30, deleted alongside this fix): 513/1358 real pre-tool
    records carried it across 6 distinct agent_type values seen in the wild
    (general-purpose/diagram-verifier/gcloud-reader/gh-reader/slack-reader/
    diagram-vision-reviewer), and zero of 227 control events (user-prompt-
    submit/session-start/post-compact) ever did. `agent_id` always co-occurs
    with it — see _agent_scope, which uses that field for a different purpose.

    Absent agent_type means "not a sub-agent call", whether that's a genuine
    main-agent call or, in principle, a malformed payload — both fail toward
    the same non-exempt branch. handle_pre_tool already returns before this is
    ever consulted if session_id is missing, so a payload that reaches here
    parsed well enough to have one; there is no realistic "truly unknown" case
    left to fail differently for. Stated choice, not a silent fallthrough.
    """
    return bool(payload.get("agent_type"))


def _agent_scope(payload):
    """Which per-agent counter bucket this call's read/streak state belongs to.

    A different question from is_subagent_call: that asks "is this a sub-agent
    call at all" (for the block-tier exemption below); this asks "whose
    counter" (so a sub-agent's reads don't move its dispatcher's, and vice
    versa). A dispatched sub-agent shares its dispatcher's session_id —
    confirmed via the same probe — so the shared on-disk state file needs a
    second key to tell them apart. `agent_id` is present exactly when
    `agent_type` is, so "main" (the dispatcher's own bucket) is exactly the
    is_subagent_call()==False population.
    """
    return payload.get("agent_id") or "main"


def blocks_enabled(payload):
    """Hard blocks are gated three ways, all fail-safe toward NOT blocking:
      1. env kill-switch CC_DISCIPLINE_BLOCK=0 → advisory-only. DELIBERATELY NOT
         ADVERTISED in the block messages any more (2026-07-27): a blocked agent
         read the override out of the text that blocked it and proposed exporting
         it, because the bypass was the copyable line in a message whose remedies
         were prose. The switch still works and is documented here and in README
         for a human who wants it — the change is who finds it, not whether it
         exists. Do not re-add it to an emit_block string;
      2. Agent-tool sub-agent calls are exempt — reader/scout sub-agents are
         SUPPOSED to read in bulk, and the hook can't tell a scout's reads from a
         main-agent inline streak, so it errs toward not blocking them (blocking a
         scout would break the very delegation pattern this hook encourages).
         Uses is_subagent_call(payload), NOT detect_session_mode(): the latter
         keys on $CLAUDE_JOB_DIR (background-job — a DIFFERENT population) and
         so exempted background MAINS instead of the foreground sub-agents this
         was written for. Measured and fixed 2026-07-30 — background mains are
         no longer exempt as of this change; see the PR for the blast-radius
         note (one measured session made ~2,150 calls under the old exemption);
      3. a cheap haiku main is warn-only.
    Note this intentionally INCLUDES sonnet (unlike is_expensive_main_model, which
    is opus/fable-only): the audit's worst inline-read offenders were sonnet relay
    children, and the block targets delegation waste, which applies to sonnet too.
    The audit found warnings ignored 82-88%; this tier gives the worst offenders
    teeth while staying instantly reversible."""
    if os.environ.get("CC_DISCIPLINE_BLOCK", "1") == "0":
        return False
    if is_subagent_call(payload):
        return False
    return get_main_model_name() != "haiku"


_CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".tf", ".tfvars",
             ".yaml", ".yml", ".json", ".md", ".sh", ".bash", ".zsh", ".toml",
             ".cfg", ".conf", ".ini", ".txt", ".sql", ".html", ".css", ".scss",
             ".xml", ".env", ".lock", ".gradle", ".properties", ".rs", ".java", ".kt")


def is_cat_as_read(cmd):
    """True for a bare `cat <source-file>` that should be the Read tool. False for
    transform pipelines (cat x | grep), heredocs (cat <<EOF — a write), redirects,
    command/variable substitution, and throwaway /tmp status files we ourselves cat."""
    if not cmd:
        return False
    if any(tok in cmd for tok in ("|", "<<", ">", "$(", "`", "$", "&&", ";")):
        return False
    m = re.match(r"^cat\s+(?:-\S+\s+)*(.+)$", cmd)
    if not m:
        return False
    args = m.group(1).strip()
    if args.startswith(("/tmp/", "/private/tmp/", "/proc/", "/dev/", "/var/", "/sys/")):
        return False
    low = args.lower()
    if "/mama/" in args or low.endswith(_CODE_EXT):
        return True
    # bare relative filename (cat Makefile / cat foo.go) — no slash, not an option
    if "/" not in args and not args.startswith("-"):
        return True
    return False


def is_ls_find_as_glob(cmd):
    """True for ls/find used as a Glob/Grep substitute (piped to head/tail/etc, or
    recursive ls). Advisory only — higher false-positive rate than the cat case."""
    if not cmd:
        return False
    if re.match(r"^ls\b.*\|\s*(head|tail|wc|cat|grep)\b", cmd):
        return True
    if re.match(r"^find\b.*\|\s*(xargs\s+cat|head|tail|grep)\b", cmd):
        return True
    if re.match(r"^ls\s+-\S*R", cmd):
        return True
    return False


_BASH_SEGMENT_SPLIT_RE = re.compile(r"&&|;|\|")

_ENV_PREFIX = r"(?:\w+=\S+\s+)*"
_GIT_GLOBAL_OPTS = r"(?:-C\s+\S+\s+|-c\s+\S+\s+)*"

_BASH_WRITE_PATTERNS = (
    # git: mutating subcommands — log/diff/status/show/blame are reads, stay
    # uncaught. Allows -C/-c global options and env=val prefixes ahead of the
    # subcommand, and requires a real stash/branch argument so `stash list`/
    # `stash show` and a bare `branch` (listing) stay reads.
    #
    # The index/tree/ref group was missing until 2026-08-04, and `add` was the
    # instance that showed it: `cd <repo> && git add -- <paths>` classified as a
    # READ and, on a session already at the aggregate threshold, the hook blocked
    # the staging step of a commit. Measured by running this function against the
    # refused command string, not inferred from the block message.
    #
    # It survived #50's own review for an instructive reason: that PR's write table
    # contains "git add CLAUDE.md && git commit -m x", which passes on the `commit`
    # segment. A case that exercises the verb you care about only incidentally reads
    # as coverage and is none — so each verb below is asserted on its own.
    #
    # Scoped by the PROPERTY (mutates repository state), not by the one instance
    # that was caught. Dry-run forms (`git add -n`, `git apply --check`) are
    # deliberately NOT carved out: over-classifying a read as a write only means it
    # stops counting toward the read cap, while under-classifying a write produces a
    # false block on a call the agent must make. The costly direction is the one
    # this commit removes.
    re.compile(r"^" + _ENV_PREFIX + r"git\s+" + _GIT_GLOBAL_OPTS +
               r"(commit|push|merge|rebase|reset|cherry-pick|revert|tag|"
               r"add|rm|mv|apply|restore|switch|checkout|fetch|pull|clone|init|"
               r"stash\b(?!\s+(?:list|show)\b)|"
               r"worktree\s+(add|remove|prune)|"
               r"branch\s+(?:-[dD]\b|(?!-)\S+)|"
               r"clean)\b"),
    # gh: create/merge/close/comment/edit/reopen mutate — view/diff/list/status
    # stay reads. `gh api`/`gh workflow run` deliberately excluded: arbitrary
    # API/workflow dispatch is a different, unbounded population, not one of
    # the reported CLIs.
    re.compile(r"^" + _ENV_PREFIX + r"gh\s+(pr|issue|release)\s+"
               r"(create|merge|close|comment|edit|reopen)\b"),
    # downbeat relay: send/reply/ack/register/rebind mutate shared state —
    # inbox/peers/whoami stay reads
    re.compile(r"^" + _ENV_PREFIX + r"downbeat\s+(send|reply|ack|register|rebind|"
               r"quarantine|drain|reconcile|gc-stale|gc-markers|migrate)\b"),
    # claude plugin: install/update/uninstall mutate the local install
    re.compile(r"^" + _ENV_PREFIX + r"claude\s+plugin\s+(install|update|uninstall)\b"),
)


def is_bash_write_command(cmd):
    """True for a Bash command matching a known git/gh/downbeat/claude-plugin
    mutating verb, rather than a read. Scope is the four tool families with a
    REPORTED miscount instance — `git worktree remove`, `gh pr create`,
    `downbeat reply`, `claude plugin update` (2026-08-03, two sessions, one day) —
    extend the pattern list for another CLI if it starts happening there too.

    The verb is matched at the START OF EACH SEGMENT, not just at cmd[0]: splits
    on `&&`/`;`/`|` first, so `cd /x && git commit`, `git -C /x commit`, and
    `GIT_AUTHOR_NAME=y git commit` all count — a leading `cd` or `git add` in an
    earlier segment no longer shields a write verb in a later one. First cut only
    matched cmd[0] and missed all three of those (10/24 misses on independent
    review of #50, 0 false positives on the same 24-command table) — the fix is
    on the axis the miss was actually on (position), not a longer CLI list
    (name); the four CLIs were already covered and still missed.

    Defaults to False for anything unrecognized, same as before this function
    existed — the streak's job is catching Bash(cat)/Bash(ls) read substitutes, and
    a permissive "assume write" default would give that up. This only carves known
    writes OUT of the read count; it never adds to it."""
    if not cmd:
        return False
    for segment in _BASH_SEGMENT_SPLIT_RE.split(cmd):
        segment = segment.strip()
        if segment and any(p.match(segment) for p in _BASH_WRITE_PATTERNS):
            return True
    return False


# ---- Reader-reflex table (kubectl / pup / slack / gh) ----------------------------
# The four inline-read nudges share one shape: STOP-nudge on first hit, then count
# repeats and escalate at the 3rd. Texts are verbatim from the pre-refactor blocks;
# add a reader by adding an entry here + one detection branch in handle_pre_tool.
# Each value is (first_msg, escalate_msg). The dict key is the family — it keys both
# the warning ("<family>_use_reader") and the reader_violations counter.
READER_REFLEX = {
    "kubectl": (
        "🛑 STOP — dispatch kubectl-reader instead of running kubectl inline. "
        "kubectl get/describe/logs/top belongs in the kubectl-reader Haiku sub-agent "
        "(~15× cheaper on Opus; summarizes by design). "
        "Inline kubectl is correct ONLY for: `kubectl config use-context`, "
        "`kubectl config current-context`, `aws eks update-kubeconfig`, or a single "
        "credential-refresh step before handing off to the sub-agent. "
        "Everything else → dispatch NOW: "
        "`Agent(subagent_type='kubectl-reader', model='haiku', "
        "prompt='RAW: kubectl --context=<ctx> -n <ns> <verb> ...')`. "
        "See your project's infra/k8s skill.",
        "🚨 Third inline kubectl read this session on an expensive main. "
        "Reader dispatch is ~15× cheaper; at current context size each "
        "also re-bills every following turn. "
        "Agent(subagent_type='kubectl-reader', model='haiku', ...)",
    ),
    "pup": (
        "🛑 STOP — dispatch datadog-reader instead of running pup-ro.sh inline. "
        "Datadog queries belong in the datadog-reader Haiku sub-agent "
        "(~15× cheaper on Opus; summarizes by design). "
        "Inline pup-ro.sh is correct ONLY for: a single verification query that "
        "will not be followed by more pup calls this session. "
        "Anything requiring 2+ pup calls → dispatch NOW: "
        "`Agent(subagent_type='datadog-reader', model='haiku', "
        "prompt='RAW: <your-datadog-read-cli> <verb> ...')`. "
        "See your Datadog skill.",
        "🚨 Third inline pup-ro.sh read this session on an expensive main. "
        "Reader dispatch is ~15× cheaper; at current context size each "
        "also re-bills every following turn. "
        "Agent(subagent_type='datadog-reader', model='haiku', ...)",
    ),
    "slack": (
        "🛑 STOP — dispatch slack-reader instead of running slack-cli.sh inline. "
        "Slack reads belong in the slack-reader Haiku sub-agent "
        "(~15× cheaper on Opus; summarizes by design). "
        "Inline slack-cli.sh is correct ONLY for: write subcommands "
        "(send/dm/react/unreact/mark/update/delete) — the sub-agent refuses those, "
        "they must stay inline. "
        "For history/replies/search/channels/users/unreads → dispatch NOW: "
        "`Agent(subagent_type='slack-reader', model='haiku', "
        "prompt='RAW: <your-slack-read-cli> <read-subcommand> ...')`. "
        "See your Slack skill.",
        "🚨 Third inline slack-cli.sh read this session on an expensive main. "
        "Reader dispatch is ~15× cheaper; at current context size each "
        "also re-bills every following turn. "
        "Agent(subagent_type='slack-reader', model='haiku', ...)",
    ),
    "gh": (
        "🛑 STOP — dispatch gh-reader instead of running gh inline. "
        "gh reads (pr/run/repo/issue/release/workflow view/list/diff/checks) belong "
        "in the gh-reader Haiku sub-agent (~15× cheaper on Opus; summarizes by design). "
        "Inline gh is correct ONLY for: pr create/merge/edit/comment/close, "
        "repo create/clone/delete, issue create/close, release create/edit/delete, "
        "auth login, api -X POST/PATCH/PUT/DELETE — the sub-agent refuses all of these. "
        "For any read operation → dispatch NOW: "
        "`Agent(subagent_type='gh-reader', model='haiku', "
        "prompt='Inspect PR #N in OWNER/REPO: title, state, last 5 comments, CI status')`. "
        "Auth-error diagnostic (before dispatching auth-refresh): "
        "your gh-auth diagnostic runbook.",
        "🚨 Third inline gh read this session on an expensive main. "
        "Reader dispatch is ~15× cheaper; at current context size each "
        "also re-bills every following turn. "
        "Agent(subagent_type='gh-reader', model='haiku', ...)",
    ),
}


def fire_reader_reflex(state, family):
    """Shared kubectl/pup/slack/gh reader-reflex (two-tier, like edit_loop): a
    STOP-nudge on the first inline read of that family, then count repeats and
    escalate at the 3rd. `family` keys both the warning (`<family>_use_reader`) and
    the reader_violations counter. Behaviour-identical to the four blocks it
    replaced — `_wr` is read BEFORE fire_once so the first hit only nudges and the
    escalation counts from the second hit onward."""
    first_msg, escalate_msg = READER_REFLEX[family]
    key = f"{family}_use_reader"
    already_fired = key in state["warnings_fired"]
    fire_once(state, key, first_msg)
    if already_fired:
        rv = state.setdefault("reader_violations", {})
        rv[family] = rv.get(family, 0) + 1
        if rv[family] >= 3:
            fire_once(state, f"{family}_use_reader_escalate", escalate_msg)


def log_fire(rule, session_id, action, **details):
    """Append one JSONL entry to the global fire log.
    `action` ∈ {"warn","block"}. `rule` is the dedupe key (e.g. "aggregate_15", "edit_loop").
    Failure-isolated — never raises into the hook flow.

    Auto-stamps `model` (opus/sonnet/haiku/unset) from settings.json so audits can
    split heed-rate by model without re-reading session JSONLs. Added 2026-05-18
    after session b1ad74b9 (Sonnet) and 726bece1 (Opus) showed different
    skill-invocation patterns that the existing log couldn't distinguish.
    """
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": session_id or "unknown",
            "model": get_main_model_name(),
            "rule": rule,
            "action": action,
            **details,
        }
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # never fail the hook on logging


def emit_session_context(rules):
    """Emit SessionStart additionalContext, prepended with the detected session
    mode so every loaded skill can branch on `interactive` vs `agent` without
    running its own detection. The mode signal is the same one written to
    /tmp/cc-session-mode-by-pid-<claude_pid>.txt for Bash-subprocess access.
    """
    mode = detect_session_mode()
    mode_banner = (
        f"**Session mode: `{mode}`** (interactive CLI vs agent/background-job).\n"
        f"- Bash subprocesses can confirm via: `cat /tmp/cc-session-mode-by-pid-$PPID.txt`\n"
        f"- Skills should branch on this when behavior differs in the two modes "
        f"(e.g. gcloud's `open` call is dropped in agent mode → use manual-`open` "
        f"fallback; gh's macOS Keychain access may fail in agent mode). "
        f"See your infra/gcloud + gh-auth skill references for the canonical workarounds.\n\n"
    )
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": mode_banner + rules,
        }
    }) + "\n")
    sys.stdout.flush()


def fire_once(state, key, msg):
    if key in state["warnings_fired"]:
        return
    state["warnings_fired"].append(key)
    emit(msg)
    log_fire(key, state.get("session_id"), "warn",
             tool_calls_total=state.get("tool_calls_total"),
             aggregate_reads=state.get("aggregate_reads"))


def jsonl_size(session_id):
    try:
        base = Path.home() / ".claude" / "projects"
        for p in base.glob(f"*/{session_id}.jsonl"):
            return p.stat().st_size
        return 0
    except Exception:
        return 0


# ---------- C1: cross-repo investigation detection (Layer 2) ----------
MAMA_ROOT = Path.home() / "mama"
# Three FP classes observed 2026-06-04..11 (all advisory-banner noise, ~200
# tokens each): generic English/short tokens that collide with prose ("app" ×2,
# "src", "docker", "group", ...), our own skill-dev dirs named in tooling prompts
# ("delegation-discipline" fired 2026-06-11), and scratch/test repos nobody
# investigates. Real repos with distinctive names (e.g. api-server, web-ui, foo-lib)
# stay enumerable.
REPO_DENYLIST = {
    "docs", "scripts",
    # generic tokens — collide with ordinary prose
    "app", "src", "tests", "group", "docker", "digital", "anna", "tp",
    "cron-job",
    # our own tooling dirs — named constantly in cost/skill prompts
    "delegation-discipline", "models-router",
    # scratch/test repos
    "test-repo", "test-tf", "test.me", "andrii_test_repo",
}
CROSS_REPO_PHRASES = ("across services", "end to end", "end-to-end")
WRITE_VERBS = ("fix", "implement", "deploy", "create", "add", "remove",
               "delete", "migrate", "rename", "refactor", "update")


def enumerate_repos():
    """Live repo-name set: immediate subdirs of ~/mama/<group>/<repo>, minus
    denylist + dotdirs. No caching (CR-2): fresh process per hook event makes an
    in-process cache useless; the walk is ~25 statcalls. Fail-safe: any error →
    empty set (detection then relies on cross-repo phrases only)."""
    repos = set()
    try:
        for group in MAMA_ROOT.iterdir():
            if not group.is_dir() or group.name.startswith("."):
                continue
            for repo in group.iterdir():
                if (repo.is_dir() and not repo.name.startswith(".")
                        and repo.name not in REPO_DENYLIST):
                    repos.add(repo.name)
    except Exception:
        return set()
    return repos


def match_repos(text, repos):
    """Repo names appearing in text as hyphen-aware whole tokens (NEW-1).
    NOT naive \\b: regex \\b treats '-' as a boundary, so \\bfoo\\b would match
    inside 'foo-ui'. Here a match is rejected if the char before/after is a word
    char OR a hyphen — so 'foo' inside 'foo-ui' does not count, 'foo-ui' does."""
    found = set()
    low = text.lower()
    for name in repos:
        n = name.lower()
        start = 0
        while True:
            i = low.find(n, start)
            if i < 0:
                break
            j = i + len(n)
            before = low[i - 1] if i > 0 else ""
            after = low[j] if j < len(low) else ""
            if (not (before.isalnum() or before in {"_", "-"})
                    and not (after.isalnum() or after in {"_", "-"})):
                found.add(name)
                break
            start = i + 1
    return found


def instruction_line(prompt):
    """The user's actual ask (NEW-3): first non-blank line that is not a bare
    URL and not a '>'-quoted/pasted line. The canonized paste-first usage
    (prompt-snippets.md) puts the verb on line 2+, so prompt[0] is the wrong span."""
    for raw in prompt.splitlines():
        line = raw.strip()
        if not line or line.startswith(">"):
            continue
        if re.match(r"^<?https?://\S+>?$", line):
            continue
        return line
    return ""


def is_read_shaped(instr):
    """True unless the instruction line starts with a write verb (read-shaped gate)."""
    toks = instr.lower().lstrip("`*-# ").split()
    return (not toks) or (toks[0] not in WRITE_VERBS)


def has_cross_repo_phrase(text):
    low = text.lower()
    return any(p in low for p in CROSS_REPO_PHRASES)


def repo_root_of_path(path):
    """Return '<group>/<repo>' if path is under ~/mama/<group>/<repo>/..., else None.
    A path of exactly ~/mama/<group> (no repo segment — e.g. a path-less Grep in cwd)
    returns None: cannot attribute to one repo (NEW-3 edge case)."""
    if not path:
        return None
    try:
        mama = str(MAMA_ROOT) + "/"
        if not path.startswith(mama):
            return None
        rest = [seg for seg in path[len(mama):].split("/") if seg]
        if len(rest) >= 2:
            return f"{rest[0]}/{rest[1]}"
    except Exception:
        return None
    return None


def hygiene_scan(repo=None):
    """Measure uncommitted work in `repo` (default: the harness dir).

    Returns (count, oldest_age_days, samples) where `samples` is the oldest few
    (path, age_days) pairs, or None when the scan cannot be trusted — not a git
    repo, git missing, git failing, or git too slow. Fail-open by design: a
    hygiene nudge is never worth delaying or breaking a prompt.

    Two git flags carry the whole correctness of this function:

    `-z`   — porcelain QUOTES paths containing spaces or non-ASCII by default
             (core.quotePath), so `my old notes.txt` arrives as `"my old
             notes.txt"`, quotes included. stat() then fails on the literal
             quotes and the entry silently reads as age 0 — a stale file
             invisible to the age threshold. -z emits raw NUL-separated paths
             and never quotes. It also splits renames across two records
             (`R  new\\0orig\\0`), which the loop below consumes.

    `-uall` — porcelain defaults to -unormal, which COLLAPSES an untracked
             directory into a single entry (`?? some_dir/`). Forty abandoned
             files then count as one, and the stat hits the directory, whose
             mtime moves on every add/remove, so age reads ~0. Both thresholds
             go blind to exactly the pile this function exists to find.
             Measured on the real harness (445MB of ignored transcripts):
             -uall costs nothing — 0.017s vs 0.020s — because git skips ignored
             trees regardless of the flag.
    """
    repo = HARNESS_DIR if repo is None else Path(repo)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "-z", "-uall"],
            capture_output=True, text=True, errors="surrogateescape",
            timeout=HYGIENE_GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
    except Exception:
        return None

    now = time.time()
    entries = []
    fields = proc.stdout.split("\0")
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if len(record) < 4:
            continue  # trailing empty field, or malformed
        status, path = record[:2], record[3:]
        if status[0] in ("R", "C"):
            i += 1  # a rename/copy record is followed by its origin path — consume it
        # Deliberately NO special-case on a "D" status. Every genuinely absent
        # path (`D `, `DD`, `AD`, `MD`, `RD`) already lands in the OSError branch
        # below and reads as age 0. The only statuses a "D" test would change are
        # the unmerged ones — `UD`/`DU`, a modify/delete conflict — and those
        # LEAVE OUR VERSION IN THE WORKTREE. Zeroing them hides a real file that
        # can be arbitrarily stale, which is the exact "unknown -> fresh" failure
        # this scan exists to avoid. Let stat() decide: it knows what is on disk.
        try:
            mtime = (repo / path).stat().st_mtime
            age = max(0, int((now - mtime) // 86400))
        except OSError:
            # The path is gone (a deletion) or unreadable. A deletion cannot be
            # stale, so 0 is right; -z makes an unreadable path near-impossible.
            age = 0
        entries.append((path, age))

    if not entries:
        return (0, 0, [])
    oldest = max(age for _, age in entries)
    samples = sorted(entries, key=lambda e: -e[1])[:HYGIENE_SAMPLE_COUNT]
    return (len(entries), oldest, samples)


def hygiene_context(state):
    """Advisory string when the harness repo's uncommitted pile is over a
    threshold, else None.

    Throttled by prompts_seen: fires on the first prompt of a session and every
    HYGIENE_PROMPT_INTERVAL-th prompt after. Advisory only — blocking a prompt
    over a dirty git tree would be absurd; the point is to surface finished work
    that stranded, not to gate anything.
    """
    seen = state.get("prompts_seen", 0)
    if seen < 1 or (seen - 1) % HYGIENE_PROMPT_INTERVAL != 0:
        return None
    scan = hygiene_scan()
    if scan is None:
        return None
    count, oldest, samples = scan

    reasons = []
    if count > HYGIENE_MAX_FILES:
        reasons.append(f"{count} uncommitted files (limit {HYGIENE_MAX_FILES})")
    if oldest > HYGIENE_MAX_AGE_DAYS:
        reasons.append(f"oldest dirty {oldest}d (limit {HYGIENE_MAX_AGE_DAYS}d)")
    if not reasons:
        return None

    listed = ", ".join(f"{p} ({a}d)" for p, a in samples)
    return (
        f"**Harness hygiene** — `~/.claude` has {' and '.join(reasons)}. This repo is "
        f"edited from many sessions and rarely exits cleanly, so finished work strands "
        f"here. Commit what is done, grouped by concern, staging only related paths — "
        f"never `git add -A`, since the pile may hold another session's work. "
        f"Oldest: {listed}."
    )


_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _wikilinks_in(text):
    """Wikilink targets in `text`, ignoring markup that only LOOKS like a link.

    Returns (targets, unterminated_fence). `targets` is normalised: alias and
    heading fragments stripped, external URLs dropped.

    Why this is not a `[[` grep: a naive text scan counts prose. The same
    validator this pattern came from counted `<rect` occurrences that were all
    inside HTML comments, skipped its own check as a result, and a verification
    grep written later in the same session repeated the trap with `<script`.
    An index file documenting the link convention will contain `[[example]]`
    inside a code fence, and nudging about it trains the reader to ignore the
    nudge. So: comments stripped, fenced blocks skipped, inline spans stripped.

    An unterminated fence is REPORTED, not swallowed. Skipping the rest of the
    file would under-report — the permissive direction — and silence from a
    check that stopped reading is the failure this whole check exists to avoid.
    """
    text = _HTML_COMMENT_RE.sub("", text)
    targets, fence, marker = [], False, ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(marker):
                fence, marker = False, ""
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence, marker = True, stripped[:3]
            continue
        for raw in _WIKILINK_RE.findall(_INLINE_CODE_RE.sub("", line)):
            target = raw.split("|", 1)[0].split("#", 1)[0].strip()
            if not target or "://" in target:
                continue
            targets.append(target)
    return targets, fence


def wiki_index_scan(repo=None):
    """Committed index rows whose target page is not committed.

    Returns (dangling, links, indexes, truncated, unparsed) on a real
    measurement, or None when the check COULD NOT LOOK — path absent, not a git
    repo, git missing/failing/too slow, or no index file present at HEAD.

    None and "zero dangling" are different answers and the caller must keep them
    apart. A `wiki_path` pointing somewhere without index files is the empty-set
    vacuity case: every per-row test passes because there are no rows, and
    reporting that as clean is a check that never looked reporting success.
    """
    repo = WIKI_DIR if repo is None else Path(repo)
    if not repo.is_dir():
        return None

    def git(*args):
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True, text=True, errors="surrogateescape",
                timeout=HYGIENE_GIT_TIMEOUT,
            )
        except Exception:
            return None
        return proc.stdout if proc.returncode == 0 else None

    # Tracked-at-HEAD, not `ls-files`: the staging area can hold a page that is
    # added but not yet committed, and a row pointing at one of those is exactly
    # the defect. Both sides of the comparison must come from the same tree.
    listing = git("ls-tree", "-r", "HEAD", "--name-only", "-z")
    if listing is None:
        return None
    tracked = {p for p in listing.split("\0") if p}
    if not tracked:
        return None

    stems = {p[:-3].rsplit("/", 1)[-1] for p in tracked if p.endswith(".md")}
    index_paths = sorted(p for p in tracked if p.rsplit("/", 1)[-1] in WIKI_INDEX_NAMES)
    if not index_paths:
        return None

    truncated = max(0, len(index_paths) - WIKI_INDEX_CAP)
    dangling, links, unparsed = [], 0, []
    for path in index_paths[:WIKI_INDEX_CAP]:
        content = git("show", f"HEAD:{path}")
        if content is None:
            unparsed.append(path)
            continue
        targets, open_fence = _wikilinks_in(content)
        if open_fence:
            unparsed.append(path)
        for target in targets:
            links += 1
            if "/" in target:
                if target in tracked or f"{target}.md" in tracked:
                    continue
            elif target in stems:
                continue
            dangling.append((path, target))
    return (dangling, links, len(index_paths) - truncated, truncated, unparsed)


def wiki_index_context(state):
    """(advisory_or_None, outcome) for the wiki index-integrity check.

    `outcome` is one of "skipped" / "clean" / "dangling" and is logged even when
    no advisory is emitted. That is the point: an advisory-only check that stays
    silent on both "nothing wrong" and "could not run" is indistinguishable from
    one that was never wired up, and the fire log is the only place the
    difference can be observed after the fact.

    Shares the pulse's throttle — first prompt of a session, then every
    HYGIENE_PROMPT_INTERVAL-th. Advisory only; a dangling row never blocks work.
    """
    seen = state.get("prompts_seen", 0)
    if seen < 1 or (seen - 1) % HYGIENE_PROMPT_INTERVAL != 0:
        return (None, None)
    scan = wiki_index_scan()
    if scan is None:
        return (None, "skipped")
    dangling, links, indexes, truncated, unparsed = scan
    if len(dangling) <= WIKI_MAX_DANGLING:
        return (None, "clean")

    listed = ", ".join(f"[[{t}]] (in {p})" for p, t in dangling[:WIKI_SAMPLE_COUNT])
    caveats = []
    if truncated:
        caveats.append(f"{truncated} further index files not inspected (cap {WIKI_INDEX_CAP})")
    if unparsed:
        caveats.append(f"{len(unparsed)} not fully scanned ({', '.join(unparsed)})")
    tail = f" Incomplete coverage: {'; '.join(caveats)}." if caveats else ""
    return (
        f"**Wiki index integrity** — {len(dangling)} committed index row(s) point at a page "
        f"that is NOT committed ({links} links across {indexes} index files checked). The link "
        f"works on this machine because the file is on disk, and is dangling in every clone. "
        f"Either commit the page or drop the row. Sample: {listed}.{tail}",
        "dangling",
    )


def _vault_relative(path):
    """An absolute Read path → its vault-relative form, or None if it is not in the vault.

    Two prefixes, not one. `_WIKI_PATH` is the canonical clone, but this repo mounts the
    same vault at `docs/core/` — a symlink on this machine — so the identical page is
    reachable under two absolute paths. Recording only the first meant a Read through the
    mount was invisible, and the advisory would then assert "this session has not opened
    it" about a page the session had just opened. That is a false statement in the one
    sentence the check exists to make, not merely a missed suppression.
    """
    if not path:
        return None
    if (_WIKI_PATH + "/") in path:
        return path.split(_WIKI_PATH + "/", 1)[1]
    if "/docs/core/" in path:
        return path.split("/docs/core/", 1)[1]
    return None


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
        # Strip trailing digits before the denylist lookup: the prefix of `SHA3-256` is
        # `SHA3`, and comparing the whole group let every decorated variant of a denied
        # prefix through.
        if m.group(1).rstrip("0123456789") in WORKSTREAM_KEY_DENY:
            continue
        key = m.group(0)
        if key in seen or key in excluded:
            continue
        seen.add(key)
        # Bounded DURING accumulation, not after. The first version appended to a list and
        # sliced at the end, so membership was a linear scan over an unbounded list — a
        # pasted CI log with thousands of distinct keys made this quadratic on the
        # prompt-submit hot path, before any git call. Count the remainder instead.
        if len(order) < WORKSTREAM_MAX_KEYS:
            order.append(key)
    return order, max(0, len(seen) - len(order))


def workstream_page_scan(keys, repo=None):
    """{"hits": {...}, "truncated_indexes": n, "ambiguous_keys": {...}}, or None.

    `ambiguous_keys` is a set of the keys (from `keys`) whose resolution hit a bare
    wikilink stem that names more than one committed page (see `stems` below). This is
    NOT the same population as a dangling row: a row pointing at a page that does not
    exist has a definite answer — no page — and belongs to `truncated_indexes`'s sibling,
    silence. An ambiguous stem has pages, plural, and no way to say which one; folding it
    into "no hits" would let the caller assert "no page exists" about a key the vault
    actually documents, and folding it into `truncated_indexes` would say "an index file
    was not inspected" about a file that was read in full. Neither statement is true, so
    it gets its own count.

    `hits` is NOT truncated here. The caller filters out pages this session already
    opened, and sampling before that filter let two already-read pages consume both
    slots and hide the unread one — the check silenced by the pages the agent happened
    to open first.

    None means we COULD NOT LOOK; an empty `hits` means we looked and the vault holds
    nothing for these keys. The index bound is RETURNED rather than applied silently,
    matching WIKI_INDEX_CAP's own comment in this file — a truncated result that reports
    nothing is indistinguishable from "there is no page".

    None and an empty `hits` are different answers and the caller must keep them apart:
    empty means the vault was searched and holds nothing, None means no search happened —
    path absent, not a git repo, git missing or too slow, no commits. Reporting the second
    as the first is a check that never looked reporting a result.

    Two matchers, because pages are named by topic far more often than by ticket:
      1. the key appears in a tracked filename;
      2. the key appears on a row of a committed `_index.md`, whose wikilink target is
         then the page. This is the higher-recall half and it is why the index convention
         earns its keep.

    Read from HEAD rather than from the worktree, for the same reason wiki_index_scan is:
    a page that exists only on this machine is not a page a fresh clone can open, and the
    advisory would be pointing at something that is not there for anyone else.
    """
    repo = WIKI_DIR if repo is None else Path(repo)
    if not keys or not repo.is_dir():
        return None

    deadline = time.monotonic() + WORKSTREAM_SCAN_BUDGET

    def git(*args):
        if time.monotonic() >= deadline:
            return None
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True, text=True, errors="surrogateescape",
                timeout=max(0.1, min(HYGIENE_GIT_TIMEOUT, deadline - time.monotonic())),
            )
        except Exception:
            return None
        return proc.stdout if proc.returncode == 0 else None

    listing = git("ls-tree", "-r", "HEAD", "--name-only", "-z")
    if listing is None:
        return None
    tracked = {p for p in listing.split("\0") if p.endswith(".md")}
    # NO early return on an empty `tracked`. A vault that is a git repo, has commits, and
    # whose `ls-tree` succeeded has been SEARCHED — it simply holds no markdown. Returning
    # the could-not-look sentinel for it merged the two states this function exists to keep
    # apart, and the fall-through below produces exactly the right answer with no special
    # case: no index files, no rows, `hits` empty, `truncated_indexes` zero.
    #
    # Harmless while `scan is None` settled unconditionally. The transient/permanent split
    # made it cost something: an install whose vault legitimately holds no markdown stopped
    # settling and now re-runs `git ls-tree` on every ticket-shaped prompt until the scan
    # budget is spent — 24 subprocesses a session where there had been one. Found by a
    # reviewer driving three vaults that differed only in their contents, which is the
    # comparison that makes it visible; either vault alone looks correct.
    # Bare-stem targets (`[[hot]]`) resolve through stems, exactly as wiki_index_scan does.
    # The first version emitted `hot.md` as a repo-root path, which resolves to nothing for
    # any page not literally at the vault root.
    #
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

    index_paths = sorted(p for p in tracked if p.rsplit("/", 1)[-1] in WIKI_INDEX_NAMES)
    index_rows, unread = [], 0
    # Split by CAUSE, because the three have three different remedies and the sum has
    # none. `truncated_indexes` reports "N index file(s) not inspected", which reads as
    # the cap — so a reader who raises WIKI_INDEX_CAP in response to a spent scan budget
    # or a malformed fence changes nothing and has no way to find that out. The sum is
    # kept as the gate (every consumer of it is asking the one question "was the
    # enumeration whole", for which the total is exactly right); the breakdown rides
    # alongside it for whoever has to act.
    cause_unreadable, cause_unterminated = 0, 0
    for path in index_paths[:WIKI_INDEX_CAP]:
        content = git("show", f"HEAD:{path}")
        if content is None:
            unread += 1
            cause_unreadable += 1
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
            cause_unterminated += 1
    cause_over_cap = max(0, len(index_paths) - WIKI_INDEX_CAP)
    truncated_indexes = cause_over_cap + unread
    truncated_index_causes = {
        k: v for k, v in (
            ("index_over_cap", cause_over_cap),
            ("index_unreadable", cause_unreadable),
            ("index_unterminated_fence", cause_unterminated),
        ) if v
    }

    # Populated by `_resolve` below, keyed by the RAW (un-normalized) target string, so
    # the per-key loop can ask "was this exact call ambiguous?" without re-deriving the
    # stem lookup. `_resolve`'s return value stays plain None either way — "not found"
    # and "found more than one" are the same answer to a caller that only wants a path —
    # this is the side channel that lets the scan tell them apart.
    ambiguous_targets = set()

    def _resolve(target):
        """A wikilink target → the tracked path it names, or None if it names nothing —
        either because no tracked page matches, or because the bare stem matches more
        than one (`stems[stem] is None`, set above). Both are None here; `ambiguous_targets`
        records which raw targets hit the second case, for the per-key loop below.

        This is the check the first version skipped entirely, and skipping it meant a
        committed index row pointing at an UNCOMMITTED page — the precise defect
        wiki_index_scan exists to detect, on the same tree — was advertised as a page to
        open. Reproduced: `QQQ-42` returned `brain/ghost-page.md`, which is not in HEAD.
        """
        raw = target
        target = target.split("|", 1)[0].split("#", 1)[0].strip()
        if not target or "://" in target:
            return None
        if "/" in target:
            if target in tracked:
                return target
            if f"{target}.md" in tracked:
                return f"{target}.md"
            return None
        resolved = stems.get(target)
        if resolved is None and target in stems:
            ambiguous_targets.add(raw)
        return resolved

    hits = {}
    ambiguous_keys = set()
    lowered = [(p, p.lower()) for p in tracked]
    for key in keys:
        kl = re.escape(key.lower())
        # Bounded on both sides: a bare substring made `PLAT-311` match
        # `plat-3113-rtbf.md`, so a real key that is a numeric prefix of another real key
        # resolved to the wrong page and the advisory stated it as fact.
        key_re = re.compile(r"(?<![a-z0-9])" + kl + r"(?![a-z0-9])")
        found = sorted(p for p, pl in lowered if key_re.search(pl))
        # `index_rows` was parsed once per file above (fence walk, first-cell wikilink);
        # this is just the per-key filter over that pre-parsed set.
        for row_lower, target in index_rows:
            if not key_re.search(row_lower):
                continue
            resolved = _resolve(target)
            if resolved and resolved not in found:
                found.append(resolved)
            elif target in ambiguous_targets:
                # This row's wikilink names a stem that matches more than one
                # committed page. `_resolve` already answered None for it — same
                # as "not found" — but this key is NOT "no page for this key": a
                # page exists, we just cannot say which, and stating the wrong one
                # is worse than staying silent (see the `stems` comment above).
                ambiguous_keys.add(key)
        if found:
            hits[key] = found
    return {
        "hits": hits,
        "truncated_indexes": truncated_indexes,
        "truncated_index_causes": truncated_index_causes,
        "ambiguous_keys": ambiguous_keys,
    }


def workstream_page_context(state, prompt):
    """(advisory_or_None, outcome, details) for the read-before-work check.

    Fires when the prompt names a workstream that HAS a committed vault page which this
    session has not opened. Once per key per session, not once per prompt — the trigger is
    content, not a counter, so a prompt-interval throttle would either nag or miss.

    `outcome` is logged on every run that had a key to act on, including the silent
    ones (`no-page`, `no-page-partial`, `opened`, `already-advised`, `skipped`,
    `scan-budget-spent`, `unopened`, `unopened-partial`), because a check that only
    speaks when it finds something is indistinguishable from one that was never wired
    up. `unopened-partial` is `unopened` with the same advisory but an incomplete
    index scan OR an ambiguous key resolution behind it — the fire log needs to tell
    "advised, and the search was whole" apart from "advised, and the search was
    partial" (`no-page-partial` draws the same line on the no-advisory side). No new
    outcome name exists for ambiguity specifically: it is unproven in exactly the same
    sense a truncated index enumeration is, so it reuses that vocabulary rather than
    adding a parallel one. When the prompt names no key at all, nothing ran and the
    outcome is None — a third state, and calling it "clean" would be the vacuity this
    repo has a page about.

    `details` is a small dict of extra fire-log fields, `{}` when there is nothing to
    add. It exists because `no-page-partial` returns no message — the branch returns
    before the caveats are built — so without it a fire-log reader sees the outcome
    name and nothing else: it cannot tell a truncated index enumeration (remedy: raise
    WIKI_INDEX_CAP) from a colliding stem (remedy: rename a page) apart, and those are
    different fixes. `unopened-partial` does not need this: its message already reaches
    the caveats and names the cause in prose. The cause is data, not a new outcome
    string — the vocabulary above is already eight names long and the cause composes
    with several of them; a field composes, a name multiplies.

    Truncated-index and ambiguous-stem are also DIFFERENT KINDS of "partial", and that
    difference is load-bearing for what settles, not just for what gets logged. A
    truncated index enumeration is a property of the SCAN: some index files were never
    read, so EVERY key in this batch has an unproven page list, including ones that
    already have a hit — settling any of them would be a claim the scan cannot support.
    An ambiguous stem is a property of a KEY: a colliding basename affects only the
    keys whose rows actually link it. Gating settlement for the whole batch on
    `ambiguous` being merely non-empty — as an earlier version of this fix did —
    punished keys that never went near the stem: one colliding page name in a prompt
    naming three keys silently froze the other two, unrelated to any collision, for the
    rest of the session. Settlement below is filtered per key on `key in
    scan["ambiguous_keys"]`; `truncated_indexes` keeps its batch-wide gate exactly as
    it already was.
    """
    settled = state.setdefault("workstream_keys_fired", [])
    keys, truncated_keys = workstream_keys(prompt, exclude=settled)
    if not keys:
        # Either the prompt named no key at all, or every key it named is settled. Decide
        # using the SAME denylist-aware extraction as everything else in this function,
        # not a raw shape regex — a raw search matches denylisted prefixes too (AES-256,
        # SHA3-256, RFC-2119, CVE-2021-4034), which reported "already-advised" for
        # prompts that never named a real key at all.
        # `already-SETTLED`, not `already-advised`. A key is settled once it has been
        # LOOKED FOR, whatever the answer — and the common answer is `no-page`, which
        # shows the user nothing. So the old name asserted that an advisory had fired
        # for a key that, in the majority case, never produced one. Reproduced
        # 2026-08-06: two prompts naming ZZZ-999 gave `no-page` then `already-advised`,
        # with no message shown either time. Same family as the severity predicate this
        # branch already repaired — a name standing in for the property, and diverging
        # from it in the case that happens most.
        #
        # The STATE FIELD keeps its name (`workstream_keys_fired`) deliberately: it is
        # persisted on disk, and renaming it would silently discard the settled set of
        # every session whose state file predates this commit, re-firing advisories that
        # were already answered. A log label is free to change; a storage key is not.
        all_keys, _ = workstream_keys(prompt)
        return (None, "already-settled" if all_keys else None, {})
    if state.get("workstream_scans", 0) >= WORKSTREAM_MAX_SCANS:
        return (None, "scan-budget-spent", {})

    fresh = keys

    scan = workstream_page_scan(fresh)

    # `workstream_scans` is a COST budget: it bounds how much git subprocess work one
    # session can provoke, not how many keys got settled. Those used to coincide — every
    # branch below that called `_mark` also happened to be the only branches that ran a
    # scan — until the ambiguity fix added two outcomes (`no-page-partial`,
    # `unopened-partial`) that run a full scan (a `git ls-tree` plus a `git show` per
    # index file) but settle nothing. Keying the counter on settling instead of on the
    # scan meant those two outcomes did real subprocess work on EVERY matching prompt,
    # for the rest of the session, with WORKSTREAM_MAX_SCANS never actually engaging —
    # unbounded git work on the UserPromptSubmit hot path, the same shape of bug as a
    # fire-log severity keyed on an outcome name instead of on whether an advisory
    # fired. Increment exactly once per call that reaches a scan, before any branch
    # decides what to do with the result — re-loading fresh state first for the same
    # reason `_mark` below does: sub-agents share this session_id and do their own
    # unlocked load-modify-save, so writing back a pre-scan snapshot would revert
    # counters they advanced during the scan's subprocess time.
    try:
        live = load_state(state.get("session_id"))
        live["workstream_scans"] = live.get("workstream_scans", 0) + 1
        save_state(live)
    except Exception:
        pass  # the advisory/outcome below is still right; only the budget count is lost
    state["workstream_scans"] = state.get("workstream_scans", 0) + 1

    # Settling is a SEPARATE event from scanning (see above) — `_mark` now only ever
    # touches `workstream_keys_fired`.
    def _mark(settled_keys):
        try:
            live = load_state(state.get("session_id"))
            lst = live.setdefault("workstream_keys_fired", [])
            for k in settled_keys:
                if k not in lst:
                    lst.append(k)
            del lst[:-WORKSTREAM_MAX_SCANS * 2]
            save_state(live)
        except Exception:
            pass  # the advisory is still right for this prompt; only once-per-key is lost
        for k in settled_keys:
            if k not in settled:
                settled.append(k)

    def _key_caveat():
        """`truncated_keys` as a details fragment, for EVERY return that has one.

        The per-prompt key bound was computed once at the top and then reported at
        exactly one of the five return sites — the advisory branch, in prose. The other
        four built their `details` from scratch and dropped it, though the variable was
        in scope at each. So a prompt naming five keys whose first three all resolved
        `no-page` logged a clean verdict with no trace that two keys were never looked at
        — a silent truncation, which is the one thing every bound in this file is
        required not to be.
        """
        return {"truncated_keys": truncated_keys} if truncated_keys else {}

    def _partial_name(found):
        """`opened-partial` vs `no-page-partial`, decided by whether a page was FOUND.

        Both partial branches used to return `no-page-partial` unconditionally, without
        consulting `hits`. `if not unopened:` is true in two distinguishable situations —
        the scan found nothing, and the scan found a page that this session had already
        opened — and only the first is what the name says. Reproduced 2026-08-06 by
        probing the truncated branch: `hits={'PLAT-3113': ['brain/proj/PLAT-3113-...md']}`
        while the outcome read `no-page-partial`. A page existed, it was open, and the
        fire log recorded that there was no page.

        The user-visible behaviour was never wrong — no advisory fires on either branch,
        which is what the surrounding comment defends. The INSTRUMENT was wrong, and the
        fire log is the one number this repo calls trustworthy. This mirrors the
        `opened` / `no-page` split the complete path already makes, so it is the
        vocabulary's own logic rather than a new invention.
        """
        return "opened-partial" if found else "no-page-partial"

    if scan is None:
        # "Could not look" comes in two kinds and they must not settle alike.
        #
        # PERMANENT — the vault is not a usable git repo at all (the packaged default
        # points nowhere). Settle: otherwise every prompt naming anything ticket-shaped
        # writes a fire-log line forever, burying the outcomes that matter. That was the
        # whole of the original justification, and for this half it still holds.
        #
        # TRANSIENT — git was slow, the scan budget ran out, a subprocess died. Settling
        # here made ONE unlucky timeout blind the feature for that key for the rest of the
        # session, silently, on a key whose page exists and is unopened. Reproduced
        # 2026-08-06 with a zeroed budget: outcome `skipped` and the key marked, where the
        # control arm with the same key and no failure produced `unopened` WITH an advisory.
        # That is the exact failure this whole check is built to prevent, caused by its own
        # guard.
        #
        # Not settling used to mean unbounded rescanning, which is why the original code
        # settled both. It no longer does: `workstream_scans` was moved above to increment
        # on every call that reaches a scan — including this one, verified by execution —
        # so a permanently-transient failure costs at most WORKSTREAM_MAX_SCANS scans and
        # then `scan-budget-spent` takes over. The justification in the old comment had
        # outlived the condition that produced it, and nobody re-read it when the counter
        # moved.
        if not (WIKI_DIR.is_dir() and (WIKI_DIR / ".git").exists()):
            _mark(fresh)
            return (None, "skipped", {"reason": "no_vault"})
        return (None, "skipped-partial", {"reason": "scan_failed", **_key_caveat()})

    hits = scan["hits"]
    ambiguous = scan["ambiguous_keys"]
    read = state.get("wiki_paths_read") or []
    read_partial = bool(state.get("wiki_paths_read_evicted"))
    unopened, truncated_pages = {}, 0
    for key, paths in hits.items():
        # Anchored: bare endswith matched across a path boundary, so a Read of
        # `snapshot.md` credited `hot.md` as opened and silenced the advisory with no
        # trace. That is the over-crediting direction, which this check must not have.
        not_read = [p for p in paths if not any(r == p or r.endswith("/" + p) for r in read)]
        if not_read:
            # Sampled HERE, after the already-read filter, not in the scan. Sampling
            # before that filter let already-read pages consume the sample budget and
            # hide the one page that still mattered — see workstream_page_scan's
            # docstring for the reproduction.
            truncated_pages += max(0, len(not_read) - WORKSTREAM_SAMPLE)
            unopened[key] = not_read[:WORKSTREAM_SAMPLE]

    if not unopened:
        if scan["truncated_indexes"]:
            # The scan did not finish enumerating the population it would need to claim
            # "no page exists" — the higher-recall (index) half was cut short by
            # WIKI_INDEX_CAP, not merely thin. This is BATCH-level, not per-key: a key
            # that already has a hit (and so isn't in `unopened`) may still have
            # index-only pages the truncated half never read, so its page list is
            # unproven too — "opened" is exactly as unsupported a claim as "no-page"
            # here. One rule, no per-key carve-out for keys with a hit. Note
            # `truncated_keys` does NOT feed the GATE: it says other keys in the prompt
            # went unchecked, not that the vault enumeration for THESE keys was
            # partial, and treating it as incomplete-too would mean a prompt naming
            # several keys never settles any of them — the next prompt re-selects the
            # same keys and the scan budget burns down without ever reaching a verdict.
            # It is still REPORTED in details, because a bound that bit must be visible.
            return (
                None,
                _partial_name(hits),
                {"truncated_indexes": scan["truncated_indexes"],
                 **scan["truncated_index_causes"], **_key_caveat()},
            )
        if ambiguous:
            # Ambiguity is a KEY property, not a batch one (see the docstring) — settle
            # every fresh key EXCEPT the ones whose own resolution collided. A key that
            # stays ambiguous keeps re-scanning on every prompt that names it, bounded
            # by WORKSTREAM_MAX_SCANS (genuinely bounded since round 1 fixed the
            # counter), until it stops colliding or the vault disambiguates it.
            clean = [k for k in fresh if k not in ambiguous]
            if clean:
                _mark(clean)
            return (
                None,
                _partial_name(hits),
                {"ambiguous_keys": sorted(ambiguous), **_key_caveat()},
            )
        # Settled: nothing more to say about these keys this session.
        _mark(fresh)
        return (None, "opened" if hits else "no-page", _key_caveat())

    # An UNOPENED key is deliberately NOT settled. `additionalContext` has been measured
    # being dropped mid-session while every stamp advanced perfectly, and marking here
    # would burn the key on a message that may never arrive — losing precisely the first
    # prompt about that workstream, which is the one this check exists for. It re-fires
    # until the page is actually opened, which also silences it.
    if scan["truncated_indexes"]:
        # Same reasoning as the no-advisory branch above, extended to the keys that DID
        # settle there before this fix: a truncated index enumeration means the page
        # list for those keys is unproven too, so `_mark` must not run for them either.
        # The advisory below still fires — a partial scan that found something real is
        # more useful than silence — but the outcome is tagged so a fire-log reader can
        # tell "advised, and the search was whole" from "advised, and the search was
        # partial". Still batch-wide: this is the SCAN being incomplete, not a per-key
        # property.
        outcome = "unopened-partial"
    else:
        # Ambiguity, unlike truncation, filters PER KEY: a key whose own resolution
        # collided (`k in ambiguous`) stays unsettled — its page list may be missing the
        # page the collision hid — but a batch-mate that never touched the colliding
        # stem settles normally. `k not in unopened` is unchanged: an unopened key is
        # never settled regardless of ambiguity, per the comment above.
        _mark([k for k in fresh if k not in unopened and k not in ambiguous])
        # `read_partial` qualifies the advisory but does NOT block settling, and the
        # asymmetry is the point: eviction can only ever produce a FALSE UNOPENED (a
        # path that was read is missing from the record), never a false opened (a path
        # present in the record was genuinely read). So the keys being settled here —
        # the ones NOT in `unopened` — rest on a positive membership test that eviction
        # cannot corrupt. Only the negative inference needs the caveat.
        outcome = "unopened-partial" if (ambiguous or read_partial) else "unopened"

    listed = "; ".join(
        f"**{key}** → {', '.join(paths)}" for key, paths in sorted(unopened.items())
    )
    caveats = []
    if truncated_keys:
        caveats.append(f"{truncated_keys} further key(s) in this prompt not checked")
    if scan["truncated_indexes"]:
        caveats.append(f"{scan['truncated_indexes']} index file(s) not inspected")
    if ambiguous:
        caveats.append(
            f"{len(ambiguous)} key(s) named a page whose basename is claimed by more "
            f"than one committed page"
        )
    if truncated_pages:
        caveats.append(f"{truncated_pages} further page(s) not listed")
    if read_partial:
        caveats.append(
            f"this session has opened more than {WIKI_READ_PATHS_CAP} vault pages, so the "
            f"record of what was opened is partial — a page named here may in fact have "
            f"been read earlier in the session"
        )
    tail = f" Incomplete coverage: {'; '.join(caveats)}." if caveats else ""
    return (
        f"**Read before work** — this prompt names a workstream the vault already has a page "
        f"for, and this session has not opened it: {listed}. Open it before deciding anything; "
        f"it holds the prior state of exactly this work.{tail}\n\n"
        f"Not a gate — nothing is blocked. It arrives now because the decision it informs "
        f"happens before anyone thinks to consult a wiki.",
        outcome,
        {},  # the message above already carries the cause in prose; see the docstring
    )


def _claim_status(claim, today):
    """('expired'|'due'|'current'|'malformed', days) for one dated claim.

    A malformed or missing `expires` returns 'malformed' rather than being
    skipped. Skipping is the tempting default and it is unknown -> permissive: a
    typo in a date silently retires the trigger, and the row still LOOKS like
    coverage while covering nothing.
    """
    try:
        expires = datetime.fromisoformat(str(claim.get("expires"))).date()
    except Exception:
        return ("malformed", None)
    days = (expires - today).days
    if days < 0:
        return ("expired", -days)
    if days <= DATED_CLAIM_LEAD_DAYS:
        return ("due", days)
    return ("current", days)


def dated_claims_context(state, today=None):
    """(advisory_or_None, outcome) for claims at or near their expiry date.

    Outcomes: 'expired' / 'due' / 'malformed' / 'current'. Shares the pulse's
    throttle. Advisory only — a stale doc is not worth blocking a prompt over.

    `today` is injectable so the tests can stand on both sides of a boundary
    without touching the clock. A dated check that can only be tested on the day
    it fires is a check nobody verifies.
    """
    seen = state.get("prompts_seen", 0)
    if seen < 1 or (seen - 1) % HYGIENE_PROMPT_INTERVAL != 0:
        return (None, None)
    today = today or datetime.now(timezone.utc).date()

    expired, due, malformed = [], [], []
    for claim in DATED_CLAIMS:
        status, days = _claim_status(claim, today)
        if status == "expired":
            expired.append((claim, days))
        elif status == "due":
            due.append((claim, days))
        elif status == "malformed":
            malformed.append(claim)

    if not (expired or due or malformed):
        return (None, "current")

    lines = []
    for claim, days in expired:
        lines.append(f"- **EXPIRED {days}d ago** — {claim['what']}. Re-check: {claim['recheck']}")
    for claim, days in due:
        lines.append(f"- expires in {days}d — {claim['what']}. Re-check: {claim['recheck']}")
    for claim in malformed:
        lines.append(f"- **unparseable expiry** `{claim.get('expires')!r}` on "
                     f"{claim.get('what', '<no description>')} — this row is not "
                     f"protecting anything; fix the date.")
    outcome = "expired" if expired else ("malformed" if malformed else "due")
    return (
        "**Dated claim re-validation** — a statement in this repo is at or past its "
        "expiry. Nothing has failed; dated prose decays without raising, which is why "
        "this fires on a clock instead of waiting to be noticed.\n" + "\n".join(lines),
        outcome,
    )


def _resolve_installed_plugin_record(plugin_name=PLUGIN_NAME):
    """(key, record) for the installed_plugins.json entry matching `plugin_name`,
    or None if the manifest is absent, unparsable, or has no matching key.

    The on-disk schema is `{"version": 2, "plugins": {"<name>@<marketplace>":
    [<record>, ...]}}` — a top-level `plugins` wrapper around the "name@market"
    keys, not those keys at the top level. Verified against the real file on
    this machine, not assumed from a description of it.
    """
    try:
        data = json.loads(INSTALLED_PLUGINS_FILE.read_text())
        plugins = data["plugins"]
    except Exception:
        return None
    for key, entries in plugins.items():
        if key.split("@", 1)[0] == plugin_name and entries:
            return key, entries[0]
    return None


def _plugin_json_names(manifest_path, plugin_name):
    """True iff `manifest_path` parses as JSON and its "name" matches
    `plugin_name`. False on any read/parse failure or a mismatch — never
    raises, never guesses."""
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception:
        return False
    return manifest.get("name") == plugin_name


def _resolve_repo_path(key, cwd):
    """Locate the git checkout the installed plugin `key` ("name@marketplace")
    was built from.

    Primary: known_marketplaces.json's directory-source entry for the
    marketplace. This authoritatively answers WHERE the marketplace lives —
    it is the very record `claude plugin update` reads for a `-local`
    checkout — but a marketplace can host more than one plugin, so the
    directory it names is not automatically the plugin being asked about.
    "Authoritative" describes the source of the location, not whether the
    thing found there is the thing being looked for; that still needs the
    same name-match verification as the fallback below. This path is global:
    it runs the same regardless of the session's cwd.

    Fallback: walk up from `cwd` looking for `.claude-plugin/plugin.json`
    whose "name" matches the plugin. `cwd` is genuine session data (the
    hook's own payload). Coverage on this path is limited to sessions whose
    cwd sits inside the repo; that gap belongs to the fallback only — the
    registry path above has none.

    Both paths reject a found manifest that names a different plugin rather
    than accepting it just because something was there. Returns None — never
    a guessed path — if neither locates a verified match.
    """
    plugin_name = key.split("@", 1)[0]
    marketplace = key.split("@", 1)[1] if "@" in key else None
    if marketplace:
        try:
            registry = json.loads(KNOWN_MARKETPLACES_FILE.read_text())
            source = (registry.get(marketplace) or {}).get("source") or {}
            if source.get("source") == "directory" and source.get("path"):
                candidate = Path(source["path"])
                manifest_path = candidate / ".claude-plugin" / "plugin.json"
                if manifest_path.is_file() and _plugin_json_names(manifest_path, plugin_name):
                    return candidate
        except Exception:
            pass  # no usable registry entry — fall through to the walk-up

    if cwd:
        for parent in [Path(cwd), *Path(cwd).parents]:
            manifest_path = parent / ".claude-plugin" / "plugin.json"
            if manifest_path.is_file() and _plugin_json_names(manifest_path, plugin_name):
                return parent
    return None


def _git_head_sha(repo_path):
    try:
        out = subprocess.run(["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=HYGIENE_GIT_TIMEOUT)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _commits_behind(repo_path, old_sha, new_sha):
    """Count of commits old_sha..new_sha if old_sha is an ancestor of new_sha,
    else None — 'diverged', not a fabricated distance."""
    try:
        anc = subprocess.run(
            ["git", "-C", str(repo_path), "merge-base", "--is-ancestor", old_sha, new_sha],
            capture_output=True, timeout=HYGIENE_GIT_TIMEOUT)
        if anc.returncode != 0:
            return None
        cnt = subprocess.run(
            ["git", "-C", str(repo_path), "rev-list", "--count", f"{old_sha}..{new_sha}"],
            capture_output=True, text=True, timeout=HYGIENE_GIT_TIMEOUT)
        if cnt.returncode == 0:
            return int(cnt.stdout.strip())
    except Exception:
        pass
    return None


def plugin_version_drift_context(state):
    """(advisory_or_None, outcome) comparing the installed claude-core-hooks copy
    against the repository it was built from.

    Outcomes: 'in_sync' / 'drifted' / one of four 'skipped:<reason>' values
    ('not_installed', 'repo_unresolved', 'manifest_unreadable',
    'missing_version') — a flat 'skipped' cannot tell "not installed under
    this name" apart from "found the repo but its manifest won't parse", and
    the remedies for each differ completely. Shares the pulse's throttle.
    Advisory only — drift never blocks work.

    THREE states exist, not two: the repository, the on-disk installed copy,
    and the copy the currently-running process is serving. This check compares
    only the first two — `claude plugin update` reports "restart required to
    apply", so a fresh install can sit on disk before any running session has
    it loaded, and this check cannot see that. The nudge names which two states
    it checked and stays silent about the third rather than implying it knows
    what is executing.

    Primary signal is `gitCommitSha` — a claim about the exact revision,
    immune to an unreleased checkout sharing a version string with a stale one
    (the exact evidence class a past reading of this file got wrong; see the
    ROADMAP entry). Falls back to version-vs-version when the sha is
    unavailable on either side; the nudge states which signal it used.

    Repo location: known_marketplaces.json's directory-source entry for the
    installed marketplace — global, no cwd dependency. Falls back to a cwd
    walk-up verified by plugin-name match, whose coverage is only sessions
    working inside the repo (documented on that fallback alone; the registry
    path above has no such gap).

    Self-referential limit, stated rather than solved: this check ships inside
    the very plugin it audits, so it cannot report on its own freshness until a
    release ships and an install happens — an "in sync" read from a stale
    installed copy of the check itself is the same failure shape it exists to
    catch.
    """
    seen = state.get("prompts_seen", 0)
    if seen < 1 or (seen - 1) % HYGIENE_PROMPT_INTERVAL != 0:
        return (None, None)

    found = _resolve_installed_plugin_record()
    if found is None:
        return (None, "skipped:not_installed")
    key, record = found

    repo_path = _resolve_repo_path(key, state.get("cwd"))
    if repo_path is None:
        return (None, "skipped:repo_unresolved")

    try:
        manifest = json.loads((repo_path / ".claude-plugin" / "plugin.json").read_text())
    except Exception:
        return (None, "skipped:manifest_unreadable")

    repo_version = manifest.get("version")
    installed_version = record.get("version")
    if not repo_version or not installed_version:
        return (None, "skipped:missing_version")

    installed_sha = record.get("gitCommitSha")
    repo_sha = _git_head_sha(repo_path) if installed_sha else None

    if installed_sha and repo_sha:
        signal = "commit"
        if installed_sha == repo_sha:
            return (None, "in_sync")
        distance = _commits_behind(repo_path, installed_sha, repo_sha)
        detail = (f" ({distance} commit(s) behind)" if distance is not None
                  else " (diverged — installed commit is not an ancestor of repo HEAD)")
    else:
        signal = "version"
        if repo_version == installed_version:
            return (None, "in_sync")
        detail = ""

    return (
        f"**Plugin version drift** — the installed `{PLUGIN_NAME}` copy "
        f"(v{installed_version}) is behind the repository (v{repo_version}){detail}. "
        f"Compared via {signal}. Two states checked: repository vs. on-disk "
        f"installed copy — NOT the copy the currently-running process is "
        f"serving (`claude plugin update` needs a restart to apply). Run "
        # The MARKETPLACE-QUALIFIED key, not PLUGIN_NAME. The bare name is
        # rejected — `claude plugin update claude-core-hooks` exits with
        # `Plugin "claude-core-hooks" not found`, while
        # `claude plugin update claude-core-hooks@claude-core-local` succeeds
        # (measured 2026-08-03, 0.11.7 -> 0.12.1). `key` already carries the
        # `name@marketplace` form from the install record, so this needs no
        # extra lookup. The prose above deliberately keeps the bare name: it
        # reads better, and it is not something anyone pastes.
        f"`claude plugin update {key}` and restart the session.",
        "drifted",
    )


def rlm_fanout_context(prompt):
    """Layer 2: detect cross-repo-strict investigation prompts.

    Returns (context_string, repos) or None. Extracted from the handler so
    UserPromptSubmit can carry more than one advisory in a single payload.
    """
    if not prompt.strip():
        return None
    if not is_read_shaped(instruction_line(prompt)):
        return None  # write task → silent
    repos = sorted(match_repos(prompt, enumerate_repos()))
    if not (len(repos) >= 2 or has_cross_repo_phrase(prompt)):
        return None  # not cross-repo → silent
    if repos:
        ctx = (f"Cross-repo investigation detected (repos: {', '.join(repos)}). "
               f"Default to running the `rlm-fanout` workflow with `args.scopes` set to "
               f"those repos (wiki-gate first, ~150k cap). Open your response with "
               f"'Running rlm-fanout — Esc to stop' and proceed unless the user objects. "
               f"Do NOT hand-roll inline cross-repo reads.")
    else:
        ctx = ("Cross-repo investigation detected (topology phrase). Default to running "
               "the `rlm-fanout` workflow (no scopes — it uses its curated default plus the "
               "boundary-free call-chain scout; wiki-gate first, ~150k cap). Open with "
               "'Running rlm-fanout — Esc to stop' and proceed unless the user objects. "
               "Do NOT hand-roll inline cross-repo reads.")
    return ctx, repos


def handle_user_prompt_submit(payload):
    """UserPromptSubmit carries two independent advisories, merged into a single
    additionalContext because the hook may only emit one JSON payload:

      1. harness-hygiene pulse — throttled; fires on prompt 1 of a session and
         every HYGIENE_PROMPT_INTERVAL-th after.
      2. wiki index integrity — same throttle; committed index rows pointing at
         uncommitted pages. Logs its outcome even when it emits nothing, so
         "ran and found nothing" stays distinguishable from "never ran".
      3. dated-claim re-validation — same throttle; statements in this repo that
         are true until a date and decay silently after it.
      4. plugin version drift — same throttle; the installed claude-core-hooks
         copy vs. the repository it was built from.
      5. read-before-work — NOT throttled by prompt count. Fires when the prompt
         names a workstream key that has a committed vault page this session has
         not opened, once per key. The trigger is content rather than a counter,
         because a prompt-interval throttle would either nag or miss the one
         prompt that mattered — the first one about that workstream.
      6. Layer 2 cross-repo rlm-fanout suggestion.

    This handler now writes state (the hygiene pulse needs a prompt counter),
    which the Layer-2 path previously did not. Fail-open throughout.
    """
    session_id = payload.get("session_id")
    prompt = payload.get("prompt") or ""
    parts = []

    state = None
    if session_id:
        try:
            state = load_state(session_id)
            state["prompts_seen"] = state.get("prompts_seen", 0) + 1
            save_state(state)
        except Exception:
            state = None  # no counter, no throttle — both advisories stand down

    # One try PER advisory, deliberately. Sharing a handler between two of them is
    # the shape of the 2026-07-26 silent-disarm (a cheap sibling's exception reached
    # a shared `except` and reset a load-bearing result): a fault in the hygiene
    # scan would otherwise take the wiki check offline with no trace anywhere.
    if state is not None:
        try:
            hyg = hygiene_context(state)
            if hyg:
                parts.append(hyg)
                log_fire("harness_hygiene", session_id, "warn")
        except Exception:
            pass  # hygiene must never break the prompt
        try:
            wiki, outcome = wiki_index_context(state)
            if wiki:
                parts.append(wiki)
            # Logged on every non-throttled run, including the silent outcomes.
            # A check that only speaks up when it finds something cannot be told
            # apart from a check that is not running at all.
            if outcome:
                log_fire(
                    "wiki_index_integrity", session_id,
                    "warn" if outcome == "dangling" else "info",
                    outcome=outcome,
                )
        except Exception:
            pass  # ditto — an index scan is never worth a broken prompt
        try:
            dated, outcome = dated_claims_context(state)
            if dated:
                parts.append(dated)
            if outcome:
                log_fire(
                    "dated_claim", session_id,
                    "warn" if outcome in ("expired", "malformed") else "info",
                    outcome=outcome,
                )
        except Exception:
            pass  # ditto — its own handler, for the same reason as the two above
        try:
            drift, outcome = plugin_version_drift_context(state)
            if drift:
                parts.append(drift)
            if outcome:
                log_fire(
                    "plugin_version_drift", session_id,
                    "warn" if outcome == "drifted" else "info",
                    outcome=outcome,
                )
        except Exception:
            pass  # ditto — its own handler, for the same reason as the others
        try:
            ws, outcome, ws_details = workstream_page_context(state, prompt)
            if ws:
                parts.append(ws)
            if outcome:
                # Severity asks whether an advisory was SHOWN, and `ws` answers that
                # directly; an outcome name only correlates with it. Deliberately not the
                # name comparison its three siblings above use — when this check gained
                # `unopened-partial`, a fired advisory silently began logging at info,
                # and nothing failed. Do not extend this predicate for a new outcome.
                #
                # `ws_details` carries the CAUSE for outcomes whose message is None
                # (`no-page-partial`) — a truncated index count, or the specific
                # ambiguous keys — so a fire-log reader can tell "raise the cap" from
                # "rename a page" apart without a message to parse. `{}` for every other
                # outcome, so this is a no-op there.
                log_fire(
                    "workstream_page", session_id,
                    "warn" if ws else "info",
                    outcome=outcome,
                    **ws_details,
                )
        except Exception:
            pass  # ditto — a vault lookup is never worth a broken prompt

    rlm = rlm_fanout_context(prompt)
    if rlm:
        ctx, repos = rlm
        parts.append(ctx)
        log_fire("workflow_suggest_rlm", session_id, "info", repos=repos)

    if not parts:
        return
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(parts),
        }
    }) + "\n")
    sys.stdout.flush()


def handle_pre_tool(payload):
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not session_id:
        return

    # Pin session_id to Claude PID for Bash-side consumers (relay.py et al).
    # Cheap (one ~36-byte atomic write) and idempotent.
    write_session_pid_marker(session_id)
    # Also (idempotently) refresh the session-mode marker — protects against
    # SessionStart hook missing or mode changing mid-session (rare but cheap).
    write_session_mode_marker(detect_session_mode())

    state = load_state(session_id)
    state["tool_calls_total"] += 1

    # ---------- models-router recency (feeds the pre-dispatch nudge) ----------
    if tool_name == "Skill" and (tool_input.get("skill") or "").strip() == "models-router":
        state["last_router_call"] = state["tool_calls_total"]

    # ---------- Forbidden Bash reads: cat-as-Read / ls-find-as-Glob (2026-06-27 audit) ----------
    # The audit found 232 Bash(cat) + 213 Bash(ls/find) substitutes in one session —
    # a dominant driver of unchecked read streaks. cat-of-a-source-file gets teeth
    # (unambiguous, should be Read); ls/find substitutes get a nudge (fuzzier).
    if tool_name == "Bash":
        _bcmd = (tool_input.get("command") or "").strip()
        if blocks_enabled(payload) and is_cat_as_read(_bcmd):
            save_state(state)
            log_fire("block_cat_as_read", session_id, "block", command=_bcmd[:120])
            emit_block(
                "🛑 `cat <file>` to read a source file — use the Read tool instead. "
                "cat dumps the whole file into context with no line numbers and no range; "
                "Read gives you offset/limit and peek-first. Transform pipelines "
                "(cat x | grep y) and heredocs are unaffected.")
            return
        if is_ls_find_as_glob(_bcmd):
            fire_once(state, "bash_ls_find_as_glob",
                "🔍 `ls`/`find` piped to head/tail/cat is a Glob/Grep substitute. Use Glob "
                "(filename patterns) or Grep (content) — structured and cheaper than parsing "
                "shell output. CLAUDE.md context-loading protocol step 1.")

    # ---------- Wiki consumption tracking ----------
    # Increment wiki_read_count when the agent touches the project wiki tree.
    # Used by the wiki_first nudge to detect "incident investigation tool
    # invoked before any wiki Read" — the producer-vs-consumer gap flagged in
    # the 2026-05-12 retrospective. Counts any tool call referencing
    # the configured wiki path (_WIKI_PATH) — Read, Grep, Glob, or Bash (command-string match).
    # Guarded as one unit. This block runs on EVERY PreToolUse call for every tool in
    # every project — the highest-frequency path in the file — and it is the one place
    # the "one try/except per advisory" convention was not applied. The predicate it
    # replaced did strictly less (a single substring test on a string forced by `or ""`);
    # this version calls a helper and mutates a list in state. `main()` does catch
    # everything and exit 0, so the prompt is never broken — but an exception here
    # silently truncates the rest of `handle_pre_tool`, including the Layer-3 cross-repo
    # catch-net below, with nothing reaching the fire log to say so.
    try:
        _wiki_path_hit = False
        _wiki_read_target = ""
        if tool_name == "Read":
            _wiki_read_target = tool_input.get("file_path") or ""
            # Via _vault_relative, so the docs/core mount counts as the vault here too. The
            # first version tested only the canonical prefix, which meant a Read through the
            # mount was neither counted nor recorded.
            _wiki_path_hit = _vault_relative(_wiki_read_target) is not None
        elif tool_name in ("Grep", "Glob"):
            _wiki_path_hit = (_WIKI_PATH + "/") in (tool_input.get("path") or "")
        elif tool_name == "Bash":
            _wiki_path_hit = _WIKI_PATH in (tool_input.get("command") or "")
        if _wiki_path_hit:
            state["wiki_read_count"] = state.get("wiki_read_count", 0) + 1
            # Record the path only for `Read`, deliberately. A Grep or a Bash sweep across the
            # vault is not "you opened the page for this ticket" — it is the read-to-consult
            # versus read-in-order-to-edit distinction that neither of the 2026-08-04
            # measurements could make, and counting a sweep as having consulted a page would
            # let the advisory be silenced by an operation that read nothing in particular.
            # Under-crediting is the safe direction here: the cost is one extra nudge.
            if tool_name == "Read" and _wiki_read_target:
                _rel = _vault_relative(_wiki_read_target)
                if _rel:
                    _seen = state.setdefault("wiki_paths_read", [])
                    if _rel not in _seen:
                        _seen.append(_rel)
                        # The cap EVICTS, and the eviction has to be recorded, because
                        # this list is read as proof of a negative: "this path is not in
                        # it" is what makes the advisory claim the page was never opened.
                        # Once the oldest entries are dropped, that inference is unsound —
                        # and the failure is not a mislabelled log row but a FALSE
                        # ADVISORY shown to the user about a page they did open.
                        # Reproduced 2026-08-06: under the cap the outcome is `opened`
                        # and silent; over it, `unopened` with the message displayed.
                        # Raising the cap is not the fix; the same class returns one
                        # long session later. Recording that the record is partial is.
                        if len(_seen) > WIKI_READ_PATHS_CAP:
                            del _seen[:-WIKI_READ_PATHS_CAP]
                            state["wiki_paths_read_evicted"] = True
    except Exception:
        pass  # never let vault bookkeeping truncate the rest of this handler

    # ---------- Layer 3: cross-repo inline-drift catch-net (C3) ----------
    _root = None
    if tool_name == "Read":
        _root = repo_root_of_path(tool_input.get("file_path") or "")
    elif tool_name in ("Grep", "Glob"):
        _root = repo_root_of_path(tool_input.get("path") or "")
    elif tool_name == "Bash":  # best-effort: scan command for a mama/<group>/<repo> path
        _m = re.search(re.escape(str(MAMA_ROOT)) + r"/[^/\s]+/[^/\s]+",
                       tool_input.get("command") or "")
        _root = repo_root_of_path(_m.group(0)) if _m else None
    if _root and _root not in state["repo_roots_seen"]:
        state["repo_roots_seen"].append(_root)
    if tool_name == "Workflow":
        state["rlm_active"] = True   # Workflow-only suppressor (NEW-2)
    if len(set(state["repo_roots_seen"])) >= 2 and not state.get("rlm_active"):
        fire_once(state, "cross_repo_inline_drift",
            "🔍 You're reading across 2+ repos inline — this is the cross-repo investigation "
            "`rlm-fanout` is for (cheaper, summarized, budgeted). Consider dispatching it "
            "(or /rlm) instead of continuing inline.")

    # ---------- Aggregate / streak (read-only mechanical tools) ----------
    # A Bash call matching a known write verb (git push/commit, gh pr create,
    # downbeat reply, claude plugin update, ...) takes the `else` reset path below
    # instead of counting — it did real work, the same as an Edit/Write call would
    # have. See is_bash_write_command's docstring for the reported instances and why
    # the default stays False (content-blind) for anything it doesn't recognize.
    _bash_write = tool_name == "Bash" and is_bash_write_command(tool_input.get("command") or "")
    if tool_name in READ_TOOLS and not _bash_write:
        # Count PROSPECTIVELY and commit only if the call is let through. A blocked
        # call never runs, so counting it made the counter measure work that did not
        # happen — and each retry pushed it further over, which is why the block
        # felt inescapable for reasons the threshold alone did not explain (observed
        # 2026-07-27: 40 -> 41 across two refused attempts). Refusing without
        # counting holds the reported number still while blocked, so the only way
        # the number moves is a call that actually read something.
        # Scoped per-agent (2026-07-30): a dispatched sub-agent shares its
        # dispatcher's session_id, so without a second key its reads land on
        # whichever scope last wrote the shared state file. `agent_reads` here
        # is deliberately NOT `aggregate_reads` — that flat, unscoped field
        # stays session-wide for the cost ledger (build_cost_ledger reads it
        # directly), and a sub-agent's reads legitimately belong in that
        # session total even though they must NOT move its dispatcher's own
        # block-tier counter. `warnings_fired` below is scoped the same way,
        # so the once-per-threshold warn isn't silently claimed by whichever
        # scope crosses it first — but fire_once() isn't reused for these
        # three, because its `key` also names the log_fire() event type, and
        # scope-qualifying that would fragment cross-session aggregation of
        # "how often does aggregate_15 fire" into one row per agent_id.
        _scope = _agent_scope(payload)
        _scoped = state.setdefault("agent_counters", {}).setdefault(
            _scope, {"read_streak": 0, "agent_reads": 0, "warnings_fired": []})
        _aggregate = state["aggregate_reads"] + 1
        _agent_reads = _scoped["agent_reads"] + 1
        _streak = _scoped.get("read_streak", 0) + 1

        # ---- Hard-block tier (2026-06-27 self-audit) — short-circuits before the warns ----
        if blocks_enabled(payload) and (
                _streak >= STREAK_BLOCK_THRESHOLD
                or _agent_reads >= AGGREGATE_BLOCK_THRESHOLD):
            _streaky = _streak >= STREAK_BLOCK_THRESHOLD
            _n = _streak if _streaky else _agent_reads
            save_state(state)  # earlier mutations only — the counters stay deliberately unchanged
            log_fire("block_read_streak" if _streaky else "block_aggregate_reads",
                     session_id, "block", count=_n, tool_name=tool_name)
            # The remedy list is PER TIER on purpose. A write resets the streak but
            # not the session aggregate, so offering "write something concrete" to
            # an aggregate-blocked session hands out a cure that leaves it blocked —
            # and a remedy the check itself defeats is the defect this whole message
            # was rewritten for. No override is advertised here either: the bypass
            # was the copyable line in a message whose remedies were an argument,
            # and the copyable half is the half that gets obeyed.
            _remedies = (
                "Do ONE: (a) dispatch a Haiku reader/scout to take over the reading "
                "(Agent with model: haiku); (b) write/edit something concrete; or (c) "
                "/clear and re-prime from a plan file — the streak resets on any of the three."
                if _streaky else
                "Do ONE: (a) dispatch a Haiku reader/scout to take over the reading "
                "(Agent with model: haiku); or (b) /clear and re-prime from a plan file. "
                "A write does NOT reset the session aggregate — only a dispatch or /clear does."
            )
            emit_block(
                f"🛑 Read-discipline hard-block: this would be your {_n}th "
                + ("consecutive " if _streaky else "")
                + "inline read-only call in main with no delegation — the pattern the "
                "2026-06-27 audit found running unchecked (warnings ignored 82-88%). "
                + _remedies
                + " This refused call is not counted, so the number will not climb while "
                "you are blocked.")
            return

        state["aggregate_reads"] = _aggregate
        _scoped["agent_reads"] = _agent_reads
        _scoped["read_streak"] = _streak
        state["recent_tools"].append(tool_name)
        state["recent_tools"] = state["recent_tools"][-5:]

        _scoped_fired = _scoped.setdefault("warnings_fired", [])
        if _scoped["agent_reads"] == AGGREGATE_THRESHOLD and "aggregate_15" not in _scoped_fired:
            _scoped_fired.append("aggregate_15")
            emit(
                f"⚠️  Aggregate read discipline: {AGGREGATE_THRESHOLD} inline mechanical tool calls in main this session. "
                "Per delegation-discipline/references/hard-rules.md, the next reading task must be dispatched to a Haiku scout. "
                "Counter resets on /clear or successful Agent dispatch.")
            # log_fire keeps the flat aggregate_reads kwarg for continuity with
            # existing fire-log rows, but ALSO logs the scoped agent_reads/scope
            # that actually caused this fire — the flat figure is a different
            # counter as of this PR (see _agent_scope) and, for a sub-agent's
            # own warning, is the DISPATCHER's count, not the number that
            # crossed the threshold. Logging only the flat one would make the
            # fire log — the one instrument already established as trustworthy
            # — record a number that did not fire the event.
            log_fire("aggregate_15", session_id, "warn",
                     tool_calls_total=state.get("tool_calls_total"),
                     aggregate_reads=state.get("aggregate_reads"),
                     agent_reads=_scoped["agent_reads"], scope=_scope)
        elif _scoped["agent_reads"] >= AGGREGATE_ESCALATION and "aggregate_25" not in _scoped_fired:
            _scoped_fired.append("aggregate_25")
            emit(
                f"🚨 Aggregate read discipline (escalation): {_scoped['agent_reads']} mechanical calls — "
                f"{_scoped['agent_reads'] - AGGREGATE_THRESHOLD} over threshold. "
                "Stop and dispatch a Haiku scout NOW, or run /clear and re-prime from a plan file.")
            log_fire("aggregate_25", session_id, "warn",
                     tool_calls_total=state.get("tool_calls_total"),
                     aggregate_reads=state.get("aggregate_reads"),
                     agent_reads=_scoped["agent_reads"], scope=_scope)

        # Streak warn tier shares the read_streak counter with the block tier above
        # (single source of truth — no desync between the two consecutive-read measures).
        if _scoped["read_streak"] >= STREAK_THRESHOLD and "streak_4" not in _scoped_fired:
            _scoped_fired.append("streak_4")
            emit(
                f"⚠️  Streak discipline: {_scoped['read_streak']} consecutive read-only tool calls in main "
                f"({', '.join(state['recent_tools'][-STREAK_THRESHOLD:])}). "
                "Next action must either write/edit something concrete, or dispatch a Haiku agent.")
            log_fire("streak_4", session_id, "warn",
                     tool_calls_total=state.get("tool_calls_total"),
                     aggregate_reads=state.get("aggregate_reads"),
                     read_streak=_scoped["read_streak"], scope=_scope)
    else:
        # any non-read tool resets the streak window for THIS call's scope only —
        # a dispatcher's Write must not reset a sub-agent's streak, or vice versa.
        _scope = _agent_scope(payload)
        state.setdefault("agent_counters", {}).setdefault(
            _scope, {"read_streak": 0, "agent_reads": 0, "warnings_fired": []}
        )["read_streak"] = 0
        state["recent_tools"].append(tool_name)
        state["recent_tools"] = state["recent_tools"][-5:]

    # ---------- Reader-agent nudges (Bash-pattern specific) ----------
    # When the main agent emits a Bash command that a dedicated Haiku sub-agent
    # is designed to handle, fire a one-shot reminder per session. Doesn't block
    # — inline use is legitimate for one-off lookups, context-switching, and
    # credential refresh (kubectl). The nudges target multi-call inspection arcs
    # which are the sub-agents' sweet spot. Skill content can't catch this case
    # if the relevant skill wasn't loaded this session; hook-level inspection
    # fires regardless of skill state.
    #
    # Three rules, mutually exclusive by command path:
    #   kubectl_use_reader  → kubectl-reader sub-agent
    #   pup_use_reader      → datadog-reader sub-agent
    #   slack_use_reader    → slack-reader sub-agent
    if tool_name == "Bash":
        cmd_full = tool_input.get("command") or ""
        cmd_parts = cmd_full.split(maxsplit=2)
        first = cmd_parts[0] if cmd_parts else ""
        verb = cmd_parts[1] if len(cmd_parts) >= 2 else ""

        # kubectl verb extractor — handles flags-before-verb and compound commands.
        #
        # Real-world pattern:
        #   kubectl --context "arn:aws:..." -n risk-engine get pods
        # splits as first="kubectl", verb="--context" → the old check failed.
        # Also handles compound forms like:
        #   export PATH=".../gcloud-sdk/bin:$PATH"\nkubectl --context=... get pods
        # where first="export", not "kubectl".
        #
        # Algorithm: find the "kubectl" token anywhere in the command, then walk
        # forward skipping flags (both --flag value and --flag=value forms) until
        # a known read verb is found. Returns "" if no read verb found (e.g.
        # kubectl config use-context, kubectl delete, kubectl exec — all stay inline).
        _KUBECTL_READ_VERBS = frozenset({"get", "describe", "logs", "top"})

        def _kubectl_read_verb(cmd: str) -> str:
            # Normalise shell syntax so "kubectl" is always a standalone token:
            # - compound operators: && ; | (pipe) — split command chains
            # - subshell/expansion: $( ` — kubectl appears right after these
            # - assignment prefix: VAR=$(kubectl → strip $( → "VAR= kubectl"
            tokens = (cmd
                      .replace("\n", " ")
                      .replace("&&", " ").replace(";", " ").replace("|", " ")
                      .replace("$(", " ").replace("`", " ")
                      ).split()
            for i, t in enumerate(tokens):
                if t != "kubectl":
                    continue
                j = i + 1
                while j < len(tokens):
                    tok = tokens[j]
                    if tok in _KUBECTL_READ_VERBS:
                        return tok
                    if tok.startswith("-"):
                        # --flag=value: single token, skip just it
                        # --flag value or -f value: two tokens, skip both
                        if "=" not in tok:
                            j += 2
                            continue
                    j += 1
            return ""

        _kubectl_verb = _kubectl_read_verb(cmd_full)

        # gh has a 3-token nested-verb structure (e.g. "gh pr view 4344"), so
        # we reparse with maxsplit=3 to inspect both subject and action.
        # READ patterns only — writes (create/merge/edit/comment/clone/auth)
        # stay silent because we don't want to nudge on write actions and we
        # don't want wiki_first firing during legitimate `gh auth login`.
        is_gh_read = False
        if first == "gh":
            gh_parts = cmd_full.split(maxsplit=3)
            gh_subject = gh_parts[1] if len(gh_parts) >= 2 else ""
            gh_action = gh_parts[2] if len(gh_parts) >= 3 else ""
            is_gh_read = (
                gh_subject == "api"
                or (gh_subject in ("pr", "run", "repo", "issue", "release",
                                   "workflow", "search")
                    and gh_action in ("view", "list", "diff", "checks", "status"))
            )

        # Wiki-first precondition: incident-investigation tools should be
        # preceded by a wiki grep. Detect the incident-tool patterns (kubectl
        # read / pup-ro.sh / slack-cli.sh read / gh read) and fire if no wiki
        # Read has happened in this session. Self-disables once
        # wiki_read_count > 0 (any tool call touching the configured wiki path
        # increments it).
        _is_incident = (
            bool(_kubectl_verb)
            or first.endswith("/pup-ro.sh")
            or (first.endswith("/slack-cli.sh") and verb in (
                "history", "replies", "search", "channels", "users", "unreads"))
            or is_gh_read
        )
        if _is_incident and state.get("wiki_read_count", 0) == 0:
            fire_once(state, "wiki_first",
                "🔍 Wiki check missing. About to run an incident-investigation tool "
                "(kubectl/pup/slack-cli/gh read), but no wiki Read in this session yet. "
                "Grep first: `grep -rl '<service-or-symptom>' "
                f"{_WIKI_PATH}/brain/ "
                f"{_WIKI_PATH}/platform/services/` — "
                "then Read the most relevant match. Today's brain entries and "
                "per-service runbooks often short-circuit hours of debugging. "
                "Self-disables after the first wiki Read this session.")

        # Reader-reflex chain — detection stays inline here; the nudge + escalation
        # shape is table-driven (READER_REFLEX / fire_reader_reflex). Exemptions are
        # carried by the conditions themselves: _kubectl_verb is read-verbs-only
        # (config/delete/exec/apply returned "" upstream); pup-ro.sh is read-only by
        # construction; slack matches read subcommands only (writes stay inline — the
        # reader refuses them); is_gh_read excludes pr create/merge/edit, api -X POST…
        if _kubectl_verb:
            fire_reader_reflex(state, "kubectl")
        elif first.endswith("/pup-ro.sh"):
            fire_reader_reflex(state, "pup")
        elif first.endswith("/slack-cli.sh") and verb in (
                "history", "replies", "search", "channels", "users", "unreads"):
            fire_reader_reflex(state, "slack")
        elif is_gh_read:
            fire_reader_reflex(state, "gh")

    # ---------- grep_use_lsp: prefer LSP for symbol-shaped searches on code ----------
    # Added 2026-06-12: blind LSP test showed Sonnet used Read+bash-grep 0/3 when the
    # doctrine was not in the session banner. Advisory-only, fire_once, mirrors
    # kubectl_use_reader shape. Trigger on Grep tool (code paths) or Bash grep on
    # code extensions; skip for content/log/docs searches.
    _CODE_EXTS = (".go", ".rb", ".py", ".ts", ".tsx", ".tf")
    _grep_is_code = False
    if tool_name == "Grep":
        _gi = ((tool_input.get("path") or "")
               + (tool_input.get("pattern") or "")
               + (tool_input.get("glob") or ""))
        _grep_is_code = any(e in _gi for e in _CODE_EXTS)
    elif tool_name == "Bash":
        _gc = tool_input.get("command") or ""
        _grep_is_code = "grep " in _gc and any(e in _gc for e in _CODE_EXTS)
    if _grep_is_code:
        fire_once(state, "grep_use_lsp",
            "Symbol-shaped search on code? The LSP tool answers definition/references/callers "
            "as a ~100-token exact result vs this grep cascade (measured ~26x cheaper for "
            "reference tracing). Load via ToolSearch select:LSP. "
            "Grep stays right for string/log/content searches.")

    # ---------- read_peek_first: nudge BEFORE a full-file Read of a large file ----------
    # Added 2026-06-25 after 4d6d609f re-analysis: 61 full-file Reads = 258k chars, none
    # with a `limit:` param. The peek-first / code-explorer rule lived only as advisory
    # text in CLAUDE.md and went unheeded under momentum (same 50%-adoption-without-
    # structural-delivery failure as the LSP doctrine). The existing >10k tripwire fires
    # POST-tool (damage done); this fires PRE-tool at the read moment. Advisory, fire_once.
    if tool_name == "Read" and not tool_input.get("limit") and not tool_input.get("offset"):
        _fp = tool_input.get("file_path")
        try:
            _sz = os.stat(_fp).st_size if _fp else 0
        except Exception:
            _sz = 0
        if _sz > 20_000:  # ~400+ lines; the 19-26k class that flooded 4d6d609f
            fire_once(state, "read_peek_first",
                f"📖 About to FULL-read a large file (~{_sz // 1000}k). Peek first: Read with "
                "limit:50 to see structure, then read only the range you need — or delegate the "
                "read to a code-explorer/Explore sub-agent (only the summary rides back into "
                "context). Full reads of big files are what flooded 4d6d609f (61 reads = 258k, "
                "all inline). For symbol/definition/caller lookups use the LSP tool, not a full read.")

    # ---------- Mechanical-Bash-on-Opus streak detector ----------
    # Added 2026-05-18 after audit of 20 recent sessions found ~$4.74/week
    # wasted on routine git/gh/fs Bash calls running on Opus, with 54% of
    # those calls happening in runs of 3+ consecutive (strong clustering
    # signal that model-switch nudges at run-of-3 capture the bulk).
    #
    # Increments on each mechanical Bash call when settings model is Opus.
    # Resets on any non-Bash tool OR judgment-Bash. Fires fire_once at
    # threshold 3 (warn) and threshold 8 (escalate with cumulative waste
    # estimate). Both messages point at /model sonnet AND the existing
    # commit-commands slash commands as the canonical alternatives.
    if tool_name == "Bash" and is_expensive_main_model():
        if is_mechanical_bash(tool_input.get("command") or ""):
            state["mech_bash_on_opus_streak"] = state.get("mech_bash_on_opus_streak", 0) + 1
            streak = state["mech_bash_on_opus_streak"]
            if streak == MECH_BASH_THRESHOLD:
                fire_once(state, "bash_on_opus_routine",
                    f"🛑 Mechanical-Bash on Opus: {streak} consecutive routine Bash calls. "
                    "Each costs ~$0.029 on Opus vs ~$0.006 on Sonnet — 5× surcharge for work "
                    "that doesn't need Opus reasoning. Switch to `/model sonnet` NOW for this "
                    "phase. For git workflows: use the `commit-commands` plugin's `/commit` / "
                    "`/commit-push-pr` (bundles status/diff/branch as pre-loaded context in "
                    "one pass — 3-5× cheaper per commit than ad-hoc Bash). Project-specific "
                    "ticket/branch conventions belong in that project's own CLAUDE.md, not a "
                    "bespoke slash command. Staying on Opus is only justified if the next step "
                    "requires cross-repo reasoning or architecture-level synthesis.")
            elif streak == MECH_BASH_ESCALATION:
                est_waste = streak * 0.023
                fire_once(state, "bash_on_opus_routine_8",
                    f"🚨 Mechanical-Bash escalation: {streak} mechanical Bash calls on Opus. "
                    f"Estimated waste this stretch: ~${est_waste:.2f}. "
                    "Switch to `/model sonnet` NOW — every subsequent mechanical Bash on Opus "
                    "burns ~$0.023 over Sonnet rate. The right cadence: Sonnet for routine "
                    "git/inspection/CI work, then `/model opus` only when you hit cross-repo "
                    "synthesis or architecture decisions.")
        else:
            # Judgment-Bash on Opus resets the streak (the stretch is over)
            if state.get("mech_bash_on_opus_streak", 0) > 0:
                state["mech_bash_on_opus_streak"] = 0
    elif tool_name != "Bash":
        # Any non-Bash tool also resets (Edit, Read, Agent, Task, etc.)
        if state.get("mech_bash_on_opus_streak", 0) > 0:
            state["mech_bash_on_opus_streak"] = 0

    # ---------- Edit-loop detector ----------
    # Phase 2 follow-up (2026-05-07): count Read-after-Edit transitions per file
    # and fire warn/escalate at separate thresholds, mirroring aggregate's
    # warn(15)/escalate(25) pattern. A single Read after Edit is a verification
    # read; multiple alternations is the cache-thrashing population. Both tiers
    # dedupe per-file and reset on a fresh Edit so a resumed cycle re-fires.
    if tool_name == "Read":
        fp = tool_input.get("file_path")
        if fp and fp in state["files_edited"]:
            counts = state.setdefault("read_after_edit_counts", {})
            counts[fp] = counts.get(fp, 0) + 1
            # Warn tier
            if counts[fp] >= EDIT_LOOP_THRESHOLD and fp not in state["files_warned_for_reread"]:
                state["files_warned_for_reread"].append(fp)
                emit(
                    f"⚠️  Edit-loop discipline: re-read `{fp}` {counts[fp]}× after Edit/Write. "
                    "Read once, collect all pending changes, apply as a single multi-Edit pass. "
                    "Each Edit invalidates the file's cache entry; subsequent Reads pay full input price.")
                log_fire("edit_loop", state.get("session_id"), "warn",
                         file_path=fp, read_after_edit_count=counts[fp])
            # Escalation tier
            if counts[fp] >= EDIT_LOOP_ESCALATION and fp not in state.setdefault("files_escalated_for_reread", []):
                state["files_escalated_for_reread"].append(fp)
                emit(
                    f"🚨 Edit-loop discipline (escalation): re-read `{fp}` {counts[fp]}× after Edit/Write — "
                    f"{counts[fp] - EDIT_LOOP_THRESHOLD} over threshold. "
                    "Stop. Capture ALL pending edits to this file as a single MultiEdit, apply once, "
                    "and stop reading until you have a concrete reason. "
                    f"You've paid the post-Edit re-read cost on this file {counts[fp] - 1}× already.")
                log_fire("edit_loop_5", state.get("session_id"), "warn",
                         file_path=fp, read_after_edit_count=counts[fp])
    elif tool_name in EDIT_TOOLS:
        fp = tool_input.get("file_path")
        if fp and fp not in state["files_edited"]:
            state["files_edited"].append(fp)
        # New edit on this file — re-warn / re-escalate allowed if cycle resumes.
        # The count itself must reset too, not just these membership lists: the
        # two tiers below are independent ifs, not if/elif, so a fresh Edit that
        # only cleared the flags but left read_after_edit_counts intact meant a
        # resumed cycle's first Read was already at or above EDIT_LOOP_ESCALATION
        # — satisfying BOTH thresholds at once and firing warn AND escalation
        # together on that single Read, instead of "re-firing" gradually the way
        # a fresh cycle does.
        state.setdefault("read_after_edit_counts", {}).pop(fp, None)
        if fp in state["files_warned_for_reread"]:
            state["files_warned_for_reread"].remove(fp)
        if fp in state.setdefault("files_escalated_for_reread", []):
            state["files_escalated_for_reread"].remove(fp)

    # ---------- Long-doc composition on expensive model (advisory, fire_once) ----------
    # Audit 2026-06-10: 212 output events >4k tokens on Fable/Opus = top waste pattern.
    # Doc-gen delegation rule: inline Write of >16k-char .md on Fable/Opus signals
    # the main agent is composing prose rather than orchestrating — dispatch a Sonnet
    # sub-agent instead.
    if tool_name == "Write" and is_expensive_main_model():
        _ld_fp = tool_input.get("file_path", "")
        _ld_content = tool_input.get("content", "")
        if _ld_fp.endswith(".md") and len(_ld_content) > 16000:
            _ld_key = "write_long_doc_on_expensive_model"
            if _ld_key not in state["warnings_fired"]:
                state["warnings_fired"].append(_ld_key)
                _ld_chars = len(_ld_content)
                emit(
                    f"📝 Long doc (~{_ld_chars // 1000}k chars) being composed inline on an "
                    "expensive main model (fable/opus). The doc-gen delegation rule says: "
                    "dispatch a Sonnet sub-agent with this Write target and an outline — "
                    "main emits pointers, not prose. "
                    "(Audit 2026-06-10: 212 >4k-token outputs on expensive models = "
                    "top waste pattern.)"
                )
                log_fire(_ld_key, session_id, "warn",
                         file_path=_ld_fp, chars=_ld_chars)

    # ---------- Sub-agent dispatch model annotation ----------
    # Phase 1.5 (2026-04-30): promoted from warning to hard block.
    # Phase 1.7 (2026-05-06): smarter rule — allow when subagent_type has a
    # frontmatter `model:` default. Block only for general-purpose or unknown
    # agents where the harness would inherit an unbounded default. Friction
    # data (4 jira-gh blocks / 7 sessions, 0 cost-prevention value) motivated
    # the relaxation. See docs/brain/cost-discipline-hook-architecture.md.
    if tool_name in DISPATCH_TOOLS:
        # models-router-before-dispatch nudge (2026-06-27 audit: router skipped
        # before ~all Agent dispatches). Advisory, fire_once per session.
        if (state["tool_calls_total"] - state.get("last_router_call", -999)) > 20:
            fire_once(state, "router_before_agent",
                "🧭 Dispatching a sub-agent with no recent models-router check. CLAUDE.md "
                "requires models-router before dispatch — it picks haiku (scouts) / sonnet "
                "(multi-file) / opus (cross-repo synthesis). Skipping it is the top reason "
                "readers land on the wrong (expensive) tier.")
        if not tool_input.get("model"):
            subagent_type = tool_input.get("subagent_type") or ""
            # Lazy-populate the agent_models cache if SessionStart didn't run
            # (e.g., resumed session where state was wiped/missing).
            if not state.get("agent_models"):
                state["agent_models"] = scan_agent_models()
            agent_models = state.get("agent_models") or {}

            # Phase 2 follow-up (2026-05-07): cache-staleness fix. If the
            # subagent_type isn't in the cached map, the agent file may have
            # been added after this session's cache was populated. Re-scan once
            # before falling through to block. Fixes the slack-reader false
            # block on 2026-05-06 (session d2c02a5c) where slack-reader.md was
            # created mid-conversation in the parent lineage.
            if subagent_type and subagent_type not in agent_models:
                fresh = scan_agent_models()
                if subagent_type in fresh:
                    state["agent_models"] = fresh
                    agent_models = fresh

            if subagent_type in agent_models:
                # Agent has a frontmatter default — allow the dispatch, log for audit.
                save_state(state)
                log_fire("model_inferred_from_frontmatter", session_id, "info",
                         tool_name=tool_name,
                         subagent_type=subagent_type,
                         inferred_model=agent_models[subagent_type],
                         description=(tool_input.get("description") or "")[:120])
            else:
                # general-purpose or unknown — harness default is unbounded; BLOCK.
                state["dispatches_blocked_no_model"] += 1
                save_state(state)
                emit_block(
                    "Agent/Task dispatch refused: missing `model:` annotation "
                    f"and `subagent_type={subagent_type or '(none)'}` has no "
                    "frontmatter default. "
                    "Re-dispatch with `model: haiku` (scouts/summarization), "
                    "`model: sonnet` (multi-file work / Jira+GH workflows), "
                    "or `model: opus` (cross-repo synthesis only). "
                    "Reference: ~/.claude/skills/models-router/references/sub-agent-routing.md.")
                log_fire("model_annotation_block", session_id, "block",
                         tool_name=tool_name,
                         subagent_type=subagent_type,
                         description=(tool_input.get("description") or "")[:120])
                return
        # Cost-ledger dispatch counting. Reached only on an ALLOWED dispatch — the
        # block path above returns first, and a refused dispatch must not advance a
        # counter: that is the ratchet defect the read-block tier was fixed for, where
        # a counter measured work that never happened. Keyed by subagent_type because
        # the question is "delegated downward to WHAT", not "how many times".
        _d_type = tool_input.get("subagent_type") or "unknown"
        _d_by_type = state.setdefault("dispatches_by_type", {})
        _d_by_type[_d_type] = _d_by_type.get(_d_type, 0) + 1
        save_state(state)

    # ---------- Workflow governance (warn + log only; design 2026-06-02) ----------
    if tool_name == "Workflow":
        script_text = (tool_input.get("script") or "") + (tool_input.get("name") or "")
        if state.get("wiki_read_count", 0) == 0:
            fire_once(state, "workflow_no_gate",
                "🔍 Workflow invoked with no wiki Read this session. The gate phase is the "
                "cheapest cost control: grep docs/ first — if the wiki answers it, the whole "
                "workflow is unnecessary. See delegation-discipline → references/workflow-authoring.md.")
        if tool_input.get("script") and "budget" not in script_text:
            fire_once(state, "workflow_no_budget",
                "⚠️  Inline Workflow script has no budget guard. budget.total is null unless "
                "the user typed '+Nk' — an unguarded parallel() has no brake. Add: "
                "const CAP = budget.total ?? args?.budget_cap ?? 150_000 and check "
                "budget.spent() >= CAP before each fan-out. "
                "See references/workflow-authoring.md.")
        log_fire("workflow_invoked", state.get("session_id"), "info",
                 workflow_name=tool_input.get("name") or "(inline script)")

    # ---------- Session scope (count + size) ----------
    if state["tool_calls_total"] == TOOL_COUNT_WARN:
        fire_once(state, "tool_count_50",
            f"⚠️  Session scope discipline: {TOOL_COUNT_WARN} tool calls in this session. "
            "Consider /clear and re-prime from a plan file at the next phase boundary.")
    elif state["tool_calls_total"] == TOOL_COUNT_ESCALATE:
        fire_once(state, "tool_count_75",
            f"🚨 Session scope discipline (escalation): {TOOL_COUNT_ESCALATE} tool calls. "
            "Strongly recommend /clear before continuing — every subsequent turn pays for the growing prefix.")

    size = jsonl_size(session_id)
    if size > JSONL_SIZE_WARN:
        fire_once(state, "size_3mb",
            f"⚠️  Session scope discipline: JSONL size {size // 1024} KB > 3 MB. "
            "/clear at next phase boundary.")

    save_state(state)


def handle_post_tool(payload):
    """PostToolUse: L4 context ledger (all tools) + dispatch-counter reset (dispatch tools only)."""
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name", "")
    if not session_id:
        return

    # ---------- L4: context ledger — measure every tool result ----------
    try:
        size = len(json.dumps(payload.get("tool_response", "")))
    except Exception:
        size = 0

    state = load_state(session_id)
    state["tool_result_chars"] = state.get("tool_result_chars", 0) + size
    # Per-result size distribution, bucketed. The sum above gives the mean for free
    # (with metered_results); this gives the TAIL, which is what a byte-gated block
    # tier needs — the point of such a gate is catching outliers, and a mean hides them.
    _buckets = state.setdefault("result_bytes_buckets", {})
    _b = _bytes_bucket(size)
    _buckets[_b] = _buckets.get(_b, 0) + 1
    # metered_results is the denominator that actually matches tool_result_chars: it
    # counts every result we summed, including mcp__ tools (PostToolUse meters them but
    # PreToolUse never increments tool_calls_total for them).
    state["metered_results"] = state.get("metered_results", 0) + 1
    if tool_name:  # skip a nameless bucket if tool_name is ever missing
        by_tool = state.setdefault("tool_result_chars_by_tool", {})
        by_tool[tool_name] = by_tool.get(tool_name, 0) + size

    # Per-result tripwire: single oversized result (fire_once per session)
    if size > 10_000 and "tool_result_oversize" not in state["warnings_fired"]:
        state["warnings_fired"].append("tool_result_oversize")
        emit(
            f"📦 Oversized tool result (~{size // 1000}k chars) just entered context permanently. "
            "For bulk command output: pipe to a file (cmd > /tmp/out.txt) and Read targeted ranges, "
            "or dispatch a reader sub-agent. Each turn from now re-pays this result in cache reads."
        )
        log_fire("tool_result_oversize", session_id, "warn",
                 tool_name=tool_name, chars=size)

    # MCP fat response: per-server-family advisory (fire_once per family per session).
    # Calibration: oversize tripwire (10k) misses the 4-6k MCP blob class; this catches
    # it. Added 2026-06-12 after session 1fc211dd: 9 MCP calls → 38K chars, top-3
    # concentration 82.6%, all below the 10k tripwire.
    if tool_name.startswith("mcp__"):
        _parts = tool_name.split("__")
        _family = _parts[1] if len(_parts) > 2 else "unknown"
        _fat_key = f"mcp_fat_{_family}"
        if size > 3_000 and _fat_key not in state["warnings_fired"]:
            state["warnings_fired"].append(_fat_key)
            emit(
                f"📦 Fat MCP response (~{size // 1000}k chars from {_family}). "
                "These accumulate per-message noise even below the 10k tripwire. "
                "Jira: pass fields= minimal sets (see jira-gh-workflow MCP response hygiene). "
                "Reads via reader sub-agents where one exists."
            )
            log_fire("mcp_fat_response", session_id, "warn",
                     family=_family, chars=size, tool_name=tool_name)

    # Cumulative watermarks: 300k then 600k chars
    total = state["tool_result_chars"]
    model_suffix = (
        " (expensive main — drag is 2-10x Sonnet)"
        if get_main_model_name() in ("fable", "opus") else ""
    )
    _ledger_msg = (
        f"📦 Context ledger: ~{total // 3500}k tokens of tool results accumulated across "
        f"{state.get('tool_calls_total', '?')} calls. At this size every turn re-pays roughly "
        f"${total / 3.5 * 22.5 / 1e6:.2f}+ in cache reads before any new work. "
        "Tool-result clearing is the lightest-touch compaction (Anthropic): "
        "/compact now, or /handoff + /clear at the next phase boundary." + model_suffix
    )
    if total >= 300_000 and "context_ledger_300k" not in state["warnings_fired"]:
        state["warnings_fired"].append("context_ledger_300k")
        emit(_ledger_msg)
        log_fire("context_ledger_300k", session_id, "warn",
                 chars=total, tool_name=tool_name)
    if total >= 600_000 and "context_ledger_600k" not in state["warnings_fired"]:
        state["warnings_fired"].append("context_ledger_600k")
        emit(_ledger_msg)
        log_fire("context_ledger_600k", session_id, "warn",
                 chars=total, tool_name=tool_name)

    # ---------- Dispatch-counter reset (Workflow + Agent/Task only) ----------
    if tool_name == "Workflow":
        log_fire("workflow_completed", session_id, "info")
        # fall through: a workflow run is the strongest delegation signal —
        # reset aggregate/streak counters exactly like an Agent/Task dispatch
    elif tool_name not in DISPATCH_TOOLS:
        save_state(state)
        write_cost_ledger(state)
        return

    state["aggregate_reads"] = 0
    # Scoped to the DISPATCHING call's own bucket (see _agent_scope): a
    # dispatch is something the dispatcher itself does, so this resets the
    # dispatcher's own counters, not every scope's.
    _scope = _agent_scope(payload)
    _scoped = state.setdefault("agent_counters", {}).setdefault(
        _scope, {"read_streak": 0, "agent_reads": 0, "warnings_fired": []})
    _scoped["read_streak"] = 0
    _scoped["agent_reads"] = 0
    _scoped["warnings_fired"] = []  # allow the aggregate warnings to fire again in the next arc
    state["recent_tools"] = []
    save_state(state)
    write_cost_ledger(state)


def handle_session_start(payload):
    session_id = payload.get("session_id")
    # Write session-mode marker FIRST — independent of session_id so it works
    # even on edge cases where session_id is missing.
    write_session_mode_marker(detect_session_mode())
    if session_id:
        # Pin session_id to Claude PID for Bash-side consumers.
        write_session_pid_marker(session_id)
        # Wipe any stale state file for this session id
        p = state_path(session_id)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
        # Pre-populate agent_models cache so the first dispatch is fast.
        state = new_state(session_id)
        state["agent_models"] = scan_agent_models()
        state["cwd"] = payload.get("cwd") or os.getcwd()
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
    # Emit the critical rules banner FIRST; the cost-ledger seed is best-effort and
    # must never be sequenced ahead of session-start context (a future un-guarded
    # failure there could otherwise preempt the banner).
    emit_session_context(force_load_rules())
    # OPEN A NEW SEGMENT last, so a session that does no tool calls still appears in
    # the collection (with zeroed counters) — empty segments are deliberately kept.
    # This must APPEND rather than write: state was just reset above, so a plain
    # write_cost_ledger would replace the last segment with the zeroed one and destroy
    # everything before this boundary. That overwrite-on-reset IS the defect segments
    # exist to fix; the ledger file outlives /tmp state.
    if session_id:
        open_cost_ledger_segment(load_state(session_id))


def reader_roster():
    """The reader agents that EXIST, read from disk. Returns (names, enumerated).

    DERIVED, NOT RECITED, and that distinction has a nine-day price attached. The
    post-compact reminder used to carry a hand-written list of five readers. `gcloud-reader`
    was added to the fleet on 2026-07-28 — as the fix for an incident whose root cause was
    that gcloud was the ONE tool with no reader, established from the child's transcript
    rather than guessed — and the list was never told. So the remedy existed and the only
    pointer to it did not mention it, which is the same shape that incident's own ROADMAP
    entry names: a remedy that does not exist from where you are standing fails silently.
    `notion-reader` would have arrived with the same gap on 2026-08-06.

    The 2026-07-28 fix itself recorded "rosters derived rather than recited" as the principle,
    and applied it to `when-to-delegate.md` while this message kept reciting. Deriving is
    therefore not a new idea here; it is the one already written down, finally applied to the
    second half.

    Two returns, not one, because "the directory holds no readers" and "the directory could
    not be read" are different answers and the caller must not print the first when it has the
    second. A roster is a claim about a population; an unreadable directory bounds no
    population at all.

    Naming convention is the contract: a reader agent is `<tool>-reader.md`. That is what the
    six existing ones already do, so the convention is observed rather than imposed.
    """
    try:
        d = HARNESS_DIR / "agents"
        if not d.is_dir():
            return ([], False)
        return (sorted(p.stem for p in d.glob("*-reader.md")), True)
    except Exception:
        return ([], False)


def handle_post_compact(payload):
    """/clear or auto-compact: reset counters but keep tool_calls_total.

    Also injects a re-routing reminder. Investigation of sessions 726bece1 and
    b1ad74b9 (2026-05-18) found that post-compact summaries are TASK-heavy
    ("Execute Tasks 1-7", "revert config.go, force-push") which create an
    execution frame that overrides the cost-discipline reminder re-injected
    via SessionStart. The model continues prior tasks without re-invoking
    models-router or delegation-discipline. This injection interrupts that
    momentum at the moment of compaction-recovery.
    """
    session_id = payload.get("session_id")
    if not session_id:
        return
    state = load_state(session_id)
    state["aggregate_reads"] = 0
    state["agent_counters"] = {}  # whole-session reset: every scope's read/streak state
    state["recent_tools"] = []
    state["mech_bash_on_opus_streak"] = 0  # I5: don't carry a mid-streak across compaction
    state["files_edited"] = []             # I5: edit-loop tracking restarts on fresh context
    state["files_warned_for_reread"] = []
    state["warnings_fired"] = []
    state["repo_roots_seen"] = []
    state["rlm_active"] = False
    state["tool_result_chars"] = 0  # L4: compaction clears context; drag restarts from zero
    # Reset the by-tool breakdown and the metered_results denominator together with
    # tool_result_chars: all three describe the SAME window (in-context drag since the
    # last compaction), which is what the cache-reread estimate is derived from. Left
    # un-reset, by_tool became a lifetime accumulator sitting next to a since-compact
    # headline (they never reconcile), and metered_results — documented as "the
    # denominator that matches tool_result_chars" — silently stopped matching its
    # numerator after the first compaction, making avg-chars-per-result meaningless.
    state["tool_result_chars_by_tool"] = {}
    state["metered_results"] = 0
    # compactions_seen is NOT reset — it counts across the session's lifetime to
    # detect repeated auto-compaction (the signal to switch to a lossless /handoff).
    state["compactions_seen"] = state.get("compactions_seen", 0) + 1
    # A compaction is a SEGMENT BOUNDARY: /clear opens a new segment rather than
    # zeroing the derived total. Every field that _segment_from_state reads must be
    # reset here, or the closed segment and the new one both carry it and
    # _sum_segments double-counts. tool_calls_total is the exception — it stays
    # monotonic for the router nudge, so we move its per-segment BASE instead.
    state["dispatches_by_type"] = {}
    state["result_bytes_buckets"] = {}
    state["segment_base_tool_calls"] = state.get("tool_calls_total", 0)
    save_state(state)
    # Append AFTER the resets and BEFORE any further write: a plain write would
    # replace the closed segment with the freshly-zeroed one and lose the arc.
    open_cost_ledger_segment(state)

    # Emit a re-routing reminder. NOTE: PostCompact does NOT support
    # hookSpecificOutput.additionalContext (only PreToolUse / UserPromptSubmit /
    # PostToolUse / PostToolBatch do). Emitting it there fails Hook JSON
    # validation and the checkpoint silently never lands. PostCompact's only
    # model-visible channel is the top-level `systemMessage` — use emit().
    # (Fixed 2026-06-03 after observing the validation error in a live compact.)
    # Derived from disk so the roster cannot drift from the fleet. The names ARE the mapping
    # — `gcloud-reader` for gcloud, `gh-reader` for gh — which is why no per-reader hint list
    # is kept here: a hint map would be a second hand-maintained list with the same failure.
    _readers, _enumerated = reader_roster()
    if not _enumerated:
        _reflex = (
            "the reader roster could NOT be enumerated (`~/.claude/agents` unreadable). This "
            "is not a claim that no readers exist — check before reading inline."
        )
    elif not _readers:
        _reflex = (
            "no reader agents are installed. Read-only queries have no delegate here, so say "
            "so rather than reading inline unnoticed — that gap is what the 2026-07-27 "
            "inline-gcloud incident was."
        )
    else:
        _reflex = (
            "for any read-only query, dispatch the reader whose name matches the tool "
            "(Haiku). Installed: " + ", ".join(f"`{n}`" for n in _readers) + ". Derived from "
            "`~/.claude/agents/` at compaction time rather than listed here, so it cannot "
            "fall behind the fleet. WRITES are never delegated."
        )
    reminder = (
        "**Post-compact checkpoint — cost discipline re-routing required.**\n\n"
        "The conversation summary above describes prior tasks in an execution frame "
        "(*\"continue\"*, *\"execute Tasks 1-7\"*, *\"force-push\"*). That framing "
        "competes with the cost-discipline reminder. Before resuming substantive work:\n\n"
        "1. **Invoke `models-router` skill** to re-decide the main-agent model for "
        "the remaining work. Prior model choice is summarized, not live.\n"
        "2. **Invoke `delegation-discipline` skill** before the next bulk read or "
        "sub-agent dispatch. Prior dispatch decisions don't carry over.\n"
        f"3. **Reader-agent reflex**: {_reflex}\n"
        "4. **Git workflows**: default to the `commit-commands` plugin's `/commit` / "
        "`/commit-push-pr` — cheaper than ad-hoc Bash chains. Project-specific ticket/PR "
        "conventions live in that project's own CLAUDE.md, not a bespoke slash command.\n\n"
        "Do not skip this re-routing on the assumption that prior invocations cover "
        "the remaining work — they don't. The summary lost the per-call routing state."
    )

    # Repeated compaction is lossy-on-lossy. Past the 2nd compaction OR a large
    # tool-call count, nudge toward the lossless /handoff + /clear reset. Hooks
    # cannot invoke slash commands — this only suggests it.
    if state.get("compactions_seen", 0) >= 2 or state.get("tool_calls_total", 0) >= 60:
        reminder += (
            f"\n\n**Consider `/handoff` + `/clear`.** This session has compacted "
            f"{state.get('compactions_seen', 0)}× ({state.get('tool_calls_total', 0)} "
            "tool calls). Each auto-compact is a lossy summary; another one summarizes "
            "a summary. `/handoff` extracts a clean lossless state file (§1–§8), then "
            "`/clear` gives a fresh session with full fidelity — cheaper and more "
            "accurate than continuing to degrade."
        )

    emit(reminder)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    try:
        if mode == "pre-tool":
            handle_pre_tool(payload)
        elif mode == "post-tool":
            handle_post_tool(payload)
        elif mode == "session-start":
            handle_session_start(payload)
        elif mode == "post-compact":
            handle_post_compact(payload)
        elif mode == "user-prompt-submit":
            handle_user_prompt_submit(payload)
        # else: silent no-op
    except Exception:
        # Fail open: never block tool calls
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
