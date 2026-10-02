"""Runner for a host that owns the agent loop: start, deliver, and a stop hook.

The two shipped runners either own the model loop (runner_sdk) or drive a CLI
whose turn is a subprocess that returns when the turn does (runner_agy). A
hosted IDE or agent runtime offers neither. What it offers is narrower, and
the same everywhere we have looked: a command that *starts* a conversation, a
command that *delivers* a message into one, and a hook the host runs when a
turn ends. The reply text is not retrievable, and nothing blocks. A team ran
for three weeks on a 462-line runner written against exactly that surface,
and every supervisor fix had to be carried on top of it. This is that runner,
upstream, configured rather than written:

    AGYTEAM_RUNNER=agyteam.runner_host:HostRunner
    AGYTEAM_RUNNER_CONFIG='{
      "start":   ["host", "new", "--agent", "{agent}", "--model", "{model}", "--prompt", "{prompt}"],
      "deliver": ["host", "send", "--conversation", "{conversation}", "--message", "{message}"],
      "cancel":  ["host", "cancel", "{conversation}"],
      "start_id_path": "conversation_id",
      "signal_dir": "~/host/turn-signals",
      "timeout": 900,
      "model_map": {"coder": "fast-model", "*": "default-model"}
    }'

`timeout` bounds a turn, the primer turn included -- a host that initialises
its tool servers synchronously spends that on the first turn. `start_timeout`
(default 120 s) bounds only the start/deliver/cancel commands themselves.

`start` must print JSON (or, with "start_id_path": "stdout", just the id); the
conversation id is read from it at `start_id_path`, a dotted path. `deliver`
takes the message as its own argv element, never interpolated into a shell
string -- or on stdin, with "deliver_stdin": true, for hosts that cap
argument length. The host's stop hook appends one line to
<signal_dir>/<conversation>.jsonl when a turn ends; a line that is JSON may
carry "error", and token counts ("input_tokens", "output_tokens",
"total_tokens", "cache_read_tokens"), which are recorded when present.

## Two-phase start

A conversation is created with a content-free primer, registered in
conversations.json, and only then given the brief and the first message. The
turn that carries the brief is the longest an agent ever takes, and anything
keyed on "which agent is this conversation" -- an audit hook, a policy, a
grant -- can only bind once the id is known. Started with the real message,
that whole first turn ran unattributed. A host that cannot start a
conversation without a prompt gets the primer as that prompt.

## Completion is a count, never a wait on a marker

The turn is over when the signal file has more lines than it had when the
message was delivered. Not "when the file appears" and not delete-then-wait:
a previous turn's line can land after the delete, and the runner would then
schedule into an agent still working.

## Capabilities

All False unless the configuration says otherwise, under "capabilities".
This runner cannot see the host's tool calls, confine its agents, or withhold
a tool schema; a host that does any of that through its own hooks can say so
here, and nothing downstream will assume it otherwise.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from .runner import Runner

PRIMER = ("This session was created by the team supervisor. Nothing is "
          "being asked of you yet; your brief and first task follow.")


class HostRunner(Runner):
    label = "host"
    resumable = True

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        c = self.config
        self.start_argv = list(c.get("start") or [])
        self.deliver_argv = list(c.get("deliver") or [])
        self.cancel_argv = list(c.get("cancel") or [])
        self.deliver_stdin = bool(c.get("deliver_stdin", False))
        self.start_id_path = c.get("start_id_path", "conversation_id")
        self.signal_dir = Path(c["signal_dir"]).expanduser() if c.get("signal_dir") else None
        self.signal_suffix = c.get("signal_suffix", ".jsonl")
        self.timeout = float(c.get("timeout", 900))
        self.start_timeout = float(c.get("start_timeout", 120))
        self.poll_seconds = float(c.get("poll_seconds", 0.5))
        self.primer = c.get("primer") or PRIMER
        self.model_map = dict(c.get("model_map") or {})
        self.extra_env = dict(c.get("env") or {})
        caps = c.get("capabilities") or {}
        self.supports_audit = bool(caps.get("supports_audit", False))
        self.supports_containment = bool(caps.get("supports_containment", False))
        self.supports_capability_scoping = bool(caps.get("supports_capability_scoping", False))
        self.inbox_pull = bool(c.get("inbox_pull", False))
        # Signal lines accounted for, per conversation. Persisted, so a
        # restart knows where it left off: the first version started empty
        # and the first begin() after a restart read every historical line
        # as a turn the operator had just taken -- 442 phantom turn events
        # in one restart, all stamped with the current time, which the
        # review gate then counted as the author having worked. A
        # conversation seen for the first time with no watermark is seeded
        # at its current length: what happened before anyone was watching
        # is history, not news.
        self._seen: dict[str, int] = self._load_seen()
        missing = [k for k, v in (("start", self.start_argv), ("deliver", self.deliver_argv),
                                  ("signal_dir", self.signal_dir)) if not v]
        if missing:
            raise SystemExit(f"runner_host: AGYTEAM_RUNNER_CONFIG is missing "
                             f"{', '.join(missing)}; see the module docstring")

    # --- host-facing -------------------------------------------------------

    def _spec(self, agent: str) -> dict:
        from . import roster as roster_lib
        try:
            for a in roster_lib.load(self.team_dir / "roster.json")["agents"]:
                if a["name"] == agent:
                    return a
        except Exception:
            pass
        return {}

    def model_for(self, agent: str) -> str:
        """Roster first, then the config's model_map, then its "*" default."""
        return (self._spec(agent).get("model") or self.model_map.get(agent)
                or self.model_map.get("*") or "")

    def _brief(self, agent: str) -> str:
        from . import persona
        from . import roster as roster_lib
        try:
            agents = roster_lib.load(self.team_dir / "roster.json")["agents"]
        except Exception:
            agents = [{"name": agent, "role": ""}]
        shared = None
        try:
            from . import scope
            shared = scope.load().shared_dir()
        except Exception:
            pass
        return persona.brief(agent, agents, shared, team_dir=self.team_dir)

    def _fill(self, argv: list, **values) -> list:
        """Substitute placeholders element by element. Never a shell string."""
        out = []
        for a in argv:
            try:
                out.append(a.format(**values))
            except (KeyError, IndexError):
                out.append(a)
        return out

    def _env(self, agent: str) -> dict:
        return {**os.environ, **self.extra_env, "AGYTEAM_AGENT": agent,
                "AGYTEAM_TEAM_DIR": str(self.team_dir)}

    @property
    def _seen_path(self) -> Path:
        return self.team_dir / "host_signals.json"

    def _load_seen(self) -> dict[str, int]:
        try:
            doc = json.loads(self._seen_path.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in doc.items()} if isinstance(doc, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_seen(self) -> None:
        try:
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            self._seen_path.write_text(json.dumps(self._seen, indent=2, sort_keys=True),
                                       encoding="utf-8")
        except OSError:
            pass

    def _watermark(self, conv: str, current: int) -> int:
        """Where counting starts for `conv`: the saved watermark, or -- on
        first sight -- the file as it is now."""
        if conv not in self._seen:
            self._seen[conv] = current
            self._save_seen()
        return self._seen[conv]

    def signal_path(self, conversation: str) -> Path:
        return self.signal_dir / f"{conversation}{self.signal_suffix}"

    @staticmethod
    def _lines(path: Path) -> list[str]:
        try:
            return [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if l.strip()]
        except OSError:
            return []

    def _read_id(self, stdout: str) -> str:
        text = stdout.strip()
        if self.start_id_path == "stdout":
            return text
        if self.start_id_path == "last_line":
            return text.splitlines()[-1].strip() if text else ""
        try:
            doc = json.loads(text)
        except ValueError:
            # The id may be on the last JSON line of chatty output.
            doc = None
            for line in reversed(text.splitlines()):
                try:
                    doc = json.loads(line)
                    break
                except ValueError:
                    continue
            if doc is None:
                return ""
        for key in self.start_id_path.split("."):
            if isinstance(doc, dict):
                doc = doc.get(key)
            else:
                return ""
        return str(doc) if doc else ""

    def _start(self, agent: str) -> str:
        argv = self._fill(self.start_argv, agent=agent, model=self.model_for(agent),
                          prompt=self.primer)
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=self.start_timeout, env=self._env(agent))
        if r.returncode != 0:
            raise RuntimeError(f"start exited {r.returncode}: {(r.stderr or '').strip()[:300]}")
        conv = self._read_id(r.stdout or "")
        if not conv:
            raise RuntimeError(f"start printed no conversation id at "
                               f"{self.start_id_path!r}: {(r.stdout or '').strip()[:200]!r}")
        return conv

    def _deliver(self, agent: str, conversation: str, message: str) -> None:
        values = dict(agent=agent, conversation=conversation,
                      model=self.model_for(agent),
                      message="" if self.deliver_stdin else message)
        argv = self._fill(self.deliver_argv, **values)
        r = subprocess.run(argv, input=message if self.deliver_stdin else None,
                           capture_output=True, text=True, timeout=self.start_timeout,
                           env=self._env(agent))
        if r.returncode != 0:
            raise RuntimeError(f"deliver exited {r.returncode}: {(r.stderr or '').strip()[:300]}")

    def _cancel(self, agent: str, conversation: str) -> None:
        if not self.cancel_argv:
            return
        try:
            subprocess.run(self._fill(self.cancel_argv, agent=agent, conversation=conversation),
                           capture_output=True, text=True, timeout=self.start_timeout,
                           env=self._env(agent))
        except (OSError, subprocess.SubprocessError):
            pass

    def _wait_lines(self, path: Path, more_than: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self._lines(path)) > more_than:
                return True
            time.sleep(self.poll_seconds)
        return False

    # --- the turn ---------------------------------------------------------

    def begin(self, agent: str, message: str) -> dict:
        t0 = time.monotonic()
        handle = {"agent": agent, "started": time.time(), "timeout": self.timeout}
        try:
            conv = self.conversation_id(agent)
            if conv is None:
                conv = self._start(agent)
                # Register, then deliver: see "Two-phase start".
                self.remember_conversation(agent, conv)
                signal = self.signal_path(conv)
                # The turn's own timeout, not start_timeout: a host that
                # initialises its tool servers synchronously spends that on
                # the first turn, and start_timeout bounds commands, not turns.
                if not self._wait_lines(signal, 0, self.timeout):
                    raise RuntimeError("the primer turn never ended; is the stop "
                                       f"hook writing to {signal}?")
                self._seen[conv] = len(self._lines(signal))
                self._save_seen()
                message = f"{self._brief(agent)}\n\n---\n\n{message}"
            signal = self.signal_path(conv)
            before = len(self._lines(signal))
            self._watermark(conv, before)
            self._note_untracked(agent, conv, before)
            self._deliver(agent, conv, message)
        except Exception as e:                      # noqa: BLE001 - report, never raise
            handle["error"] = f"[error: {agent} failed: {type(e).__name__}: {e}]"
            handle["raised"] = repr(e)
            return handle
        handle.update(conversation=conv, signal=str(signal), before=before)
        return handle

    def poll(self, handle: dict) -> str | None:
        if handle.get("error"):
            return handle["error"]
        agent, conv = handle["agent"], handle["conversation"]
        lines = self._lines(Path(handle["signal"]))
        if len(lines) > handle["before"] + 1:
            # More turns ended than this one: the operator took turns in the
            # host's own UI. They are on the record as the host's, not ours.
            self._note_untracked(agent, conv, len(lines) - 1, after=handle["before"])
        if len(lines) <= handle["before"]:
            if time.time() - handle["started"] < handle.get("timeout", self.timeout):
                return None
            # Cancel on timeout: a turn that outlives its clock kept executing
            # and overwrote another agent's files.
            self._cancel(agent, conv)
            err = f"[error: {agent} timed out after {handle.get('timeout', self.timeout):.0f}s; cancel sent]"
            self._record_failure(agent, conv, err, handle)
            return err
        dur = time.time() - handle["started"]
        self._seen[conv] = len(lines)
        self._save_seen()
        last = lines[-1]
        tokens = {}
        try:
            doc = json.loads(last)
            if isinstance(doc, dict):
                if doc.get("error"):
                    err = f"[error: {agent} failed: {doc['error']}]"
                    self._record_failure(agent, conv, err, handle)
                    return err
                tokens = {k: doc.get(k) for k in
                          ("input_tokens", "output_tokens", "cache_read_tokens", "total_tokens")
                          if doc.get(k) is not None}
        except ValueError:
            pass
        try:
            self.observer.record_turn(agent=agent, conversation=conv, duration_s=dur,
                                      model=self.model_for(agent) or None,
                                      **tokens, **self.meta_for(agent))
        except Exception:
            pass
        return ""           # no transcript on this host; the turn ended

    def _note_untracked(self, agent: str, conv: str, count: int,
                        after: int | None = None) -> None:
        """Record turns that ended without this runner starting them.

        The stop hook fires for every turn, including ones the operator
        starts in the host's UI; before this they left no trace in
        events.jsonl, so an agent could do an afternoon's work the record
        never saw. Each extra signal line becomes a turn event marked
        started_by "host".
        """
        seen = self._watermark(conv, count) if after is None else after
        for i in range(seen, count):
            try:
                self.observer.record_turn(agent=agent, conversation=conv,
                                          duration_s=None, started_by="host",
                                          signal_line=i + 1)
            except Exception:
                pass
        if count > self._seen.get(conv, 0):
            self._seen[conv] = count
            self._save_seen()

    def _record_failure(self, agent, conv, err, handle):
        try:
            self.observer.record_failure(agent, conv or "", err,
                                         duration_s=time.time() - handle["started"])
        except Exception:
            pass

    def cancel(self, handle: dict) -> None:
        if handle.get("conversation"):
            self._cancel(handle["agent"], handle["conversation"])

    def wait_turn(self, timeout: float) -> None:
        time.sleep(min(timeout, self.poll_seconds))

    def wake(self, agent: str, message: str) -> str:
        """begin() then poll() until the turn ends -- the blocking form."""
        handle = self.begin(agent, message)
        while True:
            reply = self.poll(handle)
            if reply is not None:
                return reply
            time.sleep(self.poll_seconds)
