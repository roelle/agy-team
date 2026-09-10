"""Pluggable durable memory.

Mirror of agyteam/transport.py, for the same reason: the agent-facing tools are
fixed, the storage behind them is not. The default keeps memory in markdown
files, which is right for a single machine and readable by humans. Point
AGYTEAM_MEMORY_STORE at your own class to put it somewhere else — a shared
database, a team knowledge service, an internal store — without touching
agyteam source or changing what agents see.

    AGYTEAM_MEMORY_STORE=example_memory:MyStore
    AGYTEAM_MEMORY_CONFIG='{"dsn": "..."}'          # optional, JSON

Implement `save`, `read`, `delete`, and `index`. See memory_template.py.

Division of labour: the store does storage, the MCP server does presentation.
Stores return plain data (or None for "not found") and never format user-facing
strings, so every backend produces identical wording — including the honest
"no memory named X, here is what exists" reply, which is load-bearing for
grounding and must not vary by backend.
"""
import difflib
import importlib
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_STORE = "agyteam.memory_file:FileMemory"


class MemoryError_(Exception):
    """Storage failure. The message is meant to be shown to a human."""


@dataclass
class MemoryEntry:
    name: str
    description: str
    why: str = ""
    when: str = ""


# Stopwords excluded from keyword matching to prevent spurious overlap signals
# on common grammatical glue words. Negations (not, never, no, without) are
# intentionally preserved because negation polarity is central to contradiction.
STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing",
    "down", "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more",
    "most", "my", "myself", "now", "of", "off", "on", "once", "only", "or", "other",
    "our", "ours", "ourselves", "out", "over", "own", "s", "same", "she", "should",
    "so", "some", "such", "t", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "these", "they", "this", "those", "through",
    "to", "too", "under", "until", "up", "very", "was", "we", "were", "what",
    "when", "where", "which", "while", "who", "whom", "why", "will", "with", "you",
    "your", "yours", "yourself", "yourselves"
}

# Antonym pairs for detecting opposing assertions on shared technical subjects.
#
# Layering rationale:
# 1. The table catches explicit polarity flips (e.g. available vs unavailable,
#    enabled vs disabled) where opposing claims share a technical subject.
# 2. Keyword overlap serves as the general safety net beneath it, catching
#    contradictions and topic collisions that lack explicit antonyms.
# 3. Expanding this table indefinitely is not the path to improved recall; rather
#    than trying to build an exhaustive dictionary, we record the limitation
#    honestly: specific polarity flips are checked here, while broader semantic
#    overlap is caught by the layers below.
ANTONYM_PAIRS = [
    ("available", "unavailable"),
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("allow", "disallow"),
    ("allow", "forbid"),
    ("allowed", "forbidden"),
    ("allowed", "disallowed"),
    ("always", "never"),
    ("true", "false"),
    ("pass", "fail"),
    ("passed", "failed"),
    ("success", "failure"),
    ("successful", "failed"),
    ("start", "stop"),
    ("online", "offline"),
    ("active", "inactive"),
    ("valid", "invalid"),
    ("direct", "delegate"),
    ("directly", "delegate"),
    ("required", "optional"),
    ("permitted", "prohibited"),
    ("permit", "prohibit"),
    ("accept", "reject"),
    ("accepted", "rejected"),
    ("lock", "unlock"),
    ("locked", "unlocked"),
]

ANTONYM_MAP: dict[str, str] = {}
for _w1, _w2 in ANTONYM_PAIRS:
    ANTONYM_MAP[_w1] = _w2
    ANTONYM_MAP[_w2] = _w1

NEGATION_WORDS = {
    "not", "never", "no", "cannot", "cant", "dont", "wont", "isnt", "arent",
    "wasnt", "werent", "shouldnt", "without", "stop", "avoid"
}


def extract_keywords(text: str) -> set[str]:
    """Extract lowercase alphanumeric keyword tokens, excluding stopwords."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def extract_provenance(text: str) -> tuple[str | None, str | None, str]:
    """Extract (when, why, body) from memory text.

    Tolerant of legacy memory files that lack provenance headers.
    """
    when = None
    why = None
    body_lines = []
    in_header = True
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if in_header:
            if i == 0 and stripped.startswith("#"):
                continue
            if stripped.startswith(("- When:", "When:")):
                when = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith(("- Why:", "Why:")):
                why = stripped.split(":", 1)[1].strip()
                continue
            if not stripped:
                continue
            in_header = False
        body_lines.append(line)
    return when, why, "\n".join(body_lines).strip()


def detect_conflicts(name: str, description: str, content: str, why: str,
                     existing: list[tuple[str, str, str]]) -> list[dict]:
    """Detect if (name, description, content, why) appears to overlap or conflict
    with existing memories [(other_name, other_desc, other_content), ...].

    Note on content format:
    Both `content` and `other_content` in `existing` must be provenance-stripped
    body text (not raw markdown containing '# heading', '- When:', or '- Why:'
    metadata headers). Passing raw content introduces metadata tokens (timestamps,
    header labels) that dilute keyword ratios and suppress content_overlap detection.

    Returns a list of dicts: [{'name': other_name, 'reason': str, 'kind': str}].
    Deciding what 'appears to overlap' means:
    1. Topic / Name near-duplicate: names share high string similarity or topic slug tokens.
       Rationale: Agents are instructed to update existing memories rather than pile up
       near-duplicates under slightly different names.
    2. Opposing assertions / Antonyms on shared subject: memories share subject keywords
       but express opposite polarities (e.g. available vs unavailable, delegate vs direct).
       Rationale: Catches factual contradictions like 'tools available' vs 'tools unavailable'.
    3. Negated actions: one asserts an action while the other negates it in the context of
       the same technical entities.
    4. Conflicting attribute values: same key (e.g. port, host) specified with different values.
    5. Semantic / Keyword overlap: high Jaccard similarity of distinct content terms.
    """
    conflicts = []
    new_full_text = f"{name} {description} {content} {why}"
    kw_new = extract_keywords(new_full_text)
    words_new = re.findall(r"[a-z0-9]+", new_full_text.lower())

    # Pre-calculate negated words in new memory
    neg_new = set()
    for idx, wd in enumerate(words_new):
        if wd in STOPWORDS or wd in NEGATION_WORDS or len(wd) <= 2:
            continue
        window = words_new[max(0, idx - 3):idx]
        if any(nw in NEGATION_WORDS for nw in window):
            neg_new.add(wd)

    for other_name, other_desc, other_raw in existing:
        if other_name == name:
            # Overwriting the same memory is intentional; never a conflict with itself
            continue

        other_full_text = f"{other_name} {other_desc} {other_raw}"
        kw_old = extract_keywords(other_full_text)
        shared_kw = kw_new & kw_old

        # 1. Name / slug similarity
        name_clean1 = name.replace("_", "-")
        name_clean2 = other_name.replace("_", "-")
        name_sim = difflib.SequenceMatcher(None, name_clean1, name_clean2).ratio()
        tokens1 = set(name_clean1.split("-"))
        tokens2 = set(name_clean2.split("-"))
        shared_tokens = tokens1 & tokens2

        if name_sim >= 0.8:
            conflicts.append({
                "name": other_name,
                "kind": "name_overlap",
                "reason": f"topic name is very similar to '{other_name}' ({int(name_sim * 100)}% match); consider updating it instead"
            })
            continue
        elif len(shared_tokens) >= 2 and (tokens1.issubset(tokens2) or tokens2.issubset(tokens1)):
            conflicts.append({
                "name": other_name,
                "kind": "name_overlap",
                "reason": f"topic name shares tokens ({', '.join(sorted(shared_tokens))}) with '{other_name}'; consider updating it instead"
            })
            continue

        # 2. Antonym pairs on shared subject
        antonym_found = False
        for w in kw_new:
            opp = ANTONYM_MAP.get(w)
            if opp and opp in kw_old:
                context = shared_kw - {w, opp}
                if context or name_sim >= 0.35:
                    ctx_desc = f" on subject '{', '.join(sorted(context))}'" if context else ""
                    conflicts.append({
                        "name": other_name,
                        "kind": "antonym_contradiction",
                        "reason": f"opposing assertions{ctx_desc}: '{w}' vs '{opp}'"
                    })
                    antonym_found = True
                    break
        if antonym_found:
            continue

        # 3. Negated actions on shared subject
        words_old = re.findall(r"[a-z0-9]+", other_full_text.lower())
        neg_old = set()
        for idx, wd in enumerate(words_old):
            if wd in STOPWORDS or wd in NEGATION_WORDS or len(wd) <= 2:
                continue
            window = words_old[max(0, idx - 3):idx]
            if any(nw in NEGATION_WORDS for nw in window):
                neg_old.add(wd)

        pos_new = kw_new - neg_new - NEGATION_WORDS
        pos_old = kw_old - neg_old - NEGATION_WORDS
        conflict_actions = (neg_new & pos_old) | (neg_old & pos_new)
        action_found = False
        for act in conflict_actions:
            context = shared_kw - {act}
            if context:
                who_negated = "new memory negates" if act in neg_new else f"'{other_name}' negates"
                conflicts.append({
                    "name": other_name,
                    "kind": "negation_contradiction",
                    "reason": f"conflicting action on subject '{', '.join(sorted(context))}': {who_negated} '{act}'"
                })
                action_found = True
                break
        if action_found:
            continue

        # 4. Conflicting attribute values (e.g. port 2222 vs port 2200)
        attr_pattern = re.compile(
            r'\b(port|host|server|env|environment|version|branch|user|timeout)\s*[:= ]\s*([a-zA-Z0-9_.-]+)',
            re.IGNORECASE
        )
        attrs_new = {k.lower(): v.lower() for k, v in attr_pattern.findall(new_full_text)}
        attrs_old = {k.lower(): v.lower() for k, v in attr_pattern.findall(other_full_text)}
        attr_found = False
        for k, v1 in attrs_new.items():
            if k in attrs_old and v1 != attrs_old[k] and shared_kw:
                conflicts.append({
                    "name": other_name,
                    "kind": "attribute_conflict",
                    "reason": f"conflicting value for attribute '{k}': '{v1}' vs '{attrs_old[k]}'"
                })
                attr_found = True
                break
        if attr_found:
            continue

        # 5. Semantic / keyword overlap (high Jaccard similarity)
        if len(shared_kw) >= 3:
            union_len = len(kw_new | kw_old)
            jaccard = len(shared_kw) / union_len if union_len else 0
            min_len = min(len(kw_new), len(kw_old))
            containment = len(shared_kw) / min_len if min_len else 0
            if jaccard >= 0.45 or (containment >= 0.75 and len(shared_kw) >= 4):
                top_shared = ', '.join(sorted(list(shared_kw))[:4])
                conflicts.append({
                    "name": other_name,
                    "kind": "content_overlap",
                    "reason": f"high keyword overlap ({int(jaccard * 100)}% similarity on '{top_shared}'); check if memories should be merged"
                })

    return conflicts


def normalize_name(name: str) -> str:
    """Canonical memory name, applied *before* the store sees it.

    Normalising centrally means every backend agrees on identity: saving
    "Deploy Host" and later reading "deploy-host" must hit the same record
    whether the backend is files or a database.
    """
    slug = name.strip().replace(" ", "-").lower()
    slug = "".join(c for c in slug if c.isalnum() or c in "-_")
    if not slug:
        raise MemoryError_(f"{name!r} is not a usable memory name")
    return slug


class MemoryStore(ABC):
    """One agent's durable memory. Constructed per agent process."""

    #: shown in the MCP server name, so you can tell which store is live
    label = "store"

    def __init__(self, agent: str, config: dict | None = None):
        self.agent = agent
        self.config = config or {}

    @abstractmethod
    def save(self, name: str, description: str, content: str,
             why: str = "", when: str | None = None) -> bool:
        """Create or overwrite a memory. Return True if new, False if updated.

        Names arrive already normalised. Overwriting is intentional — agents are
        told to update rather than accumulate near-duplicates.

        why: the situation or reason that produced this lesson, recorded so future
        sessions can evaluate if the memory is still true.
        when: timestamp string of when the memory was saved (defaults to current time).
        """

    @abstractmethod
    def read(self, name: str) -> str | None:
        """Return the content, or None if there is no such memory.

        Return None rather than raising or inventing: the server turns None into
        an explicit "I don't have that" reply listing what does exist, which is
        what keeps an agent from filling the gap with a guess.
        """

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a memory. Return False if it wasn't there."""

    @abstractmethod
    def index(self) -> list[MemoryEntry]:
        """Every memory as (name, description). Ordering is up to you."""

    def close(self) -> None:
        """Release resources. Called on server shutdown; default is a no-op."""


def load(agent: str, spec: str | None = None,
         config: dict | None = None) -> MemoryStore:
    """Instantiate the configured store.

    Failures are loud on purpose: silently falling back to file storage when you
    meant to use a shared backend would strand an agent's learnings somewhere
    nobody looks, and the symptom (an agent that forgets) is the exact failure
    this project exists to prevent.
    """
    spec = spec or os.environ.get("AGYTEAM_MEMORY_STORE") or DEFAULT_STORE
    if config is None:
        raw = os.environ.get("AGYTEAM_MEMORY_CONFIG", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"AGYTEAM_MEMORY_CONFIG is not valid JSON: {e}")
    if ":" not in spec:
        raise SystemExit(f"AGYTEAM_MEMORY_STORE must be 'module:Class', got {spec!r}")
    mod_name, _, cls_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"cannot load memory store {spec!r}: {e}")
    if not issubclass(cls, MemoryStore):
        raise SystemExit(f"{spec} is not an agyteam.memory.MemoryStore subclass")
    return cls(agent, config)


# Keywords indicating conditional, transient, or workaround lessons in provenance ('why').
# A memory learned as a workaround for a broken tool, missing feature, or transient outage
# can become obsolete when the underlying condition resolves.
#
# Limitations:
# Like my_capabilities in mcp_self.py, we acknowledge the heuristic's limits honestly:
# keyword matching on provenance detects that a lesson was conditional upon a past situation,
# but cannot independently verify whether that external situation has cleared.
CONDITIONAL_KEYWORDS = {
    "blocked",
    "blocker",
    "broken",
    "bug",
    "bugs",
    "bypass",
    "disabled",
    "down",
    "failed",
    "failing",
    "failure",
    "hack",
    "interim",
    "missing",
    "outage",
    "pending",
    "revert",
    "reverted",
    "temporary",
    "temporarily",
    "transient",
    "unavailable",
    "unresolved",
    "workaround",
}


def find_conditional_keywords(why: str) -> list[str]:
    """Extract matching conditional or transient trigger keywords from a memory's 'why'.

    Tokens are matched as lowercase whole words against CONDITIONAL_KEYWORDS.
    Returns a sorted list of unique matched keywords.
    """
    if not why:
        return []
    words = re.findall(r"[a-z0-9]+", why.lower())
    return sorted({w for w in words if w in CONDITIONAL_KEYWORDS})


# Bounding constants to protect agent context and bound quadratic comparisons:
#
# Comparing memories pairwise scales quadratically O(N^2). An uncapped review
# across an arbitrarily large memory store would consume excessive CPU time
# and, worse, flood the calling agent's context window with findings,
# defeating the purpose of a focused self-review tool.
#
# Consistent with my_activity in mcp_self.py:
# - MAX_REVIEW_MEMORIES (100) caps the number of memories examined.
# - MAX_FLAGS_PER_SECTION (20) caps findings reported per category.
MAX_REVIEW_MEMORIES = 100
MAX_FLAGS_PER_SECTION = 20


def review_memories(store: MemoryStore) -> str:
    """Examine stored memories and report items that deserve a second look.

    Categories flagged:
    1. Mutual contradictions: pairwise comparison of stored memories using
       detect_conflicts (evaluating pairs with i < j to avoid duplicate reports).
    2. Unknown vintage: memories without provenance or missing 'why', which cannot
       be judged for staleness.
    3. Conditional lessons: memories whose provenance ('why') describes a situation
       that may have passed (e.g. broken, unavailable, bug, workaround).

    Non-mutating: does not change or delete anything; reports findings for the agent.
    """
    entries = store.index()
    if not entries:
        return "# Memory review\n\n(empty — you have not saved anything yet)"

    # Bound pairwise review work
    examined_entries = entries[:MAX_REVIEW_MEMORIES]
    records = []
    for e in examined_entries:
        raw = store.read(e.name) or ""
        when, why, body = extract_provenance(raw)
        records.append({
            "name": e.name,
            "description": e.description,
            "raw": raw,
            "when": when,
            "why": why,
            "body": body,
        })

    contradiction_flags = []
    vintage_flags = []
    conditional_flags = []

    # 1. Mutual contradictions (pairwise comparison i < j)
    for i in range(len(records)):
        rec_i = records[i]
        existing_j = [
            (records[j]["name"], records[j]["description"], records[j]["body"])
            for j in range(i + 1, len(records))
        ]
        if existing_j:
            conflicts = detect_conflicts(
                rec_i["name"],
                rec_i["description"],
                rec_i["body"],
                rec_i["why"] or "",
                existing_j,
            )
            for c in conflicts:
                contradiction_flags.append(
                    f"- '{rec_i['name']}' vs '{c['name']}': {c['reason']}"
                )

    # 2. Unknown vintage & 3. Conditional lessons
    for rec in records:
        why = (rec["why"] or "").strip()
        when = (rec["when"] or "").strip()

        # Unknown vintage: missing 'why' means no situation was recorded,
        # so staleness cannot be evaluated.
        if not why and not when:
            vintage_flags.append(
                f"- '{rec['name']}': no provenance recorded (missing 'why' and 'when'); "
                f"cannot be judged for staleness because no situation or trigger was provided"
            )
        elif not why:
            vintage_flags.append(
                f"- '{rec['name']}': missing 'why' provenance; "
                f"cannot be judged for staleness because no situation or trigger was provided"
            )
        elif not when:
            vintage_flags.append(
                f"- '{rec['name']}': missing 'when' timestamp; provenance date is unknown"
            )

        # Conditional lessons: look for transient triggers in recorded why
        if why:
            matched_kw = find_conditional_keywords(why)
            if matched_kw:
                kw_str = ", ".join(f"'{k}'" for k in matched_kw)
                conditional_flags.append(
                    f"- '{rec['name']}': conditional trigger keyword(s) {kw_str} "
                    f"in why: \"{why}\""
                )

    header = "# Memory review"
    if len(entries) > len(examined_entries):
        header += f" (showing analysis for first {len(examined_entries)} of {len(entries)} memories)"

    # Clean baseline: if all memories are consistent and well-formed
    if not contradiction_flags and not vintage_flags and not conditional_flags:
        return f"{header}\n\nAll memories appear consistent with known provenance; no flags raised."

    sections = [header, ""]

    def _append_section(title: str, flags: list[str], disclaimer: str | None = None):
        sections.append(f"## {title}")
        if not flags:
            sections.append("(none detected)")
        else:
            capped = flags[:MAX_FLAGS_PER_SECTION]
            sections.extend(capped)
            if len(flags) > len(capped):
                sections.append(f"  (showing first {len(capped)} of {len(flags)} findings)")
        if disclaimer and flags:
            sections.append("")
            sections.append(disclaimer)
        sections.append("")

    _append_section("Mutual contradictions", contradiction_flags)
    _append_section("Unknown vintage", vintage_flags)
    _append_section(
        "Conditional lessons",
        conditional_flags,
        disclaimer=(
            "Note: Conditional lesson detection relies on keyword heuristics in recorded "
            "provenance ('why'). It cannot verify whether external conditions or bugs "
            "have actually been resolved; verify before acting."
        ),
    )

    return "\n".join(sections).rstrip()

