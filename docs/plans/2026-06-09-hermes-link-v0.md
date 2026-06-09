# Hermes Link v0 Implementation Plan

> **For Hermes:** Use `subagent-driven-development` or a direct TDD loop to implement this plan task-by-task.

**Goal:** Build a minimal LAN connector that lets one Hermes Agent system pair with another and dispatch a remote `hermes chat -q` task with status/result retrieval.

**Architecture:** v0 is a small Python package plus CLI. Each node stores configuration under its own Hermes home, exposes an HTTP receiver bound to LAN/local addresses, authenticates requests with signed bearer tokens created by a pairing flow, and executes remote tasks through bounded subprocesses. The protocol is JSON over HTTP so it can later become a Hermes toolset, gateway adapter, or built-in `hermes link` command.

**Tech Stack:** Python 3.11+, standard library first (`argparse`, `http.server`, `sqlite3`, `subprocess`, `secrets`, `hmac`, `hashlib`, `json`, `threading`), pytest for tests. Avoid external dependencies in v0 unless the standard-library HTTP server becomes too limiting.

---

## v0 Scope

### In scope

- Local project at `/home/dave/hermes-link`.
- A standalone executable module, e.g. `python -m hermes_link`.
- Receiver HTTP API:
  - `GET /health`
  - `GET /nodes/self`
  - `POST /pair/start`
  - `POST /pair/complete`
  - `POST /tasks`
  - `GET /tasks/<task_id>`
  - `GET /tasks/<task_id>/result`
- SQLite state file for nodes, pairings, tasks, and audit events.
- One-time pairing token for LAN pairing.
- HMAC request signing after pairing.
- Remote task execution via `hermes chat -q` with configurable profile/toolsets/workdir.
- Task timeout and output capture.
- Tests for protocol, signing, persistence, and task lifecycle using a fake Hermes command.

### Out of scope for v0

- NAT traversal or internet relay.
- Automatic best-node routing.
- Shared memory/session DB synchronization.
- Arbitrary remote file transfer.
- Full Hermes core integration.
- Multi-tenant auth beyond paired-node permissions.

---

## Directory layout

```text
/home/dave/hermes-link/
├── README.md
├── docs/
│   └── plans/
│       └── 2026-06-09-hermes-link-v0.md
├── hermes_link/
│   ├── __init__.py
│   ├── __main__.py
│   ├── audit.py
│   ├── cli.py
│   ├── config.py
│   ├── crypto.py
│   ├── executor.py
│   ├── models.py
│   ├── protocol.py
│   ├── server.py
│   └── store.py
└── tests/
    ├── test_crypto.py
    ├── test_store.py
    ├── test_protocol.py
    ├── test_executor.py
    └── test_server.py
```

---

## Data model

### Node record

```json
{
  "node_id": "dave-lab",
  "display_name": "Dave Lab Hermes",
  "base_url": "http://192.168.1.10:8765",
  "capabilities": {
    "profiles": ["default"],
    "toolsets": ["terminal", "file", "web"],
    "max_task_seconds": 600
  }
}
```

### Pairing record

```json
{
  "peer_node_id": "gpu-box",
  "peer_base_url": "http://192.168.1.11:8765",
  "shared_secret": "base64url secret, stored local only",
  "trust_level": "dispatch",
  "created_at": "iso timestamp"
}
```

### Task record

```json
{
  "task_id": "linktask_...",
  "peer_node_id": "dave-lab",
  "prompt": "Summarize this repo",
  "status": "queued|running|succeeded|failed|cancelled|timed_out",
  "created_at": "iso timestamp",
  "started_at": "iso timestamp or null",
  "finished_at": "iso timestamp or null",
  "exit_code": 0,
  "stdout": "final Hermes output",
  "stderr": "process stderr or error"
}
```

---

## Protocol sketch

### Signed request headers

After pairing, every mutating/read-task request uses:

```text
X-Hermes-Link-Node: <sender_node_id>
X-Hermes-Link-Timestamp: <unix_seconds>
X-Hermes-Link-Nonce: <random_hex>
X-Hermes-Link-Signature: <hex hmac sha256>
```

Signature payload:

```text
<method>\n<path>\n<timestamp>\n<nonce>\n<sha256(body).hex()>
```

Verification rules:

- Reject unknown node.
- Reject timestamp skew over 300 seconds.
- Reject reused nonce for the same peer within timestamp window.
- Use `hmac.compare_digest`.
- Audit every rejected signature without logging secrets.

---

## Task 1: Initialize Python package and CLI skeleton

**Objective:** Create importable package and baseline CLI.

**Files:**
- Create: `hermes_link/__init__.py`
- Create: `hermes_link/__main__.py`
- Create: `hermes_link/cli.py`
- Create: `tests/test_cli.py`

**Steps:**

1. Write a failing CLI smoke test that runs `python -m hermes_link --help` and expects `Hermes Link` in output.
2. Implement `argparse` with subcommands: `init`, `serve`, `pair`, `nodes`, `send`, `status`, `result`.
3. Keep subcommands as stubs returning clear `not implemented` messages except `--help`.
4. Verify:

```bash
python -m pytest tests/test_cli.py -q
python -m hermes_link --help
```

**Acceptance criteria:**

- Package imports.
- Help output works.
- Tests pass.

---

## Task 2: Add config and state path handling

**Objective:** Resolve Hermes Link state paths in a profile-safe way.

**Files:**
- Create: `hermes_link/config.py`
- Test: `tests/test_config.py`

**Implementation notes:**

- Use `HERMES_HOME` if set, else `~/.hermes`.
- Default Link dir: `$HERMES_HOME/link/`.
- State DB: `$HERMES_HOME/link/link.db`.
- Config JSON: `$HERMES_HOME/link/config.json`.
- Project-local dev override: `HERMES_LINK_HOME`.

**Verification:**

```bash
python -m pytest tests/test_config.py -q
```

---

## Task 3: Implement SQLite store

**Objective:** Persist nodes, pairings, tasks, nonces, and audit events.

**Files:**
- Create: `hermes_link/store.py`
- Create: `hermes_link/models.py`
- Test: `tests/test_store.py`

**Tables:**

- `nodes(node_id primary key, display_name, base_url, capabilities_json, created_at, updated_at)`
- `pairings(peer_node_id primary key, peer_base_url, shared_secret, trust_level, created_at)`
- `tasks(task_id primary key, peer_node_id, prompt, options_json, status, created_at, started_at, finished_at, exit_code, stdout, stderr)`
- `nonces(peer_node_id, nonce, seen_at, primary key(peer_node_id, nonce))`
- `audit(id integer primary key, at, event_type, peer_node_id, task_id, summary, details_json)`

**Verification:**

```bash
python -m pytest tests/test_store.py -q
```

---

## Task 4: Implement HMAC signing and verification

**Objective:** Sign and verify paired-node requests.

**Files:**
- Create: `hermes_link/crypto.py`
- Test: `tests/test_crypto.py`

**Test cases:**

- Valid request verifies.
- Body tampering fails.
- Method/path tampering fails.
- Timestamp skew fails.
- Reused nonce fails.
- Unknown node fails at protocol layer.

**Verification:**

```bash
python -m pytest tests/test_crypto.py -q
```

---

## Task 5: Implement task executor with fake-Hermes tests

**Objective:** Run `hermes chat -q` safely and capture result.

**Files:**
- Create: `hermes_link/executor.py`
- Test: `tests/test_executor.py`

**Implementation notes:**

- Default command: `hermes chat -q <prompt>`.
- Allow env override for tests: `HERMES_LINK_HERMES_BIN`.
- Optional options:
  - `profile`
  - `toolsets`
  - `workdir`
  - `timeout_seconds`
- Use `subprocess.run(..., timeout=..., capture_output=True, text=True)`.
- Do not shell-join the prompt.
- Enforce max timeout from node config.

**Verification:**

```bash
python -m pytest tests/test_executor.py -q
```

---

## Task 6: Implement HTTP receiver

**Objective:** Expose v0 JSON HTTP API.

**Files:**
- Create: `hermes_link/server.py`
- Create: `hermes_link/protocol.py`
- Test: `tests/test_server.py`

**Endpoints:**

- `GET /health`: unsigned, returns ok.
- `GET /nodes/self`: unsigned, returns receiver identity and public capabilities.
- `POST /pair/start`: unsigned, creates one-time token if local operator started pairing mode.
- `POST /pair/complete`: token-authenticated, stores peer pairing and returns receiver node info.
- `POST /tasks`: signed, creates queued task and starts worker thread.
- `GET /tasks/<task_id>`: signed, returns metadata without full stdout unless small.
- `GET /tasks/<task_id>/result`: signed, returns final stdout/stderr/exit status.

**Verification:**

```bash
python -m pytest tests/test_server.py -q
```

---

## Task 7: Implement CLI commands end-to-end

**Objective:** Make the user-facing commands work for two boxes.

**Files:**
- Modify: `hermes_link/cli.py`
- Test: `tests/test_cli.py`

**Commands:**

```bash
python -m hermes_link init --node-id dave-lab --name "Dave Lab Hermes"
python -m hermes_link serve --host 0.0.0.0 --port 8765
python -m hermes_link pair http://192.168.1.11:8765
python -m hermes_link nodes
python -m hermes_link send gpu-box "What OS are you running?"
python -m hermes_link status <task-id>
python -m hermes_link result <task-id>
```

**Acceptance criteria:**

- CLI prints copy-pasteable next steps.
- Pairing tokens are not logged after use.
- `nodes` shows paired nodes and last-seen time.

---

## Task 8: Add manual two-box test guide

**Objective:** Document exactly how to test on Dave's two Hermes boxes.

**Files:**
- Create: `docs/two-box-lan-test.md`

**Guide contents:**

1. Find each box's LAN IP.
2. Initialize node identity on each box.
3. Start receiver on box B.
4. Pair from box A.
5. Send a harmless task:

```bash
python -m hermes_link send box-b "Run date and hostname, then summarize the result."
```

6. Poll status.
7. Fetch result.
8. Inspect audit log.
9. Stop receiver.

**Verification:**

- The guide has commands for both boxes.
- The guide includes firewall/port notes.
- The guide includes how to revoke a pairing.

---

## Task 9: Add security review checklist

**Objective:** Prevent v0 from normalizing unsafe remote control.

**Files:**
- Create: `docs/security-checklist.md`

**Checklist:**

- Pairing required before dispatch.
- Signed requests required after pairing.
- Nonce replay rejection.
- Timestamp skew rejection.
- No secrets in audit logs.
- Task prompt logged only if local config allows it; otherwise log prompt hash.
- Receiver binds to localhost by default.
- LAN bind requires explicit flag.
- Remote task timeout enforced.
- No shell invocation for prompt execution.
- Pairing revocation documented.

---

## Verification commands for the whole project

```bash
python -m pytest -q
python -m hermes_link --help
python -m hermes_link init --node-id test-node --name "Test Node" --home /tmp/hermes-link-test
python -m hermes_link nodes --home /tmp/hermes-link-test
```

---

## First real two-box acceptance test

On box B:

```bash
cd /home/dave/hermes-link
python -m hermes_link init --node-id box-b --name "Hermes Box B"
python -m hermes_link serve --host 0.0.0.0 --port 8765
```

On box A:

```bash
cd /home/dave/hermes-link
python -m hermes_link init --node-id box-a --name "Hermes Box A"
python -m hermes_link pair http://<box-b-lan-ip>:8765
python -m hermes_link send box-b "Run hostname and date using tools, then reply with both values."
python -m hermes_link status <task-id>
python -m hermes_link result <task-id>
```

Expected outcome:

- Box A can see box B as a paired node.
- Box B receives and audits the task.
- Box B runs Hermes locally.
- Box A retrieves the final result.
- No secret or token appears in command output or audit details.
