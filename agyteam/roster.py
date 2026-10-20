"""Roster loading and normalisation. Pure standard library.

Canonical shape is a **list of self-describing entries**:

    {"mission": "...", "agents": [{"name": "tpm", "role": "coordinates"}, ...]}

A list is canonical because entries get passed around individually — into
session configs, transports, and log lines — and an entry that carries its own
name needs no context to be useful. With a name->spec mapping you have to thread
the key alongside the value everywhere it travels, and forgetting to is exactly
the bug this module exists to prevent. Ordering is also explicit, which matters
for display and for the roster's role as team documentation.

Hand-written config should not have to know that, though, so several shapes are
accepted on read and normalised:

    "agents": [{"name": "tpm", "role": "..."}]     canonical
    "agents": {"tpm": {"role": "..."}}             mapping, key is the name
    "agents": {"tpm": "coordinates"}               mapping to a role string
    "agents": ["tpm", "coder"]                     bare names, roles unset

Anything else raises RosterError with a message naming the offending entry
rather than an AttributeError from three frames down. Writes are always
canonical, so a mapping-shaped file migrates the first time it is edited.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

RESERVED = {"user"}          # addressable, but never a roster entry


class RosterError(ValueError):
    """Malformed roster. The message is meant to be shown to a human."""


def normalize_workspaces(raw, source: str = "workspaces") -> list[str]:
    """Coerce accepted workspace shape into a deduplicated list of resolved paths."""
    if raw is None:
        return []
    if isinstance(raw, (str, Path)):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise RosterError(
            f"{source}: workspaces must be a list of paths or a path string, got {type(raw).__name__}"
        )
    out: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, (str, Path)) or not str(item).strip():
            raise RosterError(
                f"{source}: workspace[{i}] must be a non-empty path string, got {type(item).__name__}"
            )
        resolved = str(Path(item).expanduser().resolve())
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def normalize_agents(raw, source: str = "roster") -> list[dict]:
    """Coerce any accepted `agents` shape into a list of dicts with "name"."""
    if raw is None:
        return []

    entries: list[dict] = []
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, str):
                entry = {"name": key, "role": val}
            elif isinstance(val, dict):
                named = val.get("name")
                if named is not None and named != key:
                    raise RosterError(
                        f"{source}: agent key {key!r} disagrees with its "
                        f"\"name\" field {named!r} — remove one so routing is "
                        f"unambiguous")
                entry = {**val, "name": key}
            else:
                raise RosterError(
                    f"{source}: agent {key!r} must map to an object or a role "
                    f"string, got {type(val).__name__}")
            entries.append(entry)
    elif isinstance(raw, list):
        for i, val in enumerate(raw):
            if isinstance(val, str):
                entries.append({"name": val, "role": ""})
            elif isinstance(val, dict):
                if not val.get("name"):
                    raise RosterError(
                        f"{source}: agents[{i}] has no \"name\" "
                        f"(keys: {', '.join(sorted(val)) or 'none'})")
                entries.append(dict(val))
            else:
                raise RosterError(
                    f"{source}: agents[{i}] must be an object or a name "
                    f"string, got {type(val).__name__}")
    else:
        raise RosterError(
            f"{source}: \"agents\" must be a list or an object, got "
            f"{type(raw).__name__}")

    seen: set[str] = set()
    for e in entries:
        name = str(e["name"]).strip()
        if not name:
            raise RosterError(f"{source}: an agent has an empty name")
        if name in RESERVED:
            raise RosterError(
                f"{source}: {name!r} is reserved (it always addresses the human)")
        if name in seen:
            raise RosterError(
                f"{source}: duplicate agent name {name!r} — names are how "
                f"messages are routed, so they must be unique")
        seen.add(name)
        e["name"] = name
        e.setdefault("role", "")
        if e.get("alias") is not None:
            alias = e["alias"]
            if not isinstance(alias, str) or not _HANDLE.match(alias.strip()):
                raise RosterError(
                    f"{source}: {name}.alias must be a handle like 'Ada' (letters, "
                    f"digits, - _ .), got {alias!r}")
            e["alias"] = alias.strip()
        if "workspaces" in e:
            e["workspaces"] = normalize_workspaces(
                e["workspaces"], source=f"{source}: {name}.workspaces"
            )
    return entries


def normalize(doc: dict, source: str = "roster") -> dict:
    if not isinstance(doc, dict):
        raise RosterError(f"{source}: roster must be a JSON object, got "
                          f"{type(doc).__name__}")
    out = dict(doc)
    if "workspaces" in doc:
        out["workspaces"] = normalize_workspaces(
            doc.get("workspaces"), source=f"{source}: workspaces"
        )
    out["agents"] = normalize_agents(doc.get("agents"), source)
    if doc.get("operator") is not None:
        out["operator"] = _normalize_operator(doc["operator"], source)
    _check_handles(out, source)
    return out


# --- what people call each other ----------------------------------------------
#
# An agent's `alias` is what the team calls it: "Ada" for "at-Ada", or any
# nickname. It is shown in clients, offered by @-autocomplete, and resolves
# to the agent wherever a name is taken (send_to_teammate, @mentions).
#
# The roster's `operator` names the human the team works for:
#
#     "operator": {"name": "Matt", "handles": ["Matt", "roellem"]}
#
# They stay "user" on the bus -- the review gate and the episode logic key on
# it -- but agents are told who that is, can address them by a handle, and an
# @handle in a channel is recorded as a mention of the user.

_HANDLE = re.compile(r"^[A-Za-z0-9][\w.-]{0,39}$")
_WORDS = {"user", "here", "all", "everyone", "channel"}


def _normalize_operator(raw, source: str) -> dict:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise RosterError(f"{source}: \"operator\" must be an object like "
                          f"{{\"name\": \"Matt\", \"handles\": [\"Matt\"]}}")
    name = str(raw.get("name") or "").strip()
    handles = raw.get("handles") or []
    if isinstance(handles, str):
        handles = [handles]
    out = []
    for h in list(handles) + ([name] if name and _HANDLE.match(name) else []):
        h = str(h).strip().lstrip("@")
        if not _HANDLE.match(h):
            raise RosterError(f"{source}: operator handle {h!r} is not a handle "
                              f"(letters, digits, - _ .)")
        if h.lower() not in (x.lower() for x in out):
            out.append(h)
    return {**raw, "name": name or (out[0] if out else ""), "handles": out}


def _check_handles(doc: dict, source: str) -> None:
    """No handle may mean two things: a name, an alias or an operator handle."""
    owner: dict[str, str] = {}

    def claim(handle: str, who: str):
        low = handle.lower()
        if low in _WORDS and who != "user":
            raise RosterError(f"{source}: {handle!r} is reserved")
        if low in owner and owner[low] != who:
            raise RosterError(f"{source}: {handle!r} would mean both {owner[low]!r} "
                              f"and {who!r}; names, aliases and operator handles "
                              f"must be unique, ignoring case")
        owner[low] = who
    for a in doc.get("agents", []):
        claim(a["name"], a["name"])
    for a in doc.get("agents", []):
        if a.get("alias"):
            claim(a["alias"], a["name"])
    for h in (doc.get("operator") or {}).get("handles", []):
        claim(h, "user")


def aliases(doc: dict) -> dict[str, str]:
    """{handle, lowercased: who}: agent aliases, and operator handles -> "user"."""
    out = {}
    for a in doc.get("agents", []) if isinstance(doc, dict) else []:
        if isinstance(a, dict) and isinstance(a.get("alias"), str) and a.get("name"):
            out[a["alias"].lower()] = a["name"]
    for h in operator(doc).get("handles", []):
        out[h.lower()] = "user"
    return out


def operator(doc: dict) -> dict:
    """{"name", "handles"} for the human this team works for; empty if unset."""
    op = doc.get("operator") if isinstance(doc, dict) else None
    try:
        return _normalize_operator(op, "roster") if op else {"name": "", "handles": []}
    except RosterError:
        return {"name": "", "handles": []}


_SEPARATORS = "-_."


def resolve_name(handle: str, names, aliases: dict[str, str] | None = None) -> str | None:
    """The agent a handle means: the agent named exactly that; else the one
    whose name matches ignoring case; else the one with that alias (from
    roster.aliases, where an operator handle means "user"); else the one
    whose name ends in it after a team prefix ("Ada" -> "at-Ada", "at_ada",
    "at.Ada"). Two candidates at any step is no match -- a handle that could
    wake either of two agents wakes neither, rather than the wrong one."""
    names = list(names)
    if not handle:
        return None
    if handle in names:
        return handle
    low = handle.lower()
    exact = [n for n in names if n.lower() == low]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    if aliases and low in aliases:
        return aliases[low]
    suffix = [n for n in names if len(n) > len(low) and n.lower().endswith(low)
              and n[-len(low) - 1] in _SEPARATORS]
    return suffix[0] if len(suffix) == 1 else None


def load(path) -> dict:
    """Read and normalise a roster file. Missing file → empty roster."""
    path = Path(path)
    if not path.exists():
        return {"agents": []}
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RosterError(f"{path}: not valid JSON ({e})")
    return normalize(doc, source=str(path))


def save(path, doc: dict) -> None:
    """Write in canonical form, migrating any accepted shape on the way out.

    Recorded in the history first (see record_write), so a supervisor that
    sees the new file also sees it was written here.
    """
    path = Path(path)
    text = json.dumps(normalize(doc, source=str(path)), indent=2) + "\n"
    record_write(path, text.encode("utf-8"))
    # Whole or not at all: a reader between a truncate and a write saw an
    # empty roster, and a supervisor reverted it as a change nobody made.
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- who changed the roster ------------------------------------------------
#
# Every permission an agent has is in roster.json, and an agent with a shell
# runs as the same user who wrote it. The supervisor keeps the roster it
# accepted and reverts a change it cannot account for (Supervisor
# _guard_roster): one made while an agent's turn was running, that this
# module did not write. Each write through save() is recorded here first,
# by content hash, which is how a `lifecycle` command or the bus's roster
# admin tools are told apart from an agent editing the file.
#
# This is detection, not a boundary. Anything agyteam can write, an agent
# running as the same user can write too, this file included; the boundary
# is an OS user or container that cannot write the team directory.

HISTORY = "roster_history.jsonl"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_write(path, data: bytes, by: str | None = None) -> None:
    """Append {ts, sha256, by} for `data` about to become `path`. Never raises."""
    path = Path(path)
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "file": path.name,
             "sha256": digest(data),
             "by": by or f"{Path(sys.argv[0] or 'python').name} (pid {os.getpid()})"}
    try:
        with (path.parent / HISTORY).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def history_size(path) -> int:
    """Where the history ends now; pass it to recorded_since later."""
    try:
        return (Path(path).parent / HISTORY).stat().st_size
    except OSError:
        return 0


def recorded_since(path, offset: int) -> set[str]:
    """sha256 of every write of `path` recorded after byte `offset`.

    Since, not newest: two saves in quick succession must both be agyteam's.
    And not ever: a roster agyteam wrote before the one now accepted is
    exactly what an agent replaying a looser one would bring back.
    """
    try:
        with (Path(path).parent / HISTORY).open("rb") as f:
            f.seek(offset)
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return set()
    out = set()
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("file") == Path(path).name and rec.get("sha256"):
            out.add(rec["sha256"])
    return out


def last_recorded(path) -> str | None:
    """The sha256 of the newest recorded write of `path`, or None."""
    try:
        lines = (Path(path).parent / HISTORY).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("file") == Path(path).name and rec.get("sha256"):
            return rec["sha256"]
    return None


def changed_entries(before: bytes | None, after: bytes | None) -> list[str]:
    """What differs, for a human: agent names, then other top-level keys."""
    def parse(b):
        try:
            d = json.loads(b) if b else {}
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {"(unparseable)": True}
    old, new = parse(before), parse(after)
    try:
        oa = {a["name"]: a for a in normalize(old).get("agents", [])} if old.get("agents") else {}
        na = {a["name"]: a for a in normalize(new).get("agents", [])} if new.get("agents") else {}
    except (RosterError, KeyError, TypeError):
        oa, na = {}, {"(agents unreadable)": True}
    out = sorted(n for n in set(oa) | set(na) if oa.get(n) != na.get(n))
    out += sorted(k for k in (set(old) | set(new)) - {"agents"} if old.get(k) != new.get(k))
    return out
