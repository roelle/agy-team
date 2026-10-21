"""Update agy-team and restart its supervisors, in one command.

    python -m agyteam.update            # update, reinstall the plugin, restart every running team
    python -m agyteam.update --check    # what would change; changes nothing
    python -m agyteam.update --no-code  # just restart the running teams gracefully

Upgrading a live team used to mean waiting until every agent was idle at
once -- rare, on a team that keeps itself busy -- or stopping it, which
cancelled the turns in flight. This updates the code where it is installed
(a git checkout fast-forwards; a pip install from git moves to the newest
release tag), reinstalls the agy CLI plugin if there is one, and asks each
running supervisor to restart: it starts no new turns, lets the ones in
flight finish, and restarts itself on the new code. Mail keeps queuing
while it drains, so nothing is lost and nobody waits on an idle moment.

A supervisor from before graceful restart does not know the request. For
one of those, this waits for a moment when it has no turn in flight, stops
it, and starts it again as `--daemon --team-dir <team>`; the team's roster
"supervisor" block says how it is run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import heartbeat

REPO_URL = "https://github.com/roelle/agy-team.git"
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class UpdateError(RuntimeError):
    pass


def _run(cmd, cwd=None, check=True) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise UpdateError(f"{' '.join(map(str, cmd))} failed: "
                          f"{(out.stderr or out.stdout).strip()[:400]}")
    return out.stdout.strip()


def _newest_tag(tags) -> str | None:
    found = [(tuple(map(int, m.groups())), t) for t in tags if (m := _TAG.match(t.strip()))]
    return max(found)[1] if found else None


def installed_version(python: str = sys.executable) -> str:
    """The version a fresh interpreter imports -- not this process's, which
    is whatever was loaded before the update."""
    return _run([python, "-c", "import agyteam; print(agyteam.__version__)"])


def checkout() -> Path | None:
    """The git checkout agyteam is imported from, if it is one."""
    repo = Path(__file__).resolve().parent.parent
    return repo if (repo / ".git").exists() else None


def _pip_source() -> str | None:
    try:
        from importlib import metadata
        raw = metadata.distribution("agyteam").read_text("direct_url.json")
        url = (json.loads(raw) if raw else {}).get("url", "")
    except Exception:
        return None
    return url[4:] if url.startswith("git+") else (url or None)


def plan(to: str | None = None, repo: Path | None = None) -> dict:
    """What an update would do, without doing it."""
    repo = repo or checkout()
    if repo is not None:
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
        _run(["git", "fetch", "--quiet", "--tags"], cwd=repo)
        dirty = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo)
        if branch != "HEAD" and not to:
            upstream = _run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=repo, check=False)
            behind = _run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=repo,
                          check=False) if upstream else ""
            return {"kind": "git", "repo": str(repo), "branch": branch,
                    "upstream": upstream or None, "behind": int(behind or 0),
                    "dirty": bool(dirty)}
        target = to or _newest_tag(_run(["git", "tag", "--list", "v*"], cwd=repo).splitlines())
        current = _run(["git", "describe", "--tags", "--exact-match"], cwd=repo, check=False)
        return {"kind": "git-tag", "repo": str(repo), "current": current or None,
                "target": target, "dirty": bool(dirty)}
    url = _pip_source()
    if url:
        tags = [line.rsplit("refs/tags/", 1)[-1] for line in
                _run(["git", "ls-remote", "--tags", "--refs", url]).splitlines()]
        return {"kind": "pip", "url": url, "target": to or _newest_tag(tags)}
    return {"kind": "unknown"}


def update_code(p: dict) -> str:
    """Bring the installed code to the plan's target. Returns what was done."""
    if p["kind"] in ("git", "git-tag") and p["dirty"]:
        raise UpdateError(f"{p['repo']} has local changes; commit or stash them first "
                          f"(or pass --no-code to only restart)")
    if p["kind"] == "git":
        if not p["upstream"]:
            raise UpdateError(f"branch {p['branch']} in {p['repo']} tracks nothing to pull from")
        if not p["behind"]:
            return f"{p['repo']} is up to date with {p['upstream']}"
        _run(["git", "merge", "--ff-only", "--quiet", "@{u}"], cwd=p["repo"])
        return f"{p['repo']}: fast-forwarded {p['behind']} commit(s) from {p['upstream']}"
    if p["kind"] == "git-tag":
        if not p["target"]:
            raise UpdateError("no release tags to update to")
        if p["current"] == p["target"]:
            return f"{p['repo']} is already at {p['target']}"
        _run(["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", p["target"]],
             cwd=p["repo"])
        return f"{p['repo']}: checked out {p['target']}"
    if p["kind"] == "pip":
        if not p["target"]:
            raise UpdateError(f"no release tags at {p['url']}")
        extra = "[sdk]" if _has_sdk() else ""
        _run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
              f"agyteam{extra} @ git+{p['url']}@{p['target']}"])
        return f"installed agyteam {p['target']} from {p['url']}"
    raise UpdateError("agyteam is installed neither from a git checkout nor from a git "
                      "URL with pip; update it the way you installed it, then run "
                      "`python -m agyteam.update --no-code` to restart the teams")


def _has_sdk() -> bool:
    try:
        import google.antigravity  # noqa: F401
        return True
    except Exception:
        return False


def reinstall_plugin() -> str | None:
    """Re-run plugin/install.sh if the agy CLI plugin is installed from here."""
    repo = checkout()
    dest = Path(os.environ.get("AGYTEAM_PLUGIN_DIR")
                or Path.home() / ".gemini/antigravity-cli/plugins/agy-team")
    if repo is None or not dest.exists() or not (repo / "plugin/install.sh").exists():
        return None
    _run(["bash", str(repo / "plugin/install.sh")], cwd=repo)
    return f"plugin reinstalled at {dest}"


# --- the teams ------------------------------------------------------------------

def teams(roots=None, team_dirs=None) -> list[Path]:
    """Team directories to consider: the ones named, else every <root>/<team>/team."""
    if team_dirs:
        return [Path(t).expanduser().resolve() for t in team_dirs]
    found = []
    for root in roots or [os.environ.get("AGYTEAM_TEAMS_ROOT") or "~/agy-teams"]:
        root = Path(root).expanduser()
        if root.is_dir():
            found += [d / "team" for d in sorted(root.iterdir())
                      if (d / "team" / "roster.json").exists()]
    return found


def restart_team(team_dir: Path, now: bool = False, legacy_wait_s: float = 1800,
                 python: str = sys.executable, log=print) -> dict:
    """Ask one team's supervisor to restart. Returns {"team", "state", ...}."""
    from .supervisor import request_restart
    beat = heartbeat.running(team_dir)
    name = team_dir.parent.name if team_dir.name == "team" else team_dir.name
    if not beat:
        return {"team": name, "state": "not running"}
    if beat.get("restartable"):
        request_restart(team_dir, now=now, by="agyteam.update")
        return {"team": name, "state": "draining", "pid": beat.get("pid"),
                "started": beat.get("started"), "waiting_for": beat.get("inflight") or []}
    return _restart_legacy(team_dir, name, beat, now, legacy_wait_s, python, log)


def _restart_legacy(team_dir, name, beat, now, wait_s, python, log) -> dict:
    from . import lifecycle
    deadline = time.monotonic() + wait_s
    while not now and (beat or {}).get("inflight"):
        if time.monotonic() > deadline:
            return {"team": name, "state": "busy", "pid": beat.get("pid"),
                    "waiting_for": beat.get("inflight"),
                    "note": "an older supervisor with turns in flight the whole time; "
                            "run again with --now to cancel them (their mail goes back)"}
        log(f"  {name}: older supervisor; waiting for a moment with no turn in flight "
            f"({', '.join(beat['inflight'])})")
        time.sleep(5)
        beat = heartbeat.running(team_dir)
    if beat:
        lifecycle.stop_team(reason="restarting for an update", team_dir=team_dir)
        for _ in range(120):
            if not heartbeat.running(team_dir):
                break
            time.sleep(1)
    lifecycle.start_team(team_dir=team_dir)
    pid = start_daemon(team_dir, python)
    return {"team": name, "state": "restarted", "pid": pid, "legacy": True}


def start_daemon(team_dir: Path, python: str = sys.executable) -> int:
    """`python -m agyteam.supervisor --daemon --team-dir <team>`, detached,
    logging to <durable>/supervisor.log as agy-chat's Start does."""
    log_path = (team_dir.parent if team_dir.name == "team" else team_dir) / "supervisor.log"
    env = dict(os.environ, AGYTEAM_TEAM_DIR=str(team_dir))
    env.pop("AGYTEAM_AGENT", None)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen([python, "-m", "agyteam.supervisor", "--daemon",
                                 "--team-dir", str(team_dir)],
                                env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    return proc.pid


def wait_restarted(team_dir: Path, before: dict, timeout_s: float, log=print) -> dict:
    """Wait until the team's supervisor has restarted (a new start time)."""
    name = team_dir.parent.name if team_dir.name == "team" else team_dir.name
    deadline = time.monotonic() + timeout_s
    said = None
    while time.monotonic() < deadline:
        beat = heartbeat.running(team_dir) or {}
        if beat.get("started") and beat.get("started") != before.get("started"):
            return {"team": name, "state": "restarted", "pid": beat.get("pid"),
                    "version": beat.get("version")}
        waiting = tuple(beat.get("inflight") or ())
        if waiting != said:
            log(f"  {name}: draining; waiting for {', '.join(waiting) or 'the restart'}")
            said = waiting
        time.sleep(1)
    return {"team": name, "state": "draining", "note": "still draining; it restarts "
            "when its turns finish"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agyteam.update", description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="say what would change; change nothing")
    ap.add_argument("--to", metavar="vX.Y.Z", help="a release tag instead of the newest")
    ap.add_argument("--no-code", action="store_true", help="do not update; only restart")
    ap.add_argument("--no-restart", action="store_true", help="update; restart nothing")
    ap.add_argument("--now", action="store_true",
                    help="cancel turns in flight instead of waiting (their mail goes back)")
    ap.add_argument("--no-wait", action="store_true",
                    help="ask the supervisors to restart and return; they finish on their own")
    ap.add_argument("--timeout", type=float, default=3600,
                    help="seconds to wait for the teams to restart (default 3600)")
    ap.add_argument("--teams-root", action="append", help="where teams live (default ~/agy-teams)")
    ap.add_argument("--team-dir", action="append", help="a team directory (repeatable)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON at the end")
    args = ap.parse_args(argv)
    log = (lambda *a, **k: print(*a, **k, file=sys.stderr)) if args.json else print
    result: dict = {"before": installed_version()}
    try:
        if not args.no_code:
            p = plan(args.to)
            result["plan"] = p
            if args.check:
                log(json.dumps(p, indent=2) if not args.json else "")
                if args.json:
                    print(json.dumps(result))
                return 0
            result["code"] = update_code(p)
            log(result["code"])
            plugin = reinstall_plugin()
            if plugin:
                result["plugin"] = plugin
                log(plugin)
        result["after"] = installed_version()
        if result["after"] != result["before"]:
            log(f"agyteam {result['before']} -> {result['after']}")
    except UpdateError as e:
        result["error"] = str(e)
        log(f"error: {e}")
        if args.json:
            print(json.dumps(result))
        return 1
    if args.no_restart or args.check:
        if args.json:
            print(json.dumps(result))
        return 0
    found = teams(args.teams_root, args.team_dir)
    before = {td: heartbeat.running(td) or {} for td in found}
    out = []
    for td in found:
        r = restart_team(td, now=args.now, log=log)
        out.append(r)
        if r["state"] != "not running":
            log(f"  {r['team']}: {r['state']}"
                + (f" (waiting for {', '.join(r['waiting_for'])})" if r.get("waiting_for") else ""))
    if not args.no_wait:
        for i, r in enumerate(out):
            if r["state"] == "draining":
                out[i] = wait_restarted(found[i], before[found[i]], args.timeout, log=log)
                log(f"  {out[i]['team']}: {out[i]['state']}"
                    + (f" on {out[i]['version']} (pid {out[i]['pid']})" if out[i].get("version") else ""))
    result["teams"] = out
    if args.json:
        print(json.dumps(result))
    elif not any(r["state"] != "not running" for r in out):
        log("no team had a supervisor running; nothing to restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
