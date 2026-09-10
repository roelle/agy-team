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
import json
from pathlib import Path

RESERVED = {"user"}          # addressable, but never a roster entry


class RosterError(ValueError):
    """Malformed roster. The message is meant to be shown to a human."""


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
    return entries


def normalize(doc: dict, source: str = "roster") -> dict:
    if not isinstance(doc, dict):
        raise RosterError(f"{source}: roster must be a JSON object, got "
                          f"{type(doc).__name__}")
    out = dict(doc)
    out["agents"] = normalize_agents(doc.get("agents"), source)
    return out


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
    """Write in canonical form, migrating any accepted shape on the way out."""
    path = Path(path)
    path.write_text(json.dumps(normalize(doc, source=str(path)), indent=2) + "\n")
