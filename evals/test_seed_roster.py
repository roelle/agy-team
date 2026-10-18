"""The roster install.sh seeds must run on whichever runtime installs it.

It pinned manager and qa to "gemini-3.1-pro-preview", an SDK model id. The
agy CLI -- the default runner -- does not know that name, so on a fresh CLI
install both oversight roles failed every turn ("invalid model selection").
Model names belong to the runtime; the seed leaves them to agyteam.models.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def seeded() -> dict:
    text = (ROOT / "plugin" / "install.sh").read_text()
    body = re.search(r"<<JSON\n(.*?)\nJSON\n", text, re.S).group(1)
    return json.loads(body.replace("$TEAM", "t"))


def test_the_seed_pins_no_model():
    pinned = {a["name"]: a["model"] for a in seeded()["agents"] if a.get("model")}
    assert not pinned, f"runtime-specific model names in the seed: {pinned}"


def test_the_seed_says_how_to_choose_one():
    assert "agyteam.models set" in seeded()["_note"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
