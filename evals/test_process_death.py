"""Real mid-turn process death tests: proving at-least-once delivery under true SIGKILL.

Strictly verifies that if a worker / agent process is killed via kernel SIGKILL
mid-turn (after messages have been fetched into memory, before acknowledgement):
1. In-flight messages are NOT dropped or marked consumed.
2. Messages remain durable in the transport queue across hard process termination.
3. Subsequent turns reliably fetch and process the preserved messages.
4. Transport acknowledge occurs strictly upon successful turn completion.

Tested against both FileTransport and SqliteTransport.
"""
import json
import os
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.runner import Runner
from agyteam.supervisor import Supervisor
from agyteam.transport import load as load_transport

CHILD_SCRIPT = """
import sys
import time
import os
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "evals"))

from agyteam.supervisor import Supervisor
from agyteam.runner import Runner

class MidTurnBlockRunner(Runner):
    label = "midturn-blocker"
    def wake(self, agent: str, message: str) -> str:
        # At this exact moment, transport.fetch() has executed in Supervisor.step(),
        # and messages are in memory, but acknowledge() has not been called!
        sys.stdout.write("MID_TURN_ACTIVE\\n")
        sys.stdout.flush()
        # Block until killed by kernel SIGKILL
        time.sleep(30)
        return "unexpected_turn_completion"

agent_name = sys.argv[2]
sup = Supervisor([agent_name], MidTurnBlockRunner(), max_hops=1, quiet=True)
sup.step()
"""


class RecoveryRunner(Runner):
    label = "recovery"

    def __init__(self):
        super().__init__()
        self.woken_prompts: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken_prompts.append(message)
        return "turn recovered successfully"


def assert_check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok and detail:
        print(f"       {detail[:400]}")
    assert ok, f"{label}: {detail}"
    return True


def wait_for_sentinel(proc: subprocess.Popen, sentinel: str = "MID_TURN_ACTIVE", timeout_s: float = 10.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        r, _, _ = select.select([proc.stdout], [], [], 0.1)
        if r:
            line = proc.stdout.readline()
            if sentinel in line:
                return True
        if proc.poll() is not None:
            break
    return False


def check_process_death_file_transport() -> tuple[int, int]:
    """Test mid-turn SIGKILL survival with FileTransport."""
    print("\n== mid-turn SIGKILL process death: FileTransport ==")
    td = Path(tempfile.mkdtemp(prefix="agyteam-sigkill-file-")) / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "coder", "role": "implements"}]}))

    saved_env = {
        "AGYTEAM_TEAM_DIR": os.environ.get("AGYTEAM_TEAM_DIR"),
        "AGYTEAM_BUS_TRANSPORT": os.environ.get("AGYTEAM_BUS_TRANSPORT"),
        "AGYTEAM_BUS_CONFIG": os.environ.get("AGYTEAM_BUS_CONFIG"),
    }
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)
    os.environ.pop("AGYTEAM_BUS_TRANSPORT", None)
    os.environ.pop("AGYTEAM_BUS_CONFIG", None)

    try:
        # 1. Enqueue in-flight test messages
        u_trans = load_transport("user")
        u_trans.send("coder", "critical file msg surviving sigkill")
        u_trans.send("coder", "second critical file msg")

        c_trans = load_transport("coder")
        initial_msgs = c_trans.peek()
        assert_check("initial messages queued in file transport", len(initial_msgs) == 2, str(initial_msgs))

        # 2. Spawn child process that fetches messages and reaches mid-turn wake
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'evals'}"
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD_SCRIPT, str(ROOT), "coder"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        sentinel_seen = wait_for_sentinel(proc, "MID_TURN_ACTIVE", timeout_s=10.0)
        assert_check("child reached mid-turn state before acknowledge", sentinel_seen, "Did not see MID_TURN_ACTIVE")

        # 3. Kernel SIGKILL mid-turn
        os.kill(proc.pid, signal.SIGKILL)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        assert_check(
            "child terminated abnormally via SIGKILL",
            proc.returncode == -signal.SIGKILL,
            f"returncode: {proc.returncode}",
        )

        # 4. Verify message survived hard crash in durable store
        pending_after_kill = c_trans.peek()
        assert_check(
            "both in-flight messages remain in queue after SIGKILL",
            len(pending_after_kill) == 2,
            str(pending_after_kill),
        )

        inbox_file = td / "inbox" / "coder.jsonl"
        on_disk_lines = [json.loads(l) for l in inbox_file.read_text().splitlines() if l.strip()]
        assert_check("raw jsonl file preserves unconsumed messages on disk", len(on_disk_lines) == 2, str(on_disk_lines))

        # 5. Subsequent recovery turn fetches and successfully processes messages
        rec_runner = RecoveryRunner()
        sup = Supervisor(["coder"], rec_runner, max_hops=1, quiet=True)
        turns = sup.step()
        sup.close()

        assert_check("recovery turn completed", turns == 1, f"turns={turns}")
        assert_check(
            "recovery turn received first preserved message",
            len(rec_runner.woken_prompts) > 0 and "critical file msg surviving sigkill" in rec_runner.woken_prompts[0],
            str(rec_runner.woken_prompts),
        )
        assert_check(
            "recovery turn received second preserved message",
            len(rec_runner.woken_prompts) > 0 and "second critical file msg" in rec_runner.woken_prompts[0],
            str(rec_runner.woken_prompts),
        )
        assert_check(
            "queue drained after successful completion and acknowledge",
            len(c_trans.peek()) == 0,
            str(c_trans.peek()),
        )
        assert_check(
            "inbox file on disk is empty after acknowledge",
            inbox_file.read_text().strip() == "",
            inbox_file.read_text(),
        )
    finally:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        shutil.rmtree(td.parent, ignore_errors=True)

    return 9, 9


def check_process_death_sqlite_transport() -> tuple[int, int]:
    """Test mid-turn SIGKILL survival with SqliteTransport."""
    print("\n== mid-turn SIGKILL process death: SqliteTransport ==")
    sqlite_db = Path(tempfile.mkdtemp(prefix="agyteam-sigkill-sql-")) / "bus.db"
    sql_cfg = json.dumps({"db": str(sqlite_db), "roster": [{"name": "coder", "role": "implements"}]})

    saved_env = {
        "AGYTEAM_TEAM_DIR": os.environ.get("AGYTEAM_TEAM_DIR"),
        "AGYTEAM_BUS_TRANSPORT": os.environ.get("AGYTEAM_BUS_TRANSPORT"),
        "AGYTEAM_BUS_CONFIG": os.environ.get("AGYTEAM_BUS_CONFIG"),
    }
    os.environ["AGYTEAM_BUS_TRANSPORT"] = "fixture_transport:SqliteTransport"
    os.environ["AGYTEAM_BUS_CONFIG"] = sql_cfg
    os.environ.pop("AGYTEAM_TEAM_DIR", None)

    try:
        # 1. Enqueue in-flight test messages
        u_trans = load_transport("user")
        u_trans.send("coder", "critical sqlite msg surviving sigkill")
        u_trans.send("coder", "second critical sqlite msg")

        c_trans = load_transport("coder")
        initial_msgs = c_trans.peek()
        assert_check("initial messages queued in sqlite transport", len(initial_msgs) == 2, str(initial_msgs))

        # Check raw DB
        con = sqlite3.connect(str(sqlite_db))
        cur = con.cursor()
        cur.execute("SELECT id, consumed, content FROM msg WHERE recipient = 'coder'")
        db_rows = cur.fetchall()
        assert_check("raw sqlite rows inserted with consumed=0", len(db_rows) == 2 and all(r[1] == 0 for r in db_rows), str(db_rows))

        # 2. Spawn child process that fetches messages and reaches mid-turn wake
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'evals'}"
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD_SCRIPT, str(ROOT), "coder"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        sentinel_seen = wait_for_sentinel(proc, "MID_TURN_ACTIVE", timeout_s=10.0)
        assert_check("child reached mid-turn state before acknowledge", sentinel_seen, "Did not see MID_TURN_ACTIVE")

        # 3. Kernel SIGKILL mid-turn
        os.kill(proc.pid, signal.SIGKILL)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        assert_check(
            "child terminated abnormally via SIGKILL",
            proc.returncode == -signal.SIGKILL,
            f"returncode: {proc.returncode}",
        )

        # 4. Verify message survived hard crash in durable sqlite store
        cur.execute("SELECT id, consumed, content FROM msg WHERE recipient = 'coder'")
        db_rows_after_kill = cur.fetchall()
        assert_check(
            "raw sqlite rows still unconsumed (consumed=0) after SIGKILL",
            len(db_rows_after_kill) == 2 and all(r[1] == 0 for r in db_rows_after_kill),
            str(db_rows_after_kill),
        )

        pending_after_kill = c_trans.peek()
        assert_check(
            "both in-flight messages remain in queue after SIGKILL",
            len(pending_after_kill) == 2,
            str(pending_after_kill),
        )

        # 5. Subsequent recovery turn fetches and successfully processes messages
        rec_runner = RecoveryRunner()
        sup = Supervisor(["coder"], rec_runner, max_hops=1, quiet=True)
        turns = sup.step()
        sup.close()

        assert_check("recovery turn completed", turns == 1, f"turns={turns}")
        assert_check(
            "recovery turn received first preserved message",
            len(rec_runner.woken_prompts) > 0 and "critical sqlite msg surviving sigkill" in rec_runner.woken_prompts[0],
            str(rec_runner.woken_prompts),
        )
        assert_check(
            "recovery turn received second preserved message",
            len(rec_runner.woken_prompts) > 0 and "second critical sqlite msg" in rec_runner.woken_prompts[0],
            str(rec_runner.woken_prompts),
        )
        assert_check(
            "queue drained after successful completion and acknowledge",
            len(c_trans.peek()) == 0,
            str(c_trans.peek()),
        )

        cur.execute("SELECT id, consumed, content FROM msg WHERE recipient = 'coder'")
        db_rows_after_rec = cur.fetchall()
        assert_check(
            "raw sqlite rows marked consumed=1 after recovery acknowledge",
            len(db_rows_after_rec) == 2 and all(r[1] == 1 for r in db_rows_after_rec),
            str(db_rows_after_rec),
        )
        con.close()
    finally:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        shutil.rmtree(sqlite_db.parent, ignore_errors=True)

    return 10, 10


def test_process_death_file_transport() -> None:
    check_process_death_file_transport()


def test_process_death_sqlite_transport() -> None:
    check_process_death_sqlite_transport()


if __name__ == "__main__":
    totals = [
        check_process_death_file_transport(),
        check_process_death_sqlite_transport(),
    ]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== process death durability: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
