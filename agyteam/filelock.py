"""An exclusive lock around a read-check-write on a shared record file.

Appending one line is safe without this: every writer here appends whole lines
with O_APPEND, and nobody's line lands inside somebody else's. What is not safe
is *deciding* based on what the file said and then writing -- "nobody owns this
task, so it is mine" -- because two processes can both read "nobody" before
either writes. Measured on the task log before this existed: eight agents
claiming one task at once, and in all twenty trials more than one of them was
told it had won. For a task that launches a simulation, the loser is a
duplicate run.

Pure standard library, like everything the plugin installs. The lock lives in
a sidecar file (`<path>.lock`) so the record itself is never opened for
anything but reading and appending.

Not re-entrant: a function holding the lock must not call another that takes
it, or it waits on itself. Keep the locked sections at the edges (the public
functions that mutate) and the helpers they call lock-free.

Advisory, and only as good as the filesystem's lock support -- reliable on a
local disk, not across NFS.
"""
import contextlib
from pathlib import Path

try:
    import fcntl
except ImportError:                    # Windows
    fcntl = None
    import msvcrt


@contextlib.contextmanager
def locked(path: Path | str):
    """Hold an exclusive lock on `path` for the duration of the block."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as f:
        if fcntl:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
