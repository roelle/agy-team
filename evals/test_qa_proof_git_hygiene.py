import pytest
from unittest.mock import patch
import io
import contextlib
from agyteam import lifecycle

def test_lifecycle_silent_degradation_on_hygiene_exception():
    """Prove that a crash in git hygiene check is incorrectly swallowed and hidden from users."""
    status = {
        "team_dir": "/tmp/test",
        "stopped": False,
        "stop_reason": "",
        "workspaces": [],
        "agents": [],
        "git_hygiene": {"is_git": False, "clean": True, "error": "Simulated git checkout crash"}
    }
    
    out = io.StringIO()
    with patch("agyteam.lifecycle.team_status", return_value=status):
        with contextlib.redirect_stdout(out):
            lifecycle.main(["status"])
    
    output = out.getvalue()
    assert "Simulated git checkout crash" in output, "Silent degradation detected: git hygiene exception swallowed."

if __name__ == '__main__':
    pytest.main([__file__, *__import__('sys').argv[1:]])
