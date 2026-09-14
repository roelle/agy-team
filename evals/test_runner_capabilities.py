"""Runners must declare what they cannot do, and tools must honour it.

The rule these pin down: a check that could not run must never be reported as
a check that found nothing. It already governs test files that collect no
tests and a bench that cannot be verified; these apply it to the runner seam,
where two documented "invariants" are actually runner-provided.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.runner import Runner  # noqa: E402
from agyteam.runner_agy import AgyRunner  # noqa: E402
from agyteam.runner_template import MyRunner  # noqa: E402


def test_base_class_defaults_to_no_capability():
    """The honest default for "can you do this?" is no."""
    assert Runner.supports_audit is False
    assert Runner.supports_containment is False


def test_template_declares_nothing_it_has_not_implemented():
    assert MyRunner.supports_audit is False
    assert MyRunner.supports_containment is False


def test_external_host_runner_declares_both_false():
    """It has no workspace mechanism and installs no audit hook."""
    assert AgyRunner.supports_audit is False
    assert AgyRunner.supports_containment is False


def test_sdk_runner_declares_both_true_if_importable():
    try:
        from agyteam.runner_sdk import SdkRunner
    except Exception:
        return          # dependency absent; nothing to assert
    assert SdkRunner.supports_audit is True
    assert SdkRunner.supports_containment is True


def test_status_reports_capabilities():
    from agyteam import lifecycle
    caps = lifecycle.runner_capabilities()
    assert "supports_audit" in caps and "supports_containment" in caps
    assert "spec" in caps


def _grade(audit_path, env_extra=None):
    import os
    env = {k: v for k, v in os.environ.items() if k != "AGYTEAM_AUDIT_LOG"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(ROOT / "bench/grade.py"), "audit_telemetry",
         "--since", "2000-01-01 00:00", "--team-dir", "/nonexistent",
         "--audit", str(audit_path)],
        capture_output=True, text=True, env=env).stdout


def test_grader_refuses_to_score_evidence_it_cannot_see():
    """The whole point: N/A, not zero, and it says which.

    Scoring zero here once produced "every defect named and nothing executed,
    check whether the key leaked" about a team that had run 33 commands.
    """
    out = _grade("/nonexistent/audit.jsonl")
    assert "N/A" in out, out
    assert "not scored" in out, out
    assert "NOT the same as" in out, out
    # and the denominator must shrink rather than carry unscoreable points
    assert "/9" not in out, out


def test_grader_does_not_blank_checks_that_read_the_bus():
    """Overstating blindness is the same error pointed the other way."""
    out = _grade("/nonexistent/audit.jsonl")
    reproduction = [ln for ln in out.splitlines() if "concrete reproduction" in ln]
    assert reproduction, out
    assert "N/A" not in reproduction[0], reproduction[0]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
