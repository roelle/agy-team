#!/usr/bin/env bash
# Install the agy-team plugin into the Antigravity CLI.
#
# The installed plugin is self-contained: the MCP servers it mounts are pure
# standard library, so they are copied in alongside the manifest and run under
# a bare system python3. No virtualenv, no third-party packages, and nothing
# breaks if this repo later moves or is deleted.
#
# Agent identity is NOT baked in: the servers inherit AGYTEAM_AGENT from the
# agy process, so one install serves every agent on every team.
set -euo pipefail

AGYTEAM_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${AGYTEAM_PYTHON:-$(command -v python3)}"
PLUGIN_SRC="$AGYTEAM_HOME/plugin/agy-team"
PLUGIN_DST="${AGYTEAM_PLUGIN_DIR:-$HOME/.gemini/antigravity-cli/plugins/agy-team}"
TEAM="${AGYTEAM_TEAM:-default}"
TEAMS_ROOT="${AGYTEAM_TEAMS_ROOT:-$HOME/agy-teams}"
DURABLE="${AGYTEAM_DURABLE_DIR:-$TEAMS_ROOT/$TEAM}"

# Modules the MCP servers need. Deliberately excludes anything importing
# google-genai or google-antigravity; if this list ever needs one of those,
# the plugin has stopped being installable without vendoring.
PURE_MODULES=(__init__.py config.py scope.py store.py roster.py mcp_base.py
              mcp_memory.py mcp_bus.py mcp_self.py transport.py transport_file.py
              transport_template.py memory.py memory_file.py memory_template.py
              runner.py runner_agy.py supervisor.py session.py persona.py
              observer.py observer_file.py observer_template.py)

echo "python      : $PYTHON"
echo "plugin dest : $PLUGIN_DST"
echo "team        : $TEAM"
echo "durable dir : $DURABLE"

[ -x "$PYTHON" ] || { echo "ERROR: python3 not found (set AGYTEAM_PYTHON)"; exit 1; }

mkdir -p "$PLUGIN_DST/agyteam" "$DURABLE/team/inbox" "$DURABLE/agents"
cp -r "$PLUGIN_SRC/." "$PLUGIN_DST/"
for m in "${PURE_MODULES[@]}"; do
  cp "$AGYTEAM_HOME/agyteam/$m" "$PLUGIN_DST/agyteam/$m"
done

sed -e "s|__PYTHON__|$PYTHON|g" -e "s|__AGYTEAM_HOME__|$PLUGIN_DST|g" \
    "$AGYTEAM_HOME/plugin/mcp_config.template.json" > "$PLUGIN_DST/mcp_config.json"

if [ ! -f "$DURABLE/team/roster.json" ]; then
  cat > "$DURABLE/team/roster.json" <<JSON
{
  "mission": "General-purpose engineering team ($TEAM).",
  "_note": "The two oversight roles -- tpm (accountable gate) and qa (review) -- run a different model family from the implementers on purpose. Three reviewers with different blind spots caught three defects on one feature that none of them found alone; capability alone did not do that, difference did. A gate that shares its team's blind spots is not a gate: the manager is the last line before the user, and for several features she ran the same model as the work she was checking. Oversight runs a few turns per feature while implementers run many, so paying for a slower model exactly there is cheap.",
  "agents": [
    {"name": "manager", "role": "Faces outward. Takes the problem from the user and restates it before anything is built: what is being asked, what would make a good answer, what is assumed, what needs answering. Decides what good means rather than waiting to be handed a metric. Gatekeeper - nothing reaches the user claiming to be done without an approved review. A rejected or unreviewed result comes back here, never onward. Accountable: 'coder said it was done' is not a defence. Does not write code, run commands, or delegate implementation directly - work goes through tpm.",
     "tools_off": ["run_command", "create_file", "edit_file"], "workers": false,
     "model": "gemini-3.1-pro-preview"},
    {"name": "tpm",    "role": "Faces inward, boots on the ground with the team. Takes work from the manager and decomposes it into pieces one agent can actually finish in one turn, delegates by name with complete context, tracks what is outstanding, and unblocks people. Leads retros. Reports to the manager, not to the user. Does not write code or run commands.",
     "tools_off": ["run_command", "create_file", "edit_file"], "workers": false},
    {"name": "coder",  "role": "Implements. Reads the surrounding code first and matches its idiom. Writes the change and its tests, and runs them before handing off."},
    {"name": "syseng", "role": "Owns environment and tooling. Reproduces problems, runs the full board, and reports concrete failures with the command and output that produced them."},
    {"name": "qa",     "role": "Reviews. Reads the diff rather than re-running the author's tests, constructs cases the author did not write, and records the verdict with record_review. Cannot edit source: findings go back to the implementer. Keeps a durable memory of the defect patterns that recur here.",
     "tools_off": ["create_file", "edit_file"], "workers": false,
     "model": "gemini-3.1-pro-preview"}
  ]
}
JSON
  echo "seeded roster: $DURABLE/team/roster.json"
fi

# Prove the install actually works rather than merely looking right: exercise it
# using only the installed copy, with this repo off the path. Importing the
# servers is not enough — the transport is resolved lazily at runtime, so a
# module missing from PURE_MODULES would slip through. Instantiate it and make a
# real call.
( cd / && env -u AGYTEAM_RUNNER PYTHONPATH="$PLUGIN_DST" AGYTEAM_TEAM_DIR="$DURABLE/team" "$PYTHON" -c "
import json, agyteam.mcp_memory, agyteam.mcp_bus, agyteam.mcp_self
from agyteam.transport import load as load_bus
from agyteam.memory import load as load_memory
from agyteam.runner import load as load_runner
from agyteam.observer import load as load_observer
load_bus('installer-selftest').teammates()      # forces roster parsing
load_memory('installer-selftest').index()       # forces memory store load
load_runner()                                   # forces runner load
load_observer()                                 # forces observer load
json.load(open('$PLUGIN_DST/mcp_config.json')); json.load(open('$PLUGIN_DST/plugin.json'))
print('verified: servers import; transport, memory store, runner, and observer all load')" )

cat <<NOTE

Installed. Run an agent so its memory and mail are attributed correctly:

    $PYTHON -m agyteam.session tpm

That joins the conversation tpm is actually working in, with its identity and
role already set. Do not use 'agy --agent tpm': that mechanism strips every
builtin tool, so the agent cannot write a file.

Or let the team run itself — teammates are woken when they get mail, so one
instruction cascades without you checking anything:

    $PYTHON -m agyteam.supervisor --say "tpm: <your task>"    # until idle
    $PYTHON -m agyteam.supervisor --daemon                    # stay reactive
    $PYTHON -m agyteam.supervisor --status                    # who has mail

Teams are isolated — separate roster, bus, and memory, no cross-talk:

    AGYTEAM_TEAM=team-b bash plugin/install.sh     # seed another team
    AGYTEAM_TEAM=team-b $PYTHON -m agyteam.session tpm

Or point somewhere explicit (useful if your backups target a specific tree):

    AGYTEAM_DURABLE_DIR=~/my-agents/my-agent-team-A $PYTHON -m agyteam.session tpm

'$PYTHON -m agyteam.scope' prints what currently resolves where.
NOTE
