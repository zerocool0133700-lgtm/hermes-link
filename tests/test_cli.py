import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "hermes_link", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_module_help_mentions_hermes_link():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "Hermes Link" in result.stdout
    for subcommand in ["init", "serve", "pair", "pair-token", "hub", "enroll", "worker", "hub-send", "hub-status", "revoke", "plugins", "profiles", "nodes", "introspect", "files", "sessions", "update-check", "send", "status", "result"]:
        assert subcommand in result.stdout


def test_init_creates_config_and_nodes_lists_self(tmp_path):
    home = tmp_path / "link-home"
    result = run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    assert result.returncode == 0, result.stderr
    assert "box-a" in result.stdout

    config = json.loads((home / "config.json").read_text())
    assert config["node_id"] == "box-a"
    assert config["display_name"] == "Box A"
    assert config["capabilities"]["introspection"] is True
    assert "plugins" in config["capabilities"]["introspection_kinds"]

    nodes = run_cli("nodes", "--home", str(home))
    assert nodes.returncode == 0, nodes.stderr
    assert "box-a" in nodes.stdout


def test_global_home_before_subcommand_is_preserved(tmp_path):
    home = tmp_path / "link-home"
    result = run_cli("--home", str(home), "init", "--node-id", "box-a", "--name", "Box A")
    assert result.returncode == 0, result.stderr
    assert (home / "config.json").exists()

    nodes = run_cli("--home", str(home), "nodes")
    assert nodes.returncode == 0, nodes.stderr
    assert "box-a" in nodes.stdout


def test_pair_token_create_persists_token_with_ttl(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))

    result = run_cli("pair-token", "create", "--ttl", "300", "--home", str(home))
    assert result.returncode == 0, result.stderr
    token = result.stdout.strip()
    assert token

    from hermes_link.store import LinkStore

    row = LinkStore(home / "link.db").get_pairing_token(token)
    assert row is not None
    assert row["expires_at"]


def test_revoke_removes_pairing(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))

    from hermes_link.models import PairingRecord
    from hermes_link.store import LinkStore

    store = LinkStore(home / "link.db")
    store.upsert_pairing(PairingRecord("box-b", "http://127.0.0.1:8765", "secret", "dispatch"))
    result = run_cli("revoke", "box-b", "--home", str(home))
    assert result.returncode == 0, result.stderr
    assert store.get_pairing("box-b") is None


def test_send_requires_known_paired_node(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    result = run_cli("send", "missing", "hello", "--home", str(home))
    assert result.returncode != 0
    assert "unknown paired node" in result.stderr.lower()


def test_plugins_requires_known_paired_node(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    result = run_cli("plugins", "missing", "--home", str(home))
    assert result.returncode != 0
    assert "unknown paired node" in result.stderr.lower()


def test_profiles_chat_rejects_non_link_profile_id(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    result = run_cli("profiles", "chat", "default", "hello", "--home", str(home))
    assert result.returncode != 0
    assert "remote profile id" in result.stderr.lower()


def test_profiles_chat_requires_known_paired_node(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    result = run_cli("profiles", "chat", "link:missing/default", "hello", "--home", str(home))
    assert result.returncode != 0
    assert "unknown paired node" in result.stderr.lower()
