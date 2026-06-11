# Hermes Link remote profiles

Remote profiles let a local Hermes UI or agent treat paired Hermes Link nodes as selectable chat targets.

This is intended for desktop/voice clients such as Hermes Desktop on Windows: the Windows app can show local profiles and mesh profiles in one selector, then relay chat to the selected remote Hermes profile over signed Hermes Link requests.

## Identity shape

A remote profile id has this form:

```text
link:<node-id>/<profile>
```

Examples:

```text
local:default
link:dave-ellie-labs/default
link:ellie-home2/default
link:windows-box/default
```

`local:<profile>` is a UI/local-runtime convenience. `link:<node>/<profile>` is the Hermes Link remote route.

## List profiles

On an operator node:

```bash
python -m hermes_link profiles list --probe
```

Machine-readable output for desktop clients:

```bash
python -m hermes_link profiles list --probe --json
```

The command includes:

- local profiles from this node's Link config
- paired remote profiles from live signed `/profiles` discovery when `--probe` is used
- paired/cached remote profiles from the local node registry when `--probe` is omitted

## Remote profile discovery endpoint

Remote profile discovery is signed-only:

```text
GET /profiles
```

Example response:

```json
{
  "kind": "profiles",
  "node_id": "dave-ellie-labs",
  "node_display_name": "Dave Ellie Labs",
  "profiles": [
    {
      "remote_profile_id": "link:dave-ellie-labs/default",
      "node_id": "dave-ellie-labs",
      "node_display_name": "Dave Ellie Labs",
      "profile": "default",
      "display_name": "Dave Ellie Labs / default",
      "capabilities": {
        "chat": true,
        "sessions": true,
        "files": true
      }
    }
  ]
}
```

Do not expose private profile/session inventory on public `/nodes/self`. Public self metadata may advertise that profile discovery exists, but detailed discovery belongs behind signed paired-node auth.

## Chat with a remote profile

```bash
python -m hermes_link profiles chat link:dave-ellie-labs/default "Hello from the Windows app"
```

Under the hood this sends a signed task request to `dave-ellie-labs` with:

```json
{
  "prompt": "Hello from the Windows app",
  "options": {
    "profile": "default"
  }
}
```

The remote node runs its own local Hermes command equivalent to:

```bash
hermes --profile default chat -q "Hello from the Windows app"
```

The remote node keeps its own:

- model/provider config
- tools and tool approvals
- memory and skills
- filesystem and working directories
- sessions
- audit log

## Windows Desktop integration sketch

Desktop clients should call the local Hermes/Link bridge for profile discovery and render a combined selector:

```text
Local
  default

Mesh
  Dave Ellie Labs / default
  Ellie Home2 / default
  Windows Hermes / default
```

When the user selects a `link:<node>/<profile>` target, the desktop client should route chat turns through Hermes Link rather than trying to run that remote profile locally.

Later, the same target id can back:

- remote session list/resume
- file attach into a remote session/task
- artifact return
- voice-first chat from the Windows app into a Linux Hermes backend

## Security notes

- `/profiles` requires a signed paired-node request.
- `profiles chat` requires an existing pairing and signs the task request.
- Remote nodes enforce their own approval/tool policy.
- File attach and full transcript/session read should stay separate capabilities.
