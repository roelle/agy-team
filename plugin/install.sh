#!/usr/bin/env bash
# Install the clawagy plugin into the Antigravity CLI.
#
# Renders absolute paths into mcp_config.json (env-var expansion inside that
# file is undocumented, so we don't rely on it) and seeds the durable team dir.
# Agent identity is NOT baked in: the MCP servers inherit CLAWAGY_AGENT from the
# agy process, so one install serves every agent.
set -euo pipefail

CLAWAGY_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${CLAWAGY_PYTHON:-$CLAWAGY_HOME/.venv/bin/python}"
PLUGIN_SRC="$CLAWAGY_HOME/plugin/clawagy"
PLUGIN_DST="${CLAWAGY_PLUGIN_DIR:-$HOME/.gemini/antigravity-cli/plugins/clawagy}"
DURABLE="${CLAWAGY_DURABLE_DIR:-$HOME/.gemini/clawagy}"

[ -x "$PYTHON" ] || { echo "ERROR: python not found at $PYTHON (set CLAWAGY_PYTHON)"; exit 1; }

echo "clawagy home : $CLAWAGY_HOME"
echo "python       : $PYTHON"
echo "plugin dest  : $PLUGIN_DST"
echo "durable dir  : $DURABLE"

mkdir -p "$PLUGIN_DST" "$DURABLE/team/inbox" "$DURABLE/agents"
cp -r "$PLUGIN_SRC/." "$PLUGIN_DST/"

sed -e "s|__PYTHON__|$PYTHON|g" -e "s|__CLAWAGY_HOME__|$CLAWAGY_HOME|g" \
    "$CLAWAGY_HOME/plugin/mcp_config.template.json" > "$PLUGIN_DST/mcp_config.json"

if [ ! -f "$DURABLE/team/roster.json" ]; then
  cat > "$DURABLE/team/roster.json" <<'JSON'
{
  "mission": "General-purpose engineering team.",
  "agents": [
    {"name": "tpm",    "role": "Coordinates: decomposes work, delegates, reports to the user. No shell, no file writes."},
    {"name": "coder",  "role": "Implements, runs, and verifies code changes."},
    {"name": "syseng", "role": "Owns environment and tooling; independently verifies others' work."}
  ]
}
JSON
  echo "seeded roster: $DURABLE/team/roster.json"
fi

"$PYTHON" -c "import json,sys; json.load(open('$PLUGIN_DST/mcp_config.json')); json.load(open('$PLUGIN_DST/plugin.json')); print('manifest + mcp_config valid JSON')"

cat <<NOTE

Installed. To run an agent so its memory and mail are correctly attributed:

    CLAWAGY_AGENT=tpm agy --agent tpm

Set CLAWAGY_AGENT to the same name you pass to --agent. Optionally set
CLAWAGY_DURABLE_DIR (identity/memory) and CLAWAGY_PROJECT_DIR (the repo you're
working in); see 'python -m clawagy.scope' for what resolves where.

If your agy build expects plugins elsewhere, re-run with
CLAWAGY_PLUGIN_DIR=/path/to/plugins/clawagy, or use: agy plugin install "$PLUGIN_DST"
NOTE
