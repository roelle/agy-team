"""Scripted runner for testing the supervisor without spending tokens.

Records every wake and optionally replies over the bus, so the reactive cascade
(A messages B → B is woken → B replies → A is woken) can be verified
deterministically. Written against only the public Runner interface.

Config: {"script": {"agent": [["to", "content"], ...]}, "log": "/path.jsonl",
         "fail": ["agent"], "retro": {"agent": [went_well, did_not, change]}}

## Two columns

`ScriptedRunner` returns prose from `wake()`. So did every other double in
this suite, which is how the retrospective came to depend on reply text while
the Runner contract said in as many words that it must not: no test could
detect the dependency, because no double was ever as limited as a real runner
is allowed to be. The mocks were more capable than the thing they stood in
for.

`MinimalRunner` is the same runner with the optional capability removed. Any
test that passes under ScriptedRunner and fails under MinimalRunner has found
an undeclared capability requirement. See evals/test_runner_columns.py.
"""
import json
import os

from agyteam import retro_store
from agyteam.runner import Runner
from agyteam.transport import load as load_transport


class ScriptedRunner(Runner):
    label = "scripted"

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.script = self.config.get("script", {})
        self.retro_answers = dict(self.config.get("retro", {}))
        self.tool_calls = dict(self.config.get("tool_calls", {}))
        self.fail = set(self.config.get("fail", []))
        self.log_path = self.config.get("log") or os.environ.get("AGYTEAM_RUNNER_LOG")
        self.woken: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"agent": agent, "message": message}) + "\n")
        if agent in self.fail:
            try:
                self.observer.record_failure(agent, self.conversation_id(agent) or "scripted",
                                             f"{agent} blew up on purpose", duration_s=0.01)
            except Exception:
                pass
            raise RuntimeError(f"{agent} blew up on purpose")
        # Each scripted reply is sent once; a runner that re-sent on every wake
        # would manufacture the very loop the hop budget exists to catch.
        sends = self.script.pop(agent, [])
        if sends:
            bus = load_transport(agent)
            for to, content in sends:
                bus.send(to, content)
            bus.close()
        # An agent asked to reflect calls record_retro. Available to both
        # columns because a real agent on either host can do it; the minimal
        # column is the one with no other way to be heard.
        retro_reply = ""
        if "retrospective" in message.lower():
            answers = self.retro_answers.pop(agent, None)
            if answers:
                retro_store.record(self.team_dir, agent, *answers)
                # A rich host also returns the model's prose, which is what
                # made the reply-text dependency invisible for so long. Say it
                # here, so the two columns differ in the way real hosts do.
                retro_reply = retro_store.as_reflection(
                    dict(zip(("went_well", "did_not", "should_change"),
                             answers)))
        for tc in self.tool_calls.pop(agent, []):
            try:
                self.observer.record_tool_call(
                    agent=agent,
                    conversation=self.conversation_id(agent) or "scripted",
                    tool=tc.get("tool", "unknown"),
                    args=tc.get("args"),
                    result=tc.get("result"),
                    error=tc.get("error"),
                    duration_s=tc.get("duration_s", 0.01),
                )
            except Exception:
                pass
        try:
            self.observer.record_turn(agent, self.conversation_id(agent) or "scripted",
                                      duration_s=0.01)
        except Exception:
            pass
        if retro_reply:
            return retro_reply
        return f"{agent} handled {len(message)} chars, sent {len(sends)}"


class MinimalRunner(ScriptedRunner):
    """The weakest runner the contract allows: everything but the transcript.

    Identical behaviour to ScriptedRunner -- same bus traffic, same recorded
    turns -- differing only in what the contract calls optional: it returns ""
    for a completed turn and declares none of the three capability flags.

    That difference is the whole diagnostic. A test that passes above and
    fails here is not a flaky test; it is a dependency on something no runner
    promised, and every one found so far has been a real defect in agyteam
    rather than a shortcoming of the host.
    """
    label = "minimal"
    supports_audit = False
    supports_containment = False
    supports_capability_scoping = False

    def wake(self, agent: str, message: str) -> str:
        super().wake(agent, message)
        # Not "[error: ...]": the turn succeeded. Returning an error string
        # here would have the supervisor record a failure and the retry
        # wrapper re-run a turn that already completed.
        return ""


#: The eval matrix. Parameterise anything that drives agents over both.
COLUMNS = {"rich": ScriptedRunner, "minimal": MinimalRunner}
