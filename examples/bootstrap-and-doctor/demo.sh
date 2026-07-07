#!/usr/bin/env bash
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DEMO_HOME="$(mktemp -d)"
export CLAUDE_SKILLS="$DEMO_HOME/.claude/skills"
export CLAUDE_DIR="$DEMO_HOME/.claude"
mkdir -p "$CLAUDE_DIR"

"$CORE_DIR/bootstrap.sh"
"$CORE_DIR/doctor.sh" || true   # doctor.sh exits 1 on WARN — expected in this isolated demo dir

rm -rf "$DEMO_HOME"
