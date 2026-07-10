#!/usr/bin/env node
// /harvest — stage 1: deterministic harvest for article-topic ideation. No LLM.
// Collects the current repo's recent git activity + existing content-seed slugs (for dedup),
// and prints a compact markdown blob for the ideation stage (driven by the /harvest command).
//
// Usage: node harvest.mjs [--commits N] [--since <git-date>]
//   Default commit count comes from ~/.claude/harvest.config.json {"defaultCommits": N} (else 10).
//   --commits N overrides it. --since "2 weeks ago" uses a date window instead of a count.
// Node 14+ (only uses ??, no other newer syntax), no deps.

import { execFileSync } from "node:child_process";
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const CONFIG = join(homedir(), ".claude", "harvest.config.json");
const cfg = existsSync(CONFIG) ? JSON.parse(readFileSync(CONFIG, "utf8")) : {};
const DEFAULT_COMMITS = cfg.defaultCommits ?? 10;
const SEEDS_DIR = cfg.seedsDir || "";

const argv = process.argv.slice(2);
const getArg = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
const commits = parseInt(getArg("--commits") ?? "", 10) || DEFAULT_COMMITS;
const since = getArg("--since");

const git = (a) => {
  try { return execFileSync("git", a, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(); }
  catch { return ""; }
};

let out = "";
if (git(["rev-parse", "--is-inside-work-tree"]) !== "true") {
  out += "# Harvest\n\n(not inside a git repo — no git activity harvested; ideate from session context alone)\n";
} else {
  const repo = git(["rev-parse", "--show-toplevel"]).split("/").pop();
  const total = parseInt(git(["rev-list", "--count", "HEAD"]) || "0", 10);
  const n = Math.min(commits, Math.max(total - 1, 0));  // HEAD~n needs n < total
  // `git log -n` uses the raw `commits` value (not `n`): -n clamps to available
  // history on its own, unlike `HEAD~n..HEAD` which errors if n >= total — so
  // only the diff range below needs the clamped `n`.
  const range = since ? [`--since=${since}`] : ["-n", String(commits)];
  const log = git(["log", ...range, "--format=%h %ad %s", "--date=short"]);
  const stat = since
    ? git(["log", "--since=" + since, "--stat", "--format="]) || ""
    : (n > 0 ? git(["diff", "--stat", `HEAD~${n}..HEAD`]) : "");

  out += `# Harvest: ${repo}\n\n`;
  out += `## Commits (${since ? "since " + since : "last " + commits})\n` + (log || "(none)") + "\n\n";
  out += `## Files changed\n` + (stat || "(none)") + "\n";
}

if (SEEDS_DIR && existsSync(SEEDS_DIR)) {
  const slugs = readdirSync(SEEDS_DIR)
    .filter((f) => f.endsWith(".md") && !f.startsWith("_") && f !== "README.md")
    .map((f) => f.replace(/\.md$/, ""));
  out += `\n## Existing content-seeds — do NOT duplicate these topics\n`;
  out += (slugs.length ? slugs.map((s) => "- " + s).join("\n") : "(none)") + "\n";
}

process.stdout.write(out);
