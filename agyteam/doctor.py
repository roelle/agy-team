"""Preflight: prove the wiring works before spending a run finding out it doesn't.

    python -m agyteam.doctor

Three of the worst failures found so far were not agyteam bugs and not
testable from this repo, because they live in the join between agyteam and
whatever host is driving the agents. All three are cheap to *self-diagnose* if
something exercises the seam and prints what is actually wired:

**A tool nobody can call.** An agent reliably calls only a tool whose
signature it knows, and a lazily-loaded MCP tool never appears in an agent's
own list of what it has. The only honest probe is a call. A manager once spent
an entire run on nine failed delegations against a tool it could not see the
shape of, and the error it got back each time was `[error: 'to']`.

**A bus bound to the wrong team.** Where the bus is an MCP server, the team
directory reaches it as an environment variable, and that environment is read
once, when the host spawns the process. Rewriting the config to point at a
different team does not rebind a server that is already running, and nothing
anywhere reports the mismatch. Measured: five servers still bound to the
previous team, agents delegating normally, every tool call returning success,
mail landing in the old team's bus, and the new supervisor seeing an empty bus
and declaring the team idle. Eight runs completed in sixteen minutes and
produced a full set of meaningless scores -- and the symptom read as an agent
failure, because each run ended "[done after 1 turns - team went idle]" with
reason NO_TOOL_CALL.

**A brief missing what the agent needs.** Cheap to see now, expensive to
discover nine delegations in.

None of this needs an API key or a model. It writes a probe message through
the bus and checks it lands where the supervisor will read it.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import persona, roster as roster_lib, scope
from .mcp_bus import TOOLS
from .runner import capabilities

OK, WARN, BAD = "  ok ", " warn", " FAIL"


class Report:
    def __init__(self) -> None:
        self.failed = False

    def line(self, mark: str, text: str) -> None:
        if mark == BAD:
            self.failed = True
        print(f"[{mark}] {text}")

    def detail(self, text: str) -> None:
        for ln in text.splitlines():
            print(f"        {ln}")


def check_runner(r: Report) -> dict:
    caps = capabilities()
    r.line(OK if caps["error"] is None else BAD, f"runner: {caps['spec']}")
    if caps["error"]:
        r.detail(caps["error"])
        return caps
    for flag in ("supports_audit", "supports_containment",
                 "supports_capability_scoping"):
        value = caps[flag]
        mark = {True: OK, False: WARN, None: WARN}[value]
        r.line(mark, f"{flag} = {value}")
    if caps["supports_audit"] and not os.environ.get("AGYTEAM_AUDIT_LOG"):
        r.line(WARN, "this runner can audit but AGYTEAM_AUDIT_LOG is unset, "
                     "so nothing is being recorded")
    if caps["supports_containment"] is False:
        r.line(WARN, "roster `workspaces` do nothing on this runner; "
                     "containment is whatever the host enforces")
    if caps["supports_capability_scoping"] is False:
        r.line(WARN, "roster `tools_off` is not enforced on this runner; "
                     "it is a statement of intent, not a boundary")
    return caps


def check_reply_text(r: Report, agent: str) -> None:
    """Which column is this host in? The operator should not have to guess.

    Nothing depends on reply text any more, but knowing whether you have it
    decides how much of the record is readable when something goes wrong.
    """
    r.line(OK, "wake() reply text: not required by anything in agyteam")
    r.detail("Agents publish over the bus. If your runner returns \"\" for a "
             "completed turn that is correct and supported; never return "
             '"[error: ...]" for a quiet success.')


def check_tools(r: Report, team_dir: Path, agent: str) -> None:
    """Call every bus tool end to end. A tool list is not a probe; a call is."""
    from .mcp_bus import main as _bus_main            # noqa: F401  (import check)
    from .transport import load as load_transport

    try:
        t = load_transport(agent)
    except Exception as e:
        r.line(BAD, f"could not open the bus as '{agent}': {e}")
        return

    from .mcp_bus import _check_inbox, _list_teammates

    probe = f"[agyteam.doctor probe from {agent}]"
    try:
        sent = t.send(agent, probe)
        r.line(OK if not sent.startswith("[error:") else BAD,
               f"send_to_teammate -> {sent}")
    except Exception as e:
        r.line(BAD, f"send_to_teammate raised {type(e).__name__}: {e}")
        return

    # THE check: did the probe land where the supervisor will look for it?
    bus_file = team_dir / "bus.jsonl"
    landed = bus_file.exists() and probe in bus_file.read_text(errors="replace")
    if landed:
        r.line(OK, f"the probe is readable in {bus_file}")
    else:
        r.line(BAD, "the probe did NOT land in the team directory the "
                    "supervisor reads")
        r.detail(f"supervisor will read: {bus_file}\n"
                 f"the bus wrote to:     {getattr(t, 'log', 'unknown')}\n"
                 f"If the bus is an MCP server, its environment was read when "
                 f"the host spawned it; changing the config does not rebind a "
                 f"running server. Kill the bus server processes and let them "
                 f"respawn.")

    try:
        inbox = _check_inbox(t)
        r.line(OK if probe in inbox else WARN,
               "check_inbox returned the probe" if probe in inbox
               else "check_inbox did not return the probe (it may have been "
                    "drained by a running supervisor)")
    except Exception as e:
        r.line(BAD, f"check_inbox raised {type(e).__name__}: {e}")

    try:
        mates = _list_teammates(t)
        r.line(OK, f"list_teammates -> {len(mates.splitlines())} entries")
    except Exception as e:
        r.line(BAD, f"list_teammates raised {type(e).__name__}: {e}")

    try:
        from . import retro_store
        out = retro_store.record(team_dir, agent,
                                 "doctor probe: the bus round-tripped",
                                 "doctor probe: nothing yet, this is preflight",
                                 "doctor probe: no change, this is a probe",
                                 probe=True)
        r.line(OK if not out.startswith("[error:") else BAD,
               f"record_retro -> {out.splitlines()[0][:80]}")
    except Exception as e:
        r.line(BAD, f"record_retro raised {type(e).__name__}: {e}")

    declared = {spec["name"] for spec in TOOLS}
    exercised = {"send_to_teammate", "check_inbox", "list_teammates",
                 "record_retro"}
    skipped = declared - exercised - {"broadcast", "record_review",
                                      "list_reviews"}
    if skipped:
        r.line(WARN, f"not exercised by this probe: {', '.join(sorted(skipped))}")
    r.line(OK, "record_review is not probed: it runs a proof file and writes a "
               "durable record, which a preflight should not do")

    try:
        t.close()
    except Exception:
        pass


def check_stale_bus_servers(r: Report, team_dir: Path) -> None:
    """Look for bus server processes bound to a different team.

    The probe above is necessary and not sufficient, and the difference
    matters: it loads the transport in *this* process, so the two paths it
    compares can never disagree. The failure it is named after happens in a
    process started earlier, whose environment was captured at spawn time and
    cannot be changed by rewriting a config file.

    So read the environment of the servers that are actually running. This is
    the check that would have caught it: five live servers still pointing at
    the previous team while the mount config on disk said otherwise.

    Unreadable is reported as unreadable. A process listing we could not read
    is not a process listing that found nothing.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        r.line(WARN, "cannot enumerate processes on this platform; check by "
                     "hand that no bus server is bound to an older team")
        return

    found, unreadable = [], 0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = [p for p in (entry / "cmdline").read_bytes().split(b"\0") if p]
            # Exact argv element, not a substring: any shell whose command
            # line merely mentions the module matches otherwise, and a check
            # that reports the shell you are typing in as a stale bus server
            # is one people learn to ignore.
            if b"agyteam.mcp_bus" not in cmdline:
                continue
            env = (entry / "environ").read_bytes().split(b"\0")
        except OSError:
            unreadable += 1
            continue
        bound = next((v.decode(errors="replace").split("=", 1)[1] for v in env
                      if v.startswith(b"AGYTEAM_TEAM_DIR=")), None)
        # Identity may also arrive as positional args: <team_dir> <agent>,
        # which is the SDK path (see mcp_bus.__main__).
        if bound is None:
            at = cmdline.index(b"agyteam.mcp_bus")
            if len(cmdline) >= at + 3:
                bound = cmdline[at + 1].decode(errors="replace") or None
        found.append((entry.name, bound))

    stale = [(pid, b) for pid, b in found
             if b and Path(b).resolve() != team_dir.resolve()]
    if stale:
        r.line(BAD, f"{len(stale)} bus server(s) bound to a DIFFERENT team")
        for pid, bound in stale:
            r.detail(f"pid {pid}: {bound}")
        r.detail(f"this run reads: {team_dir}\n"
                 f"That environment was read when the host spawned the "
                 f"process; rewriting the mount config does not rebind a "
                 f"running server, and nothing reports the mismatch. Agents "
                 f"will delegate normally, every tool call will return "
                 f"success, the mail will land in the other team's bus, and "
                 f"this team will look idle. Kill these pids and let them "
                 f"respawn.")
    elif found:
        r.line(OK, f"{len(found)} bus server(s) running, all bound to this team")
    else:
        r.line(OK, "no bus server processes running "
                   "(they are spawned per session by the host)")
    if unreadable:
        r.line(WARN, f"{unreadable} process(es) could not be read; this check "
                     f"is incomplete, not clean")


def check_workspace_grants(r: Report, team_dir: Path, caps: dict) -> None:
    """No grant may reach the team directory, because that is the record.

    bus.jsonl, reviews.jsonl, retro_inbox.jsonl, NORMS.md, roster.json and
    conversations.json are all written by agyteam processes and read back as
    the account of what the team did. Agents reach them through the bus tools,
    which run in a separate process and need no grant of their own -- so a
    grant that covers the team directory buys nothing and costs every gate
    built on those files at once: reviews can be appended without passing the
    review gate, a retrospective answer can be written in a teammate's name,
    and roster.json can grant the rest.
    """
    from .lifecycle import covers_team_dir

    try:
        doc = roster_lib.load(team_dir / "roster.json")
    except Exception as e:
        r.line(WARN, f"could not read the roster to check grants: {e}")
        return

    grants = [("team", w) for w in doc.get("workspaces", [])]
    for a in doc.get("agents", []):
        grants += [(a.get("name", "?"), w) for w in a.get("workspaces", [])]

    bad = [(who, w) for who, w in grants if covers_team_dir(w, team_dir)]
    if not bad:
        r.line(OK, f"no workspace grant reaches the team directory "
                   f"({len(grants)} grant(s))")
    else:
        r.line(BAD, f"{len(bad)} workspace grant(s) contain the team directory")
        for who, w in bad:
            r.detail(f"{who}: {w}")
        r.detail(f"team directory: {team_dir}\n"
                 f"An agent with write access there can append to "
                 f"reviews.jsonl and retro_inbox.jsonl directly, which is "
                 f"every review gate at once. Revoke it and grant the working "
                 f"tree, or move the team directory out of it.")

    if caps.get("supports_containment") is False and grants:
        r.line(WARN, "this runner enforces no workspace boundary, so the team "
                     "directory is reachable regardless of the grant list")
        r.detail("Keep it outside every working tree and rely on the host: "
                 "filesystem permissions, a container, or a separate account.")


def check_plugin_install(r: Report, spec: str) -> None:
    """Is the code the agents run the code you are reading?

    Where agents run inside the CLI, the MCP tools are served by the *installed*
    copy of this package, not by this repo. Nothing in the repo's test suite can
    see that copy, the install self-test only imports the servers, and an
    install that is months behind fails nothing and reports nothing. Measured
    here: an install pinned to a commit from before the review gate checked
    authorship at all, quietly accepting reviews an agent wrote of its own work,
    while every test in the repo asserted the gate held.

    Three things, then, and in this order: does it exist, is it complete on its
    own terms, and is it this code.
    """
    from . import install_check

    dst = install_check.default_install_dir()
    pkg = dst / "agyteam"
    if not pkg.is_dir():
        r.line(OK, f"no installed plugin at {dst} (nothing to check)")
        r.detail("Agents driven through the CLI get their tools from an "
                 "installed copy; if you use one, install it with "
                 "plugin/install.sh so this check has something to compare.")
        return

    # A check that compares a thing with itself always passes, which is the
    # one result it must never be allowed to report.
    if install_check.SRC == pkg.resolve():
        r.line(WARN, f"running from the installed copy at {pkg}; drift from "
                     f"the repo cannot be judged from here")
        return

    gaps = install_check.missing(dst)
    if gaps:
        r.line(BAD, f"the install is incomplete: {', '.join(gaps)}")
        r.detail("These are either imported by the installed sources or are "
                 "entrypoints an install is expected to carry. A module "
                 "imported inside a function raises ImportError on the first "
                 "call that reaches it, mid-run, while importing the server "
                 "looks fine. Reinstall: bash plugin/install.sh")
    else:
        r.line(OK, "the install has every module its own sources import")

    changed = install_check.drift(dst)
    if not changed:
        r.line(OK, f"the installed plugin matches this repo ({pkg})")
        return
    # Only the CLI path serves tools from the install; on the SDK path a stale
    # copy is untidy rather than load-bearing, and saying FAIL where it is not
    # load-bearing is how a preflight gets ignored.
    serves_tools = "sdk" not in spec.lower()
    r.line(BAD if serves_tools else WARN,
           f"the installed plugin is NOT this code: {len(changed)} file(s) differ")
    r.detail(", ".join(changed[:8]) + (" ..." if len(changed) > 8 else ""))
    r.detail(f"installed: {pkg}\nthis repo: {install_check.SRC}\n"
             + ("Agents on this runner call tools served by that copy, so a run "
                "started now measures that code and not this one. "
                if serves_tools else
                "This runner serves its own tools, so the stale copy is not in "
                "the path of a run -- but anything you start in the CLI is. ")
             + "Reinstall: bash plugin/install.sh")


def check_brief(r: Report, team_dir: Path, agent: str, lines: int) -> None:
    try:
        agents = roster_lib.load(team_dir / "roster.json")["agents"]
    except Exception as e:
        r.line(BAD, f"could not read the roster: {e}")
        return
    if not any(a["name"] == agent for a in agents):
        r.line(BAD, f"'{agent}' is not on the roster: "
                    f"{', '.join(a['name'] for a in agents)}")
        return
    text = persona.brief(agent, agents, team_dir=team_dir)
    has_index = "send_to_teammate(to, content)" in text
    r.line(OK if has_index else BAD,
           "the brief carries the bus tool signatures"
           if has_index else
           "the brief is MISSING the tool index; an agent on a host that does "
           "not inject MCP schemas will have to guess argument names")
    print()
    print(f"--- first {lines} lines of {agent}'s brief " + "-" * 24)
    for ln in text.splitlines()[:lines]:
        print(ln)
    print("-" * 60)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="agyteam.doctor",
        description="Exercise the seam between agyteam and its host, and "
                    "print what is actually wired.")
    ap.add_argument("--agent", default=None,
                    help="probe as this agent (default: the first on the roster)")
    ap.add_argument("--team-dir", default=None)
    ap.add_argument("--brief-lines", type=int, default=40)
    a = ap.parse_args(argv)

    team_dir = scope.team_dir(a.team_dir)
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)

    agent = a.agent
    if agent is None:
        try:
            agent = roster_lib.load(team_dir / "roster.json")["agents"][0]["name"]
        except Exception:
            agent = None
    if not agent:
        print(f"no roster at {team_dir / 'roster.json'} and no --agent given; "
              f"run `./run setup` or pass --agent", file=sys.stderr)
        return 2

    print(f"team:   {team_dir}")
    print(f"agent:  {agent}\n")

    r = Report()
    caps = check_runner(r)
    print()
    check_reply_text(r, agent)
    print()
    check_tools(r, team_dir, agent)
    print()
    check_workspace_grants(r, team_dir, caps)
    print()
    check_plugin_install(r, caps.get("spec") or "")
    print()
    check_stale_bus_servers(r, team_dir)
    print()
    check_brief(r, team_dir, agent, a.brief_lines)

    print()
    if r.failed:
        print("PREFLIGHT FAILED. Fix the FAIL lines before starting a run: "
              "every one of them costs more to find mid-run than here.")
        return 1
    print("Preflight clean. Warnings above are things to know, not things to "
          "fix -- a runner that cannot audit is allowed, as long as nothing "
          "downstream pretends otherwise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
