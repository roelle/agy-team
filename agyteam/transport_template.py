"""Skeleton for a native/internal A2A transport. Copy, rename, fill in.

This is the *only* file you should need to write to move agyteam off the file
bus and onto a real peer-messaging system. Nothing else in agyteam changes, and
the agents' tools keep the same names and semantics.

    cp agyteam/transport_template.py example_transport.py
    # implement the three methods
    export AGYTEAM_BUS_TRANSPORT=example_transport:MyTransport
    export AGYTEAM_BUS_CONFIG='{"endpoint": "..."}'      # optional
    .venv/bin/python evals/test_transport.py     # 15 checks per transport

Run that suite before trusting it — it is transport-agnostic and checks the
contract (delivery, no redelivery, unknown recipients, isolation between
agents) that the rest of the system relies on. It runs those 15 checks against
your transport and against the file transport, so the headline total is 34;
what matters for yours is that none of its 15 fail.

## What this contract cannot express

Every check here is pull-shaped: the suite sends, then calls fetch/peek and
inspects what came back. A transport that delivers out of band — pushing
straight into a live agent session — can satisfy all 15 and still break the
system, because a delivery the supervisor never saw is a delivery outside its
hop budget, its stop-on-answer condition, its failure isolation and its
requeue path. The contract has no way to observe that. This is a known gap,
documented rather than fixed: see "Push instead of polling" in the README. If
you push, you are taking over the supervisor's scheduling role, and those four
guarantees become yours to reimplement.
"""
# Relative import, because this file ships inside the package. The moment you
# copy it out to example_transport.py at the repo root, change this line to
#     from agyteam.transport import Message, Transport
# or the copy raises ImportError before any of your code runs.
from .transport import Message, Transport


class NativeTransport(Transport):
    label = "native"

    def __init__(self, me, config=None):
        super().__init__(me, config)
        # self.config holds the parsed AGYTEAM_BUS_CONFIG JSON.
        # Open your client/connection here, e.g.:
        # self.client = YourA2AClient(endpoint=self.config["endpoint"], agent=me)
        raise NotImplementedError("implement NativeTransport before using it")

    def send(self, to: str, content: str) -> str:
        """Deliver to a peer, or to 'user'.

        Return '[delivered to <to>]' on success. For an unknown recipient
        return a string starting with '[error:' that lists valid names — do not
        raise; the model reads this and retries correctly.
        """
        raise NotImplementedError

    def fetch(self) -> list[Message]:
        """Return messages for me and mark them consumed.

        Must not redeliver — an agent that keeps seeing the same message loops
        forever. If your system pushes rather than polls, buffer deliveries on
        the instance and drain the buffer here. Return [] when empty.
        """
        raise NotImplementedError

    def teammates(self) -> list[dict]:
        """[{"name": ..., "role": ...}] for peers, excluding me and 'user'.

        If your system has an agent directory, read it here; that makes the
        roster file irrelevant and keeps discovery authoritative.
        """
        raise NotImplementedError

    # Optional. broadcast() defaults to send-per-teammate; override if your
    # system has a real fan-out primitive.
    #
    # def broadcast(self, content: str) -> str: ...
    #
    # Set supports_roster_admin = True and implement these only if the operator
    # should be able to change team composition through this transport.
    #
    # supports_roster_admin = True
    # def roster_add(self, name: str, role: str) -> str: ...
    # def roster_remove(self, name: str) -> str: ...
    #
    # def close(self) -> None:
    #     self.client.disconnect()
