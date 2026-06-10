import json

from hermes_link.protocol import json_response, parse_json_body, task_public_dict
from hermes_link.models import LinkTask


def test_parse_json_body_rejects_invalid_json():
    assert parse_json_body(b'{"ok": true}')["ok"] is True
    try:
        parse_json_body(b"not-json")
    except ValueError as exc:
        assert "invalid json" in str(exc).lower()
    else:
        raise AssertionError("invalid json should raise")


def test_task_public_dict_omits_large_stdout_by_default():
    task = LinkTask.new("node-b", "prompt", {})
    task.stdout = "x" * 2000
    public = task_public_dict(task)
    assert "stdout" not in public
    full = task_public_dict(task, include_result=True)
    assert full["stdout"] == task.stdout


def test_task_public_dict_decodes_bytes_results():
    task = LinkTask.new("node-b", "prompt", {})
    task.stdout = b"hello \xe2\x9c\x93"  # type: ignore[assignment]
    task.stderr = b"bad \xff"  # type: ignore[assignment]
    full = task_public_dict(task, include_result=True)
    assert full["stdout"] == "hello ✓"
    assert full["stderr"] == "bad �"
    json.dumps(full)
