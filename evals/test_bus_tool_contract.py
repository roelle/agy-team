"""A tool call the model got wrong must come back with the call that works.

Both findings here were measured on a host that does not inject MCP schemas
into the system prompt. An agent reliably calls only a tool whose signature it
knows, and when it does not know one it guesses — so the quality of the error
decides whether the next attempt is better or merely different.

A manager made nine consecutive failed delegation attempts, cycling
{message}, {content}, {content, to}, against a tool that wanted (to, content).
It delegated nothing for an entire run. The bus log ended with two lines. The
error it was given each time, in full: `[error: 'to']`.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import persona  # noqa: E402
from agyteam.mcp_bus import ADMIN_TOOLS, TOOLS, _args, _BadArgs  # noqa: E402


# --- U1: the error names the tool, the argument, and the call form ----------

@pytest.mark.parametrize("args", [
    {},
    {"message": "do the thing"},           # the shape the model actually tried
    {"content": "do the thing"},
    {"to": "coder"},
    {"to": "coder", "content": "   "},     # present but empty is still missing
    {"to": "", "content": "x"},
    {"to": None, "content": "x"},
])
def test_a_bad_call_explains_itself(args):
    with pytest.raises(_BadArgs) as e:
        _args("send_to_teammate", args, "to", "content")
    msg = str(e.value)
    assert "send_to_teammate" in msg, "an error that names no tool is unactionable"
    assert "send_to_teammate(to=..., content=...)" in msg, "no call form given"
    assert msg != "[error: 'to']"


def test_the_error_says_which_argument_is_missing():
    with pytest.raises(_BadArgs) as e:
        _args("send_to_teammate", {"to": "coder"}, "to", "content")
    assert "content" in str(e.value)
    # and does not accuse the one that was supplied
    assert "argument(s): content." in str(e.value)


def test_a_good_call_passes_through_unchanged():
    assert _args("send_to_teammate", {"to": " coder ", "content": "  hi  "},
                 "to", "content") == ("coder", "  hi  ")


def test_message_bodies_keep_their_whitespace():
    """Stripping a recipient is tidying; stripping a message is data loss."""
    body = "line one\n\n    indented\n"
    assert _args("x", {"to": "c", "content": body}, "to", "content")[1] == body


# --- U2: the brief carries the signatures ----------------------------------

def test_every_bus_tool_appears_in_the_brief():
    index = persona.tool_index()
    for t in TOOLS:
        assert f"- {t['name']}(" in index, f"{t['name']} missing from the brief"


def test_the_index_is_generated_not_transcribed():
    """A hand-written list drifts from the declarations; this one cannot.

    Proven by changing a declaration and watching the index follow.
    """
    spec = next(t for t in TOOLS if t["name"] == "send_to_teammate")
    props = spec["inputSchema"]["properties"]
    props["urgency"] = {"type": "string", "description": "temporary"}
    try:
        assert "send_to_teammate(to, content, [urgency])" in persona.tool_index()
    finally:
        del props["urgency"]
    assert "send_to_teammate(to, content)" in persona.tool_index()


def test_required_and_optional_are_distinguishable():
    index = persona.tool_index()
    assert "record_review(what, author, verdict, proof_file, [findings])" in index
    assert "check_inbox()" in index


def test_admin_tools_are_absent_unless_asked_for():
    assert "roster_add(" not in persona.tool_index()
    assert "roster_add(name, role)" in persona.tool_index(admin=True)
    assert all(t["name"] in persona.tool_index(admin=True) for t in ADMIN_TOOLS)


def test_the_brief_itself_carries_the_index():
    text = persona.brief("coder", [{"name": "coder", "role": "implements"},
                                   {"name": "qa", "role": "reviews"}])
    assert "send_to_teammate(to, content)" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
