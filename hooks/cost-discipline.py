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

- Default session main is now **Opus 4.8** (high effort). Opus ≈ 1.7–5× Sonnet per token — switch to `/model sonnet` for routine/mechanical phases; reserve Opus for architecture, cross-repo synthesis, root-cause. Delegation discipline pays most on an Opus main.
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


_EXPENSIVE_MODEL_CACHE = {"value": None, "name": None, "checked_at": 0.0}
_EXPENSIVE_MODEL_TTL = 60.0  # seconds


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
        _EXPENSIVE_MODEL_CACHE["checked_at"] = now
    except Exception:
        # Don't cache a read failure for the full TTL — leave checked_at=0 so a
        # transiently-missing/unreadable settings.json self-heals on the next call
        # instead of pinning "unknown" (and disarming the model-tier gates) for 60s.
        _EXPENSIVE_MODEL_CACHE["value"] = False
        _EXPENSIVE_MODEL_CACHE["name"] = "unknown"
        _EXPENSIVE_MODEL_CACHE["checked_at"] = 0.0


def is_expensive_main_model():
    """True iff settings.json main model is Opus. Cached for 60s."""
    _refresh_model_cache()
    return _EXPENSIVE_MODEL_CACHE["value"]


def get_main_model_name():
    """Normalised model-family name from settings.json: opus|sonnet|haiku|unset|unknown.
    Used to stamp fire log entries so audits can split heed-rate by model.
    """
    _refresh_model_cache()
    return _EXPENSIVE_MODEL_CACHE["name"]


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
        "aggregate_reads": 0,
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
        "mech_bash_on_opus_streak": 0,
        "repo_roots_seen": [],   # Layer 3: distinct "<group>/<repo>" roots read inline
        "rlm_active": False,     # Layer 3 suppressor: a Workflow dispatch happened (NEW-2)
        "compactions_seen": 0,   # PostCompact: count compactions to nudge toward /handoff
        "tool_result_chars": 0,  # L4: cumulative tool result chars accumulated in context
        "tool_result_chars_by_tool": {},  # cost-ledger: per-tool char breakdown (which tool floods)
        "cwd": None,             # cost-ledger: working dir, captured at session start
        "started_at": None,      # cost-ledger: ISO8601, captured at session start
        "reader_violations": {},  # I4: per-family inline-read violation count (post-first-fire)
        "read_streak": 0,         # consecutive read-only tool calls (hard-block tier, 2026-06-27)
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
    p = state_path(state["session_id"])
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


# Cost estimate calibration (matches the L4 context-ledger message):
# ~3.5 chars/token, ~$22.5 per 1M cache-read tokens re-paid each turn.
LEDGER_CHARS_PER_TOKEN = 3.5
LEDGER_USD_PER_MTOK = 22.5


def cost_ledger_path(session_id):
    return COST_LEDGER_DIR / f"{session_id}.json"


def build_cost_ledger(state):
    """Derive the compact, collectable per-session cost summary from working state.
    Pure function (no I/O) so it is trivially testable."""
    chars = state.get("tool_result_chars", 0)
    tokens = int(chars / LEDGER_CHARS_PER_TOKEN)
    by_tool_chars = state.get("tool_result_chars_by_tool", {}) or {}
    by_tool = {
        name: {"chars": c, "tokens": int(c / LEDGER_CHARS_PER_TOKEN)}
        for name, c in sorted(by_tool_chars.items(), key=lambda kv: kv[1], reverse=True)
    }
    return {
        "session_id": state.get("session_id"),
        "cwd": state.get("cwd"),
        "started_at": state.get("started_at"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "main_model": get_main_model_name(),
        "tool_calls_total": state.get("tool_calls_total", 0),
        "tool_result_chars": chars,
        "tool_result_tokens_est": tokens,
        "cache_reread_usd_per_turn_est": round(tokens * LEDGER_USD_PER_MTOK / 1e6, 2),
        "aggregate_reads": state.get("aggregate_reads", 0),
        "tool_result_chars_by_tool": by_tool,
    }


def write_cost_ledger(state):
    """Best-effort: persist the per-session cost summary to the cross-session ledger dir.
    Never raises — a ledger write must not block or fail a tool call."""
    try:
        session_id = state.get("session_id")
        if not session_id:
            return
        COST_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        p = cost_ledger_path(session_id)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(build_cost_ledger(state), indent=2))
        tmp.replace(p)
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
def blocks_enabled():
    """Hard blocks are gated three ways, all fail-safe toward NOT blocking:
      1. env kill-switch CC_DISCIPLINE_BLOCK=0 → advisory-only;
      2. agent/background-job sessions are exempt — reader/scout sub-agents are
         SUPPOSED to read in bulk, and the hook can't tell a scout's reads from a
         main-agent inline streak, so it errs toward not blocking them (blocking a
         scout would break the very delegation pattern this hook encourages);
      3. a cheap haiku main is warn-only.
    Note this intentionally INCLUDES sonnet (unlike is_expensive_main_model, which
    is opus/fable-only): the audit's worst inline-read offenders were sonnet relay
    children, and the block targets delegation waste, which applies to sonnet too.
    The audit found warnings ignored 82-88%; this tier gives the worst offenders
    teeth while staying instantly reversible. Conservative v1 — revisit child/agent
    blocking once hook-firing semantics inside sub-agents are confirmed."""
    if os.environ.get("CC_DISCIPLINE_BLOCK", "1") == "0":
        return False
    if detect_session_mode() == "agent":
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
      2. Layer 2 cross-repo rlm-fanout suggestion.

    This handler now writes state (the hygiene pulse needs a prompt counter),
    which the Layer-2 path previously did not. Fail-open throughout.
    """
    session_id = payload.get("session_id")
    prompt = payload.get("prompt") or ""
    parts = []

    if session_id:
        try:
            state = load_state(session_id)
            state["prompts_seen"] = state.get("prompts_seen", 0) + 1
            save_state(state)
            hyg = hygiene_context(state)
            if hyg:
                parts.append(hyg)
                log_fire("harness_hygiene", session_id, "warn")
        except Exception:
            pass  # hygiene must never break the prompt

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
        if blocks_enabled() and is_cat_as_read(_bcmd):
            save_state(state)
            log_fire("block_cat_as_read", session_id, "block", command=_bcmd[:120])
            emit_block(
                "🛑 `cat <file>` to read a source file — use the Read tool instead. "
                "cat dumps the whole file into context with no line numbers and no range; "
                "Read gives you offset/limit and peek-first. Transform pipelines "
                "(cat x | grep y) and heredocs are unaffected. "
                "(Override on this machine: export CC_DISCIPLINE_BLOCK=0)")
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
    _wiki_path_hit = False
    if tool_name == "Read":
        _wiki_path_hit = (_WIKI_PATH + "/") in (tool_input.get("file_path") or "")
    elif tool_name in ("Grep", "Glob"):
        _wiki_path_hit = (_WIKI_PATH + "/") in (tool_input.get("path") or "")
    elif tool_name == "Bash":
        _wiki_path_hit = _WIKI_PATH in (tool_input.get("command") or "")
    if _wiki_path_hit:
        state["wiki_read_count"] = state.get("wiki_read_count", 0) + 1

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
    if tool_name in READ_TOOLS:
        state["aggregate_reads"] += 1
        state["read_streak"] = state.get("read_streak", 0) + 1
        state["recent_tools"].append(tool_name)
        state["recent_tools"] = state["recent_tools"][-5:]

        # ---- Hard-block tier (2026-06-27 self-audit) — short-circuits before the warns ----
        if blocks_enabled() and (
                state["read_streak"] >= STREAK_BLOCK_THRESHOLD
                or state["aggregate_reads"] >= AGGREGATE_BLOCK_THRESHOLD):
            _streaky = state["read_streak"] >= STREAK_BLOCK_THRESHOLD
            _n = state["read_streak"] if _streaky else state["aggregate_reads"]
            save_state(state)
            log_fire("block_read_streak" if _streaky else "block_aggregate_reads",
                     session_id, "block", count=_n, tool_name=tool_name)
            emit_block(
                f"🛑 Read-discipline hard-block: {_n} "
                + ("consecutive " if _streaky else "")
                + "inline read-only calls in main with no delegation — the pattern the "
                "2026-06-27 audit found running unchecked (warnings ignored 82-88%). Do ONE: "
                "(a) dispatch a Haiku reader/scout to take over the reading "
                "(Agent with model: haiku); (b) write/edit something concrete; or (c) /clear and "
                "re-prime from a plan file. The counter resets on any dispatch or /clear. "
                "(Override on this machine: export CC_DISCIPLINE_BLOCK=0)")
            return

        if state["aggregate_reads"] == AGGREGATE_THRESHOLD:
            fire_once(state, "aggregate_15",
                f"⚠️  Aggregate read discipline: {AGGREGATE_THRESHOLD} inline mechanical tool calls in main this session. "
                "Per delegation-discipline/references/hard-rules.md, the next reading task must be dispatched to a Haiku scout. "
                "Counter resets on /clear or successful Agent dispatch.")
        elif state["aggregate_reads"] >= AGGREGATE_ESCALATION:
            fire_once(state, "aggregate_25",
                f"🚨 Aggregate read discipline (escalation): {state['aggregate_reads']} mechanical calls — "
                f"{state['aggregate_reads'] - AGGREGATE_THRESHOLD} over threshold. "
                "Stop and dispatch a Haiku scout NOW, or run /clear and re-prime from a plan file.")

        # Streak warn tier shares the read_streak counter with the block tier above
        # (single source of truth — no desync between the two consecutive-read measures).
        if state["read_streak"] >= STREAK_THRESHOLD:
            fire_once(state, "streak_4",
                f"⚠️  Streak discipline: {state['read_streak']} consecutive read-only tool calls in main "
                f"({', '.join(state['recent_tools'][-STREAK_THRESHOLD:])}). "
                "Next action must either write/edit something concrete, or dispatch a Haiku agent.")
    else:
        # any non-read tool resets the streak window
        state["read_streak"] = 0
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
        # New edit on this file — re-warn / re-escalate allowed if cycle resumes
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
    state["read_streak"] = 0
    state["recent_tools"] = []
    # Allow the aggregate warnings to fire again in the next arc
    for key in ("aggregate_15", "aggregate_25", "streak_4"):
        if key in state["warnings_fired"]:
            state["warnings_fired"].remove(key)
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
    emit_session_context(FORCE_LOAD_RULES)
    # Seed the cross-session cost ledger LAST so a session that does no tool calls
    # still appears in the collection (with zeroed counters).
    if session_id:
        write_cost_ledger(load_state(session_id))


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
    state["read_streak"] = 0
    state["recent_tools"] = []
    state["mech_bash_on_opus_streak"] = 0  # I5: don't carry a mid-streak across compaction
    state["files_edited"] = []             # I5: edit-loop tracking restarts on fresh context
    state["files_warned_for_reread"] = []
    state["warnings_fired"] = []
    state["repo_roots_seen"] = []
    state["rlm_active"] = False
    state["tool_result_chars"] = 0  # L4: compaction clears context; drag restarts from zero
    # compactions_seen is NOT reset — it counts across the session's lifetime to
    # detect repeated auto-compaction (the signal to switch to a lossless /handoff).
    state["compactions_seen"] = state.get("compactions_seen", 0) + 1
    save_state(state)

    # Emit a re-routing reminder. NOTE: PostCompact does NOT support
    # hookSpecificOutput.additionalContext (only PreToolUse / UserPromptSubmit /
    # PostToolUse / PostToolBatch do). Emitting it there fails Hook JSON
    # validation and the checkpoint silently never lands. PostCompact's only
    # model-visible channel is the top-level `systemMessage` — use emit().
    # (Fixed 2026-06-03 after observing the validation error in a live compact.)
    reminder = (
        "**Post-compact checkpoint — cost discipline re-routing required.**\n\n"
        "The conversation summary above describes prior tasks in an execution frame "
        "(*\"continue\"*, *\"execute Tasks 1-7\"*, *\"force-push\"*). That framing "
        "competes with the cost-discipline reminder. Before resuming substantive work:\n\n"
        "1. **Invoke `models-router` skill** to re-decide the main-agent model for "
        "the remaining work. Prior model choice is summarized, not live.\n"
        "2. **Invoke `delegation-discipline` skill** before the next bulk read or "
        "sub-agent dispatch. Prior dispatch decisions don't carry over.\n"
        "3. **Reader-agent reflex**: for kubectl get/describe/logs/top, dispatch "
        "`kubectl-reader` (Haiku). For gh pr/run/repo reads, dispatch `gh-reader`. "
        "For Slack reads, `slack-reader`. For Datadog, `datadog-reader`. "
        "For Jira reads (getJiraIssue/searchJiraIssuesUsingJql/transitions), dispatch `jira-reader`.\n"
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
