#!/usr/bin/env bash
# tests/test_doctor_plugin_detection.sh — fake a `claude` binary on PATH that
# echoes a canned `plugin list --json` payload, run doctor.sh against it, assert
# checks #5 and #7 react correctly to plugin presence/absence, and that a
# missing `claude` binary never crashes doctor.sh.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -R "$SRC/." "$TMP/core"
CORE="$TMP/core"
HOME_DIR="$TMP/home"
CLAUDE_DIR="$HOME_DIR/.claude"
mkdir -p "$CLAUDE_DIR" "$TMP/fakebin"

FAIL=0
ck() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAIL=1; fi; }

echo "=== case 1: claude-core-hooks plugin enabled, no legacy entry, no downbeat ==="
cat > "$TMP/fakebin/claude" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2" = "plugin list" ]; then
  echo '[{"id":"claude-core-hooks@local","enabled":true},{"id":"other-plugin@x","enabled":true}]'
fi
EOF
chmod +x "$TMP/fakebin/claude"
echo '{"hooks":{}}' > "$CLAUDE_DIR/settings.json"
# NOTE: PATH is deliberately restricted to fakebin + system dirs only (not the
# real $PATH) so a real `downbeat` CLI binary installed on the host machine
# cannot leak into this "downbeat absent" scenario and false-positive check #7.
OUT="$(PATH="$TMP/fakebin:/usr/bin:/bin" HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/doctor.sh" 2>&1)"
ck "check5 passes via plugin" 'echo "$OUT" | grep -q "PASS  hook:cost-discipline registered (claude-core-hooks plugin)"'
ck "check7 silent (downbeat absent)" '! echo "$OUT" | grep -q "relay:hooks"'

echo "=== case 2: no plugin, no downbeat binary, no legacy entry -> WARN ==="
cat > "$TMP/fakebin/claude" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2" = "plugin list" ]; then
  echo '[]'
fi
EOF
OUT="$(PATH="$TMP/fakebin:$PATH" HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/doctor.sh" 2>&1)"
ck "check5 warns when nothing registered" 'echo "$OUT" | grep -q "WARN  hook:cost-discipline"'

echo "=== case 3: no claude binary at all -> doctor.sh does not crash ==="
OUT="$(PATH="/usr/bin:/bin" HOME="$HOME_DIR" CLAUDE_DIR="$CLAUDE_DIR" bash "$CORE/doctor.sh" 2>&1)"
ck "doctor.sh still produces a Summary line without claude on PATH" 'echo "$OUT" | grep -q "^Summary:"'

echo ""; [ "$FAIL" -eq 0 ] && echo "DOCTOR SMOKE: all pass" || echo "DOCTOR SMOKE: FAILURES"; exit "$FAIL"
