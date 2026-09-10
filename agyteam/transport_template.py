"""Skeleton for a native/internal A2A transport. Copy, rename, fill in.

This is the *only* file you should need to write to move agyteam off the file
bus and onto a real peer-messaging system. Nothing else in agyteam changes, and
the agents' tools keep the same names and semantics.

    cp agyteam/transport_template.py mycorp/agy_a2a.py
    # implement the three methods
    export AGYTEAM_BUS_TRANSPORT=mycorp.agy_a2a:NativeTransport
    export AGYTEAM_BUS_CONFIG='{"endpoint": "..."}'      # optional
    .venv/bin/python evals/test_transport.py             # must pass 12/12

Run that suite before trusting it — it is transport-agnostic and checks the
contract (delivery, no redelivery, unknown recipients, isolation between
agents) that the rest of the system relies on.
"""
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
