import os
import stat
from pathlib import Path

from hermes_link.executor import run_hermes_task


def make_fake_hermes(path: Path):
    path.write_text("#!/usr/bin/env python3\nimport sys\nprint('ARGS=' + repr(sys.argv[1:]))\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_executor_runs_hermes_without_shell_joining_prompt(tmp_path, monkeypatch):
    fake = tmp_path / "hermes"
    make_fake_hermes(fake)
    monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(fake))

    result = run_hermes_task("hello; rm -rf /", options={"profile": "remote", "toolsets": "terminal,file", "timeout_seconds": 5})

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert "chat" in result.stdout
    assert "hello; rm -rf /" in result.stdout
    assert "--profile" in result.stdout
    assert "--toolsets" in result.stdout


def test_executor_reports_timeout(tmp_path, monkeypatch):
    fake = tmp_path / "hermes"
    fake.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(2)\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(fake))

    result = run_hermes_task("slow", options={"timeout_seconds": 1})

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert "timed out" in result.stderr.lower()
