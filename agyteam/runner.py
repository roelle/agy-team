"""Pluggable agent runner — *how* an agent is woken to do work.

Third seam, same pattern as agyteam/transport.py (how messages move) and
agyteam/memory.py (where knowledge lives). These are orthogonal: you can run
agents through the agy CLI while messages travel over a custom bus and memory
lives in a shared database, changing any one without touching the others.

    AGYTEAM_RUNNER=agyteam.runner_agy:AgyRunner        # default
    AGYTEAM_RUNNER=agyteam.runner_sdk:SdkRunner        # where the SDK is installed
    AGYTEAM_RUNNER_CONFIG='{"binary": "agy"}'          # optional, JSON

Why this exists: without it, delivery is passive — mail sits in an inbox until
somebody thinks to check. A runner lets the supervisor *wake* an agent when work
arrives, so a message from a teammate causes work the same way a message from
the user does. That is the difference between a team and a shared mailbox.

Implement `wake`. See runner_template docs in the class below.
"""
import importlib
import json
import os
import random
import time
from abc import ABC, abstractmethod

# The default must be the runner that works everywhere. runner_sdk needs
# google-antigravity, which the plugin deliberately excludes to stay
# standard-library-only, so defaulting to it leaves a fresh install unable to
# load any runner at all. SdkRunner is better where its dependency exists —
# opt in with AGYTEAM_RUNNER, or per agent via runner_mixed.
# Transient provider failures, by the text they arrive as. A capacity spike on
# the model provider killed two unattended runs in ten minutes: the SDK retried
# twice, gave up, and the supervisor logged it and moved on -- losing a run with
# real state in it. Retrying here costs a sleep; not retrying costs the episode.
RETRYABLE = ("503", "unavailable", "high demand", "429", "resource_exhausted",
             "rate limit", "timeout", "temporarily")
RETRY_ATTEMPTS = int(os.environ.get("AGYTEAM_RETRY_ATTEMPTS", 4))
RETRY_BASE_SECONDS = float(os.environ.get("AGYTEAM_RETRY_BASE", 5))


def is_retryable(text: str) -> bool:
    """True if this failure is worth waiting out rather than reporting."""
    low = (text or "").lower()
    # A model that does not exist is not a capacity problem, and retrying it
    # four times just spends four times as long being wrong.
    if "not found" in low or "not supported" in low or "api key" in low:
        return False
    return any(k in low for k in RETRYABLE)


def with_retry(fn, *, attempts: int | None = None, on_wait=None):
    """Run fn(), retrying transient provider failures with backoff.

    fn returns a result; a result that is a string starting with "[error:" is
    treated as a failure, since that is how runners report rather than raise.
    """
    attempts = attempts or RETRY_ATTEMPTS
    last = None
    for i in range(attempts):
        try:
            out = fn()
            if not (isinstance(out, str) and out.startswith("[error:")
                    and is_retryable(out)):
                return out
            last = out
        except Exception as e:              # noqa: BLE001 - runners must not raise
            if not is_retryable(str(e)):
                raise
            last = f"[error: {type(e).__name__}: {e}]"
        if i < attempts - 1:
            # Jittered exponential backoff: a capacity spike hits every agent at
            # once, and synchronised retries reproduce the spike.
            delay = RETRY_BASE_SECONDS * (2 ** i) * (0.5 + random.random())
            if on_wait:
                on_wait(i + 1, delay, last)
            time.sleep(delay)
    return last


DEFAULT_RUNNER = "agyteam.runner_agy:AgyRunner"


class Runner(ABC):
    """Starts or resumes one agent turn with a message it must act on.

    ## The completion contract

    A runner must be able to detect that a turn **ended**. It need not be able
    to read what the agent said. That is a far weaker requirement than it
    looks, and stating it is deliberate: a host that exposes only
    fire-and-forget verbs and returns no transcript can still satisfy it with
    a lifecycle hook, a status file, a queue message, or a line appended to a
    log. Agents publish to teammates and to the user over the bus, not through
    this return value, so a runner that can only report "done" loses nothing
    the rest of the system depends on. See `wake` for what to return when no
    reply text exists.

    That last sentence was false for one caller for some time, and the cost of
    a contract nobody enforces is worth stating once: `retro()` parsed this
    return value for its three sections, so on a runner honouring the contract
    exactly, a full retrospective produced a report reading "(missing or
    invalid)" and never wrote NORMS.md -- team-level learning silently did not
    happen, while agent-level learning worked because distillation measures
    files on disk. Retro answers now go to disk too (`agyteam/retro_store.py`,
    the `record_retro` tool). Anything else that grows a dependency on reply
    text is a bug in that caller, not a requirement on your runner.

    ## Capability flags

    Three properties this project documents as invariants are provided by the
    *runner*, not by the core. A runner that cannot provide one must say so
    rather than let an operator assume otherwise. All default to False,
    because the honest default for "can you do this?" is no.

    The point is not to rank runners. It is that a check which could not run
    must never be reported as a check that found nothing -- the same rule this
    project already applies to test files that collect no tests and to a bench
    that cannot be verified. A silent zero and a real zero must not look alike.
    """

    #: shown in supervisor output so you can tell which runner is live
    label = "runner"

    #: True if this runner records every tool call to AGYTEAM_AUDIT_LOG, in a
    #: location agents cannot reach. Grading, succession scoring and every
    #: "did they actually check?" question read that log against the bus.
    #: When False, those tools must report that evidence was not OBSERVABLE --
    #: never that the team produced none.
    supports_audit = False

    #: True if this runner confines agents to granted workspace directories.
    #: When False, containment is whatever the underlying host enforces, which
    #: agyteam neither sets nor can observe: roster `workspaces` and
    #: AGYTEAM_EXTRA_WORKSPACES have no effect. Anything that depends on an
    #: agent being UNABLE to read a path -- answer keys, another team's
    #: memories -- needs a different mechanism on such a runner. Containment
    #: belongs to whoever owns the process; agyteam should say so plainly
    #: rather than imply a guarantee it is not making.
    supports_containment = False

    #: True if this runner enforces roster capability scoping -- `tools_off`
    #: and `workers` -- by withholding the tool schema from the model. This is
    #: the load-bearing half of "roles are enforced by capability, not
    #: instruction", and it was undeclared while the introspection server told
    #: every agent its `tools_off` list as settled fact. Measured: a manager
    #: whose roster entry read tools_off: [create_file, edit_file] and "Does
    #: not write the work" ran `sed -i` on the fixture it was auditing. Prose
    #: that says "please don't" decays under deadline pressure; a schema the
    #: model never sees cannot be reached for. On a runner where nothing
    #: withholds it, the roster entry is a statement of intent, and anything
    #: reporting it must say which of the two it is.
    supports_capability_scoping = False

    def __init__(self, config: dict | None = None, observer=None):
        self.config = config or {}
        self._observer = observer

    @property
    def observer(self):
        if self._observer is None:
            from . import observer as observer_lib
            self._observer = observer_lib.load()
        return self._observer

    @observer.setter
    def observer(self, value):
        self._observer = value

    # --- conversation continuity, shared by every runner -------------------
    #
    # An agent keeps one conversation, and which one is recorded in
    # <team_dir>/conversations.json so a human can walk into it
    # (`python -m agyteam.session <agent>`). This lives on the base class
    # because both runners need it and two copies would drift: the SDK runner
    # once wrote its sessions into the CLI's conversation store without
    # recording their ids, so they existed but nobody could find them.

    @property
    def team_dir(self):
        from pathlib import Path
        env = os.environ.get("AGYTEAM_TEAM_DIR")
        if env:
            return Path(env)
        from . import scope
        return scope.load().team_dir()

    @property
    def conversations_path(self):
        return self.team_dir / "conversations.json"

    def conversations(self) -> dict:
        try:
            return json.loads(self.conversations_path.read_text())
        except (OSError, ValueError):
            return {}

    def conversation_id(self, agent: str) -> str | None:
        return self.conversations().get(agent)

    def remember_conversation(self, agent: str, conv_id: str) -> None:
        if not conv_id:
            return
        convs = self.conversations()
        if convs.get(agent) == conv_id:
            return
        convs[agent] = conv_id
        self.conversations_path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations_path.write_text(
            json.dumps(convs, indent=2, sort_keys=True))

    @property
    def retired_path(self):
        return self.team_dir / "conversations_retired.jsonl"

    def reset(self, agent: str | None = None) -> None:
        """Forget stored conversations so the next wake starts fresh.

        The id is retired, not discarded. Dropping it is what `--cycle-all`
        did: conversations.json was observed shrinking from five agents to two
        to one across a single run, after which `python -m agyteam.session
        <agent>` found nobody -- which is precisely the failure the base class
        exists to prevent ("the SDK runner once wrote its sessions into the
        CLI's conversation store without recording their ids, so they existed
        but nobody could find them"). Anything that reverse-maps a
        conversation id back to an agent -- audit attribution, containment --
        fails open for a cycled agent otherwise, silently.
        """
        convs = self.conversations()
        retiring = (list(convs.items()) if agent is None
                    else [(agent, convs[agent])] if agent in convs else [])
        if retiring:
            try:
                self.retired_path.parent.mkdir(parents=True, exist_ok=True)
                with self.retired_path.open("a", encoding="utf-8") as f:
                    for name, cid in retiring:
                        f.write(json.dumps({
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "agent": name, "conversation": cid}) + "\n")
            except OSError:
                pass            # bookkeeping must not block the reset
        convs = {} if agent is None else {k: v for k, v in convs.items()
                                          if k != agent}
        self.conversations_path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations_path.write_text(
            json.dumps(convs, indent=2, sort_keys=True))

    def retired_conversations(self) -> list[dict]:
        """Every conversation this team has cycled, oldest first."""
        try:
            text = self.retired_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def agent_for_conversation(self, conv_id: str) -> str | None:
        """Which agent a conversation id belongs to, live or retired.

        Attribution must survive a cycle. A host that hands back a
        conversation id and asks whose it is gets an answer for an agent that
        was cycled an hour ago, rather than None and a guess.
        """
        if not conv_id:
            return None
        for agent, cid in self.conversations().items():
            if cid == conv_id:
                return agent
        for rec in reversed(self.retired_conversations()):
            if rec.get("conversation") == conv_id:
                return rec.get("agent")
        return None

    def recycle_agent(self, agent: str, reason: str = "") -> None:
        """Explicitly terminate and recycle an agent session. Default calls reset."""
        self.reset(agent)

    def sync_roster(self, roster_doc: dict | None = None) -> None:
        """Sync running agent configurations with an updated roster document. Default is a no-op."""

    @abstractmethod
    def wake(self, agent: str, message: str) -> str:
        """Run `agent` against `message` and return whatever it said.

        Blocks until the turn finishes. **Detecting completion is the
        requirement; reading the reply is not.** The returned text is for
        logging only — anything the agent wants a teammate or the user to see,
        it sends over the bus itself, which is what lets one wake cascade into
        the next.

        A runner on a host that returns no transcript should return an empty
        string once the turn has genuinely ended. Do not invent a reply, and
        do not return "[error: ...]" for a turn that merely produced no
        readable text: that string means failure, the supervisor records it as
        one, and `with_retry` may re-run a turn that already succeeded.

        Errors should be returned as text starting with "[error:", not raised;
        a supervisor must survive one agent failing.
        """

    def close(self) -> None:
        """Release resources (sessions, subprocesses). Default is a no-op."""
        if getattr(self, "_observer", None) is not None:
            try:
                self._observer.close()
            except Exception:
                pass


CAPABILITIES = ("supports_audit", "supports_containment",
                "supports_capability_scoping")


def capabilities(spec: str | None = None) -> dict:
    """What the configured runner declares, without constructing it.

    Returns each flag as True, False, or None for "could not tell" -- the
    third state is the point. The obvious version of this, calling load(spec)
    inside `except Exception`, does not survive contact with the loader:
    load() signals every misconfiguration with SystemExit, which is a
    BaseException and passes straight through. A grader that wrapped it that
    way exited 1 with no score at all when AGYTEAM_RUNNER named a runner whose
    constructor complained -- turning a tool that reports blindness honestly
    into one that refuses to grade, which is a worse failure than the one it
    was written to fix.

    Reading the class attribute also avoids building a client, opening a
    session or requiring an API key to answer a question about a declaration.
    """
    spec = spec or os.environ.get("AGYTEAM_RUNNER") or DEFAULT_RUNNER
    out = {"spec": spec, "error": None}
    out.update({flag: None for flag in CAPABILITIES})
    try:
        if ":" not in spec:
            raise ValueError(f"expected 'module:Class', got {spec!r}")
        mod_name, _, cls_name = spec.partition(":")
        cls = getattr(importlib.import_module(mod_name), cls_name)
        if not isinstance(cls, type) or not issubclass(cls, Runner):
            raise TypeError(f"{spec} is not an agyteam.runner.Runner subclass")
        for flag in CAPABILITIES:
            value = getattr(cls, flag, None)
            # A property object on the class (MixedRunner computes its flags
            # per agent) cannot be read without an instance. Unknown, not False.
            out[flag] = bool(value) if isinstance(value, bool) else None
    except BaseException as e:      # noqa: BLE001 - SystemExit included, deliberately
        out["error"] = f"could not inspect runner {spec!r}: {type(e).__name__}: {e}"
    return out


def load(spec: str | None = None, config: dict | None = None) -> Runner:
    """Instantiate the configured runner. Fails loudly on misconfiguration."""
    spec = spec or os.environ.get("AGYTEAM_RUNNER") or DEFAULT_RUNNER
    if config is None:
        raw = os.environ.get("AGYTEAM_RUNNER_CONFIG", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"AGYTEAM_RUNNER_CONFIG is not valid JSON: {e}")
    if ":" not in spec:
        raise SystemExit(f"AGYTEAM_RUNNER must be 'module:Class', got {spec!r}")
    mod_name, _, cls_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"cannot load runner {spec!r}: {e}")
    if not issubclass(cls, Runner):
        raise SystemExit(f"{spec} is not an agyteam.runner.Runner subclass")
    return cls(config)
