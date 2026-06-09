from hermes_link.models import LinkTask, NodeRecord, PairingRecord
from hermes_link.store import LinkStore


def test_store_roundtrips_nodes_pairings_tasks_and_audit(tmp_path):
    store = LinkStore(tmp_path / "link.db")
    store.init_schema()

    node = NodeRecord("node-a", "Node A", "http://127.0.0.1:8765", {"profiles": ["default"]})
    store.upsert_node(node)
    assert store.get_node("node-a").display_name == "Node A"

    pairing = PairingRecord("node-b", "http://127.0.0.1:8766", "secret", "dispatch")
    store.upsert_pairing(pairing)
    assert store.get_pairing("node-b").shared_secret == "secret"

    task = LinkTask.new(peer_node_id="node-b", prompt="hello", options={"timeout_seconds": 3})
    store.create_task(task)
    loaded = store.get_task(task.task_id)
    assert loaded.status == "queued"
    assert loaded.options["timeout_seconds"] == 3

    store.update_task(task.task_id, status="succeeded", stdout="ok", exit_code=0)
    assert store.get_task(task.task_id).stdout == "ok"

    store.add_audit("task.succeeded", peer_node_id="node-b", task_id=task.task_id, summary="ok", details={"safe": True})
    events = store.list_audit(limit=5)
    assert events[0]["event_type"] == "task.succeeded"


def test_nonce_insert_is_one_time(tmp_path):
    store = LinkStore(tmp_path / "link.db")
    store.init_schema()
    assert store.record_nonce("node-b", "nonce-1") is True
    assert store.record_nonce("node-b", "nonce-1") is False
