import json
from pathlib import Path

from hermes_link.config import LinkConfig, LinkPaths, load_config, resolve_paths, save_config


def test_resolve_paths_prefers_link_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    link_home = tmp_path / "explicit-link"
    paths = resolve_paths(home=link_home)
    assert paths.link_home == link_home
    assert paths.db_path == link_home / "link.db"
    assert paths.config_path == link_home / "config.json"


def test_resolve_paths_uses_hermes_home_link_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_LINK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    paths = resolve_paths()
    assert paths.link_home == tmp_path / "hermes" / "link"


def test_save_and_load_config_roundtrip(tmp_path):
    paths = LinkPaths(tmp_path, tmp_path / "link.db", tmp_path / "config.json")
    config = LinkConfig(node_id="node-a", display_name="Node A", base_url="http://127.0.0.1:8765", capabilities={"profiles": ["default"]})
    save_config(paths, config)
    loaded = load_config(paths)
    assert loaded == config
    assert json.loads(paths.config_path.read_text())["node_id"] == "node-a"
