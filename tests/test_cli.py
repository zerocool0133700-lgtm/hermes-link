import json
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
    for subcommand in ["init", "serve", "pair", "nodes", "send", "status", "result"]:
        assert subcommand in result.stdout


def test_init_creates_config_and_nodes_lists_self(tmp_path):
    home = tmp_path / "link-home"
    result = run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    assert result.returncode == 0, result.stderr
    assert "box-a" in result.stdout

    config = json.loads((home / "config.json").read_text())
    assert config["node_id"] == "box-a"
    assert config["display_name"] == "Box A"

    nodes = run_cli("nodes", "--home", str(home))
    assert nodes.returncode == 0, nodes.stderr
    assert "box-a" in nodes.stdout


def test_send_requires_known_paired_node(tmp_path):
    home = tmp_path / "link-home"
    run_cli("init", "--node-id", "box-a", "--name", "Box A", "--home", str(home))
    result = run_cli("send", "missing", "hello", "--home", str(home))
    assert result.returncode != 0
    assert "unknown paired node" in result.stderr.lower()
