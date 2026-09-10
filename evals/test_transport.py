"""Transport-agnostic contract suite for the A2A bus.

Runs the identical scenario against every configured transport, so a new
implementation (native, internal, proprietary) can be validated before it is
trusted. This is the suite agyteam/transport_template.py tells you to run.

  .venv/bin/python evals/test_transport.py
  AGYTEAM_BUS_TRANSPORT=example_transport:MyTransport \
  AGYTEAM_BUS_CONFIG='{"endpoint":"..."}' .venv/bin/python evals/test_transport.py

With no arguments it tests the built-in file transport plus an independent
SQLite fixture — if both pass, the seam is real and not file-specific.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from rpc_util import ROOT, check, rpc, text_of, tool_names

ROSTER = [{"name": "tpm", "role": "coordinates"},
          {"name": "coder", "role": "implements"},
          {"name": "syseng", "role": "verifies"}]


def contract(label: str, env_for) -> tuple[int, int]:
    """env_for(agent, admin) -> env dict selecting the transport for that agent."""
    print(f"\n== transport contract: {label} ==")

    def call(agent, calls, admin=False):
        return rpc("agyteam.mcp_bus", [], calls, env=env_for(agent, admin))

    a = call("tpm", [("list_teammates", {}),
                     ("send_to_teammate", {"to": "coder", "content": "build X"}),
                     ("send_to_teammate", {"to": "ghost", "content": "hi"}),
                     ("check_inbox", {})])
    a_tools, a_res = tool_names(a[1]), a[2:]

    b = call("coder", [("check_inbox", {}), ("check_inbox", {})])[2:]
    c = call("syseng", [("broadcast", {"content": "standup"})])[2:]
    d = call("tpm", [("check_inbox", {})])[2:]
    adm = call("tpm", [("roster_add", {"name": "qa", "role": "reviews"}),
                       ("roster_add", {"name": "qa", "role": "dup"}),
                       ("roster_remove", {"name": "qa"})], admin=True)
    adm_tools, adm_res = tool_names(adm[1]), adm[2:]
    # The user is a real recipient, not a log line: the supervisor decides an
    # episode is finished by seeing that the user was answered, so a transport
    # that drops these has a team that never stops talking.
    u = call("tpm", [("send_to_teammate", {"to": "user", "content": "done: X"})])[2:]
    u_read = call("user", [("check_inbox", {})])[2:]

    return sum([
        check("teammates listed, self excluded, user included",
              "coder" in text_of(a_res[0]) and "tpm" not in text_of(a_res[0])
              and "user" in text_of(a_res[0]), text_of(a_res[0])),
        check("send to peer reports delivery",
              "delivered to coder" in text_of(a_res[1]), text_of(a_res[1])),
        check("unknown recipient errors instead of vanishing",
              text_of(a_res[2]).startswith("[error:"), text_of(a_res[2])),
        check("sender's own inbox unaffected by sending",
              text_of(a_res[3]) == "[inbox empty]", text_of(a_res[3])),
        check("recipient gets it in a separate process",
              "build X" in text_of(b[0]) and "from tpm" in text_of(b[0]),
              text_of(b[0])),
        check("messages consumed once (no redelivery loop)",
              text_of(b[1]) == "[inbox empty]", text_of(b[1])),
        check("broadcast reports its recipients",
              "tpm" in text_of(c[0]) and "coder" in text_of(c[0]), text_of(c[0])),
        check("broadcast excludes the sender",
              "syseng" not in text_of(c[0]), text_of(c[0])),
        check("broadcast actually lands in an inbox",
              "standup" in text_of(d[0]), text_of(d[0])),
        check("roster tools hidden without admin flag",
              "roster_add" not in a_tools, str(a_tools)),
        check("roster tools present with admin flag",
              "roster_add" in adm_tools, str(adm_tools)),
        check("roster add / duplicate-reject / remove all behave",
              "added 'qa'" in text_of(adm_res[0])
              and "already on the roster" in text_of(adm_res[1])
              and "removed 'qa'" in text_of(adm_res[2]),
              " | ".join(text_of(r) for r in adm_res)),
        check("send to user reports delivery, not a shrug",
              "delivered to the user" in text_of(u[0]), text_of(u[0])),
        check("the user's answer is retrievable, not just logged",
              "done: X" in text_of(u_read[0]), text_of(u_read[0])),
    ]), 14


def file_env():
    team = ROOT / "evals" / "team_contract"
    shutil.rmtree(team, ignore_errors=True)
    (team / "inbox").mkdir(parents=True)
    (team / "roster.json").write_text(json.dumps({"agents": ROSTER}))

    def env_for(agent, admin):
        e = {"AGYTEAM_AGENT": agent, "AGYTEAM_TEAM_DIR": str(team),
             "AGYTEAM_BUS_TRANSPORT": "agyteam.transport_file:FileTransport"}
        if admin:
            e["AGYTEAM_ROSTER_ADMIN"] = "1"
        return e
    return env_for


def sqlite_env():
    db = Path(tempfile.mkdtemp(prefix="agyteam-fixture-")) / "bus.db"
    cfg = json.dumps({"db": str(db), "roster": ROSTER})

    def env_for(agent, admin):
        e = {"AGYTEAM_AGENT": agent, "AGYTEAM_BUS_CONFIG": cfg,
             "AGYTEAM_BUS_TRANSPORT": "fixture_transport:SqliteTransport"}
        if admin:
            e["AGYTEAM_ROSTER_ADMIN"] = "1"
        return e
    return env_for


def test_loader_failures() -> tuple[int, int]:
    """A misconfigured transport must fail loudly, never silently fall back."""
    print("\n== loader safety ==")
    import subprocess
    from rpc_util import PY

    def run(env):
        p = subprocess.run([str(PY), "-m", "agyteam.mcp_bus"], cwd=ROOT,
                           input="", capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PYTHONPATH": str(ROOT),
                                "AGYTEAM_AGENT": "tpm", **env})
        return p.returncode, (p.stderr or "") + (p.stdout or "")

    rc1, o1 = run({"AGYTEAM_BUS_TRANSPORT": "nosuchmodule:Thing"})
    rc2, o2 = run({"AGYTEAM_BUS_TRANSPORT": "agyteam.transport_file"})
    rc3, o3 = run({"AGYTEAM_BUS_TRANSPORT": "agyteam.transport_file:FileTransport",
                   "AGYTEAM_BUS_CONFIG": "{not json"})
    rc4, o4 = run({"AGYTEAM_BUS_TRANSPORT": "agyteam.scope:Scopes"})
    return sum([
        check("missing module fails loudly", rc1 != 0 and "cannot load" in o1, o1),
        check("malformed spec rejected", rc2 != 0 and "module:Class" in o2, o2),
        check("bad config JSON rejected", rc3 != 0 and "not valid JSON" in o3, o3),
        check("non-Transport class rejected", rc4 != 0 and "not a" in o4, o4),
    ]), 4


if __name__ == "__main__":
    spec = os.environ.get("AGYTEAM_BUS_TRANSPORT")
    if spec:
        cfg = os.environ.get("AGYTEAM_BUS_CONFIG", "")
        def custom(agent, admin):
            e = {"AGYTEAM_AGENT": agent, "AGYTEAM_BUS_TRANSPORT": spec,
                 "AGYTEAM_BUS_CONFIG": cfg}
            if admin:
                e["AGYTEAM_ROSTER_ADMIN"] = "1"
            return e
        runs = [contract(spec, custom)]
    else:
        runs = [contract("file (default)", file_env()),
                contract("sqlite (independent fixture)", sqlite_env()),
                test_loader_failures()]
    got, want = sum(s for s, _ in runs), sum(t for _, t in runs)
    print(f"\n== transport contract: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
