"""Tests for team lifecycle migration primitives (export/import), tar-slip defenses,
and capability-governed agent roles.
"""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agyteam import lifecycle, manager_seed, persona, roster as roster_lib
from agyteam.supervisor import Supervisor, _find_principal
from agyteam.transport import load as load_transport
from fixture_runner import ScriptedRunner


def test_export_import_roundtrip():
    """Test export_team and import_team preserving roster, norms, reviews, inbox, and memory."""
    with tempfile.TemporaryDirectory(prefix="agy-test-export-") as src_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-import-") as dest_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-bundle-") as bundle_dir_raw:

        src_dir = Path(src_dir_raw)
        dest_dir = Path(dest_dir_raw)
        bundle_path = Path(bundle_dir_raw) / "team_backup.tar.gz"

        # 1. Setup source team directory
        roster_data = {
            "mission": "Explore lifecycle migration",
            "agents": [
                {"name": "alice", "role": "Principal architect", "is_principal": True},
                {"name": "bob", "role": "Quality engineer", "is_gatekeeper": True},
                {"name": "carol", "role": "Process driver", "is_retro_leader": True},
            ],
            "workspaces": [str(src_dir / "workspaces" / "default")],
        }
        (src_dir / "roster.json").write_text(json.dumps(roster_data, indent=2))
        (src_dir / "NORMS.md").write_text("# Team Norms\n- Strict verification\n")
        (src_dir / "reviews.jsonl").write_text('{"what": "feat", "verdict": "approved"}\n')
        (src_dir / "bus.jsonl").write_text('{"sender": "alice", "recipient": "bob", "content": "hi"}\n')
        (src_dir / "observer.jsonl").write_text('{"event": "start"}\n')

        # Agent memories
        alice_mem = src_dir / "agents" / "alice" / "memory"
        alice_mem.mkdir(parents=True)
        (alice_mem / "core-arch.md").write_text("# Core Architecture\nMemory content\n")
        (src_dir / "agents" / "alice" / "MEMORY.md").write_text("- [core-arch] Architecture notes\n")

        # 2. Export team
        exp_res = lifecycle.export_team(bundle_path, team_dir=src_dir)
        assert exp_res["status"] == "ok"
        assert bundle_path.is_file()
        assert "alice" in exp_res["agents"]
        assert "bob" in exp_res["agents"]
        assert "carol" in exp_res["agents"]
        assert set(exp_res["components"]) == {"roster", "norms", "reviews", "inbox", "observer", "memory"}

        # Check tar manifest contents
        with tarfile.open(bundle_path, "r:*") as tar:
            m_info = tar.getmember("manifest.json")
            m_f = tar.extractfile(m_info)
            manifest = json.loads(m_f.read().decode("utf-8"))
            assert manifest["format_version"] == "1.0"
            assert "timestamp" in manifest
            assert manifest["agents"] == ["alice", "bob", "carol"]
            assert "manifest.json" in tar.getnames()
            assert "roster.json" in tar.getnames()
            assert "NORMS.md" in tar.getnames()
            assert "reviews.jsonl" in tar.getnames()
            assert "bus.jsonl" in tar.getnames()
            assert "observer.jsonl" in tar.getnames()
            assert "agents/alice/memory/core-arch.md" in tar.getnames()

        # 3. Import into destination
        imp_res = lifecycle.import_team(bundle_path, team_dir=dest_dir)
        assert imp_res["status"] == "ok"
        assert imp_res["agents"] == ["alice", "bob", "carol"]

        # Validate fidelity of restored contents
        assert (dest_dir / "roster.json").exists()
        restored_roster = json.loads((dest_dir / "roster.json").read_text())
        assert restored_roster["agents"][0]["name"] == "alice"
        assert restored_roster["agents"][0]["is_principal"] is True

        assert (dest_dir / "NORMS.md").read_text() == "# Team Norms\n- Strict verification\n"
        assert (dest_dir / "reviews.jsonl").read_text() == '{"what": "feat", "verdict": "approved"}\n'
        assert (dest_dir / "bus.jsonl").read_text() == '{"sender": "alice", "recipient": "bob", "content": "hi"}\n'
        assert (dest_dir / "observer.jsonl").read_text() == '{"event": "start"}\n'
        assert (dest_dir / "agents" / "alice" / "memory" / "core-arch.md").read_text() == "# Core Architecture\nMemory content\n"


def test_export_selective_exclusions():
    """Test export with selective flags: no-memory, no-inbox, no-observer."""
    with tempfile.TemporaryDirectory(prefix="agy-test-selective-") as src_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-bundle-") as bundle_dir_raw:

        src_dir = Path(src_dir_raw)
        bundle_path = Path(bundle_dir_raw) / "selective.tar.gz"

        (src_dir / "roster.json").write_text(json.dumps({"agents": [{"name": "coder"}]}))
        (src_dir / "bus.jsonl").write_text('{"msg": 1}\n')
        (src_dir / "observer.db").write_text("sqlite-mock")
        (src_dir / "agents" / "coder" / "memory").mkdir(parents=True)
        (src_dir / "agents" / "coder" / "memory" / "note.md").write_text("note")

        exp_res = lifecycle.export_team(
            bundle_path,
            team_dir=src_dir,
            include_memory=False,
            include_inbox=False,
            include_observer=False,
        )
        assert set(exp_res["components"]) == {"roster"}

        with tarfile.open(bundle_path, "r:*") as tar:
            names = tar.getnames()
            assert "manifest.json" in names
            assert "roster.json" in names
            assert "bus.jsonl" not in names
            assert "observer.db" not in names
            assert not any(n.startswith("agents/") for n in names)


def test_import_overwrite_protection():
    """Test that import_team refuses to overwrite existing non-empty directory unless overwrite=True."""
    with tempfile.TemporaryDirectory(prefix="agy-test-overwrite-") as dest_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-bundle-") as bundle_dir_raw:

        dest_dir = Path(dest_dir_raw)
        bundle_path = Path(bundle_dir_raw) / "bundle.tar.gz"

        # Create a valid archive
        manifest = {
            "format_version": "1.0",
            "timestamp": "2026-09-12T00:00:00Z",
            "team_name": "team",
            "agents": ["coder"],
            "components": ["roster"],
            "file_count": 2,
        }
        with tarfile.open(bundle_path, "w:gz") as tar:
            m_bytes = json.dumps(manifest).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(m_bytes)
            tar.addfile(ti, io.BytesIO(m_bytes))

            r_bytes = json.dumps({"agents": [{"name": "coder"}]}).encode("utf-8")
            ti_r = tarfile.TarInfo("roster.json")
            ti_r.size = len(r_bytes)
            tar.addfile(ti_r, io.BytesIO(r_bytes))

        # Put an existing file in dest_dir
        (dest_dir / "existing_work.txt").write_text("do not overwrite me!")

        # Overwrite=False must raise FileExistsError
        with pytest.raises(FileExistsError) as exc_info:
            lifecycle.import_team(bundle_path, team_dir=dest_dir, overwrite=False)
        assert "not empty" in str(exc_info.value)
        assert (dest_dir / "existing_work.txt").read_text() == "do not overwrite me!"
        assert not (dest_dir / "roster.json").exists()

        # Overwrite=True succeeds
        res = lifecycle.import_team(bundle_path, team_dir=dest_dir, overwrite=True)
        assert res["status"] == "ok"
        assert (dest_dir / "roster.json").exists()
        assert (dest_dir / "existing_work.txt").exists()


@pytest.mark.parametrize("malicious_name", [
    "/etc/shadow",
    "//root/.ssh/id_rsa",
    "C:\\Windows\\system32\\cmd.exe",
    "../outside.txt",
    "sub/../../escape.txt",
    "a/b/../../../evil.sh",
])
def test_tar_slip_defense_malicious_paths(malicious_name):
    """Test that tar-slip pre-extraction rejects absolute paths and traversal attacks."""
    with tempfile.TemporaryDirectory(prefix="agy-test-tarslip-") as dest_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-bundle-") as bundle_dir_raw:

        dest_dir = Path(dest_dir_raw)
        bundle_path = Path(bundle_dir_raw) / "malicious.tar.gz"

        manifest = {"format_version": "1.0", "timestamp": "now", "agents": []}
        with tarfile.open(bundle_path, "w:gz") as tar:
            m_bytes = json.dumps(manifest).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(m_bytes)
            tar.addfile(ti, io.BytesIO(m_bytes))

            ti_mal = tarfile.TarInfo(malicious_name)
            payload = b"pwned"
            ti_mal.size = len(payload)
            tar.addfile(ti_mal, io.BytesIO(payload))

        with pytest.raises(ValueError) as exc_info:
            lifecycle.import_team(bundle_path, team_dir=dest_dir)
        assert "Tar-slip security violation" in str(exc_info.value)
        # Ensure nothing was extracted
        assert not any(dest_dir.iterdir())


def test_tar_slip_defense_escaping_symlink():
    """Test that symlinks pointing outside the destination directory are rejected."""
    with tempfile.TemporaryDirectory(prefix="agy-test-symlink-") as dest_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-bundle-") as bundle_dir_raw:

        dest_dir = Path(dest_dir_raw)
        bundle_path = Path(bundle_dir_raw) / "symlink.tar.gz"

        manifest = {"format_version": "1.0", "timestamp": "now", "agents": []}
        with tarfile.open(bundle_path, "w:gz") as tar:
            m_bytes = json.dumps(manifest).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(m_bytes)
            tar.addfile(ti, io.BytesIO(m_bytes))

            # Malicious symlink pointing to parent
            ti_sym = tarfile.TarInfo("evil_link")
            ti_sym.type = tarfile.SYMTYPE
            ti_sym.linkname = "../../outside"
            tar.addfile(ti_sym)

        with pytest.raises(ValueError) as exc_info:
            lifecycle.import_team(bundle_path, team_dir=dest_dir)
        assert "Tar-slip security violation" in str(exc_info.value)
        assert not any(dest_dir.iterdir())


def test_tar_slip_defense_device_node():
    """Test that special device or fifo files are rejected."""
    with tempfile.TemporaryDirectory(prefix="agy-test-dev-") as dest_dir_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-bundle-") as bundle_dir_raw:

        dest_dir = Path(dest_dir_raw)
        bundle_path = Path(bundle_dir_raw) / "dev.tar.gz"

        manifest = {"format_version": "1.0", "timestamp": "now", "agents": []}
        with tarfile.open(bundle_path, "w:gz") as tar:
            m_bytes = json.dumps(manifest).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(m_bytes)
            tar.addfile(ti, io.BytesIO(m_bytes))

            ti_fifo = tarfile.TarInfo("evil_fifo")
            ti_fifo.type = tarfile.FIFOTYPE
            tar.addfile(ti_fifo)

        with pytest.raises(ValueError) as exc_info:
            lifecycle.import_team(bundle_path, team_dir=dest_dir)
        assert "Tar-slip security violation" in str(exc_info.value)
        assert not any(dest_dir.iterdir())


def test_declared_capabilities_persona_brief():
    """Test that declared capabilities in roster take precedence over role/name heuristics in persona.brief."""
    agents = [
        {"name": "alice", "role": "Senior Engineer", "is_principal": True},
        {"name": "bob", "role": "Code Reviewer", "is_gatekeeper": True},
        {"name": "charlie", "role": "Faces outward to user", "is_principal": False},  # Heuristic overridden!
        {"name": "david", "role": "Engineer"},
    ]

    brief_alice = persona.brief("alice", agents=agents)
    assert "You represent the user's interests inside this team" in brief_alice
    assert "## Teammates and workers" not in brief_alice

    brief_bob = persona.brief("bob", agents=agents)
    assert "You represent the user's interests inside this team" in brief_bob
    assert "## Teammates and workers" not in brief_bob

    brief_charlie = persona.brief("charlie", agents=agents)
    # is_principal is explicitly False, so should NOT get PRINCIPAL text
    assert "## Teammates and workers" in brief_charlie
    assert "You represent the user's interests inside this team" not in brief_charlie

    brief_david = persona.brief("david", agents=agents)
    assert "## Teammates and workers" in brief_david
    assert "You represent the user's interests inside this team" not in brief_david


def test_declared_capabilities_supervisor_resolution():
    """Test that Supervisor resolves manager, principal, and retro leader via declared capabilities."""
    with tempfile.TemporaryDirectory(prefix="agy-test-caps-") as td_raw:
        td = Path(td_raw)
        roster_data = {
            "agents": [
                {"name": "lead", "role": "Tech Lead", "is_principal": True},
                {"name": "auditor", "role": "Security Gate", "is_gatekeeper": True},
                {"name": "scrum", "role": "Agile Facilitator", "is_retro_leader": True},
                {"name": "dev", "role": "Software Developer"},
            ]
        }
        (td / "roster.json").write_text(json.dumps(roster_data))

        # 1. _find_principal
        assert _find_principal(td, ["lead", "auditor", "scrum", "dev"]) == "lead"
        # If lead absent, auditor (gatekeeper) is next
        assert _find_principal(td, ["auditor", "scrum", "dev"]) == "auditor"

        # 2. Supervisor._find_manager
        runner = ScriptedRunner({})
        sup = Supervisor(
            agents=["lead", "auditor", "scrum", "dev"],
            team_dir=td,
            runner=runner,
        )
        assert sup._find_manager() == "lead"

        # 3. Supervisor._is_leader_accountable
        assert sup._is_leader_accountable("scrum") is True
        assert sup._is_leader_accountable("lead") is True
        assert sup._is_leader_accountable("auditor") is True
        assert sup._is_leader_accountable("dev") is False


def test_seed_from_roster():
    """Test manager_seed.seed_from_roster dynamically seeding craft memory by capability."""
    with tempfile.TemporaryDirectory(prefix="agy-test-seed-") as td_raw:
        td = Path(td_raw)
        roster_data = {
            "agents": [
                {"name": "lead", "role": "Tech Lead", "is_principal": True},
                {"name": "scrum", "role": "Agile Coach", "is_retro_leader": True},
                {"name": "dev", "role": "Developer"},
            ]
        }
        (td / "roster.json").write_text(json.dumps(roster_data))

        seeded = manager_seed.seed_from_roster(team_dir=td)
        assert "lead" in seeded
        assert "scrum" in seeded
        assert "dev" not in seeded

        # Check seeded memory names
        assert any("manage" in m for m in seeded["lead"])
        assert any("decompose" in m for m in seeded["scrum"])


def test_lifecycle_cli_add_agent_capabilities():
    """Test lifecycle CLI add-agent with --principal, --gatekeeper, --retro-leader flags."""
    with tempfile.TemporaryDirectory(prefix="agy-test-cli-") as td_raw:
        td = Path(td_raw)
        (td / "roster.json").write_text(json.dumps({"agents": []}))

        # Add agent with capabilities
        rc = lifecycle.main([
            "--team-dir", str(td),
            "add-agent", "custom_lead",
            "--role", "Custom Team Lead",
            "--principal",
            "--retro-leader",
        ])
        assert rc == 0

        roster = roster_lib.load(td / "roster.json")
        agent_entry = next(a for a in roster["agents"] if a["name"] == "custom_lead")
        assert agent_entry["role"] == "Custom Team Lead"
        assert agent_entry["is_principal"] is True
        assert agent_entry["is_retro_leader"] is True


def test_lifecycle_cli_export_import():
    """Test CLI export and import commands through lifecycle.main."""
    with tempfile.TemporaryDirectory(prefix="agy-test-cli-src-") as src_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-cli-dst-") as dst_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-cli-bundle-") as bnd_raw:

        src = Path(src_raw)
        dst = Path(dst_raw)
        bundle = Path(bnd_raw) / "cli_team.tar.gz"

        (src / "roster.json").write_text(json.dumps({"agents": [{"name": "cli_agent"}]}))
        (src / "NORMS.md").write_text("# CLI Norms\n")

        # CLI export
        rc_exp = lifecycle.main(["--team-dir", str(src), "export", str(bundle)])
        assert rc_exp == 0
        assert bundle.is_file()

        # CLI import
        rc_imp = lifecycle.main(["--team-dir", str(dst), "import", str(bundle)])
        assert rc_imp == 0
        assert (dst / "roster.json").exists()
        assert (dst / "NORMS.md").read_text() == "# CLI Norms\n"


def test_run_script_export_import():
    """Test ./run export and ./run import commands."""
    import subprocess
    run_bin = ROOT / "run"
    if not run_bin.is_file():
        pytest.skip("./run script not found")

    with tempfile.TemporaryDirectory(prefix="agy-test-run-src-") as src_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-run-dst-") as dst_raw, \
         tempfile.TemporaryDirectory(prefix="agy-test-run-bnd-") as bnd_raw:

        src = Path(src_raw)
        dst = Path(dst_raw)
        bundle = Path(bnd_raw) / "run_bundle.tar.gz"

        (src / "roster.json").write_text(json.dumps({"agents": [{"name": "run_agent"}]}))

        # Run export
        env = dict(os.environ, AGYTEAM_TEAM_DIR=str(src))
        proc_exp = subprocess.run([str(run_bin), "export", str(bundle)], env=env, capture_output=True, text=True)
        assert proc_exp.returncode == 0, f"export failed: {proc_exp.stderr}"
        assert bundle.is_file()

        # Run import
        env_dst = dict(os.environ, AGYTEAM_TEAM_DIR=str(dst))
        proc_imp = subprocess.run([str(run_bin), "import", str(bundle)], env=env_dst, capture_output=True, text=True)
        assert proc_imp.returncode == 0, f"import failed: {proc_imp.stderr}"
        assert (dst / "roster.json").exists()


def test_import_agent_memory_silent_overwrite_vulnerability(tmp_path):
    """Prove that import_team silently writes over existing agents outside the team dir."""
    import tarfile, json
    import agyteam.lifecycle as lifecycle
    
    # Pre-existing standard 'agents' directory with data
    base = tmp_path / "workspace"
    base.mkdir()
    
    agents_dir = base / "agents"
    agents_dir.mkdir()
    agents_dir.joinpath("victim.txt").write_text("critical user data")
    
    # Create bundle
    bundle = tmp_path / "bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        manifest = {"format_version": "1.0"}
        m_file = tmp_path / "manifest.json"
        m_file.write_text(json.dumps(manifest))
        tar.add(m_file, arcname="manifest.json")
        
        fake_agents = tmp_path / "fake"
        fake_agents.mkdir()
        fake_agents.joinpath("victim.txt").write_text("overwritten")
        tar.add(fake_agents, arcname="agents")

    team_dir = base / "team"
    lifecycle.import_team(str(bundle), team_dir=str(team_dir), overwrite=False)
    
    # This assertion points out the flaw:
    assert agents_dir.joinpath("victim.txt").read_text() == "critical user data", "Silent data overwrite occurred on memory outside team_dir"


def test_import_agent_memory_overwrite_allowed(tmp_path):
    """Verify that when overwrite=True, agent memories can be updated and new files are copied."""
    import tarfile, json
    import agyteam.lifecycle as lifecycle

    base = tmp_path / "workspace"
    base.mkdir()

    agents_dir = base / "agents"
    agents_dir.mkdir()
    agents_dir.joinpath("victim.txt").write_text("critical user data")

    bundle = tmp_path / "bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        manifest = {"format_version": "1.0"}
        m_file = tmp_path / "manifest.json"
        m_file.write_text(json.dumps(manifest))
        tar.add(m_file, arcname="manifest.json")

        fake_agents = tmp_path / "fake"
        fake_agents.mkdir()
        fake_agents.joinpath("victim.txt").write_text("overwritten")
        fake_agents.joinpath("new_agent.txt").write_text("brand new")
        tar.add(fake_agents, arcname="agents")

    team_dir = base / "team"
    lifecycle.import_team(str(bundle), team_dir=str(team_dir), overwrite=True)

    assert agents_dir.joinpath("victim.txt").read_text() == "overwritten"
    assert agents_dir.joinpath("new_agent.txt").read_text() == "brand new"

