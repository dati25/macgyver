---
name: test-hook-locally
description: Run a Rossum hook payload against a local hook .py file to test it without deploying. Use when the user wants to test hook code, debug a hook, run a payload through local code, or iterate on a hook before pushing to Rossum. Triggers on requests like "test this hook", "run the payload", "try this locally", "debug the hook".
---

# Test a Rossum Hook Locally

Run a generated Rossum hook payload against a local hook Python file. Uses the `rossum_hook_request_handler` entry point convention.

## When to use

When the user wants to iterate on hook code without deploying — typical loop:

1. Generate a payload for a deployed hook.
2. Run it through the local version of the hook's `.py` file.
3. Inspect the result, adjust code, repeat.

## Workflow

### 1. Generate a payload

Use the `rossum_generate_hook_payload` MCP tool. It writes the payload to `/tmp` and returns a file path — do **not** pass the payload JSON inline, it's large.

Example:
```
rossum_generate_hook_payload(hook_id=1500, event="invocation", action="scheduled")
→ { "path": "/tmp/rossum-hook-payload-1500-invocation-scheduled-abc123.json", ... }
```

For `annotation_content` / `annotation_status` events, also pass `annotation_id`, `previous_status`, `status`. For `email` events, `email_id`. For `upload` events, `upload_id`.

### 2. Identify the local hook file

The user typically has many hook `.py` files. Ask (or infer from context) which one maps to the hook that generated the payload. The runner expects a path like `./exporter.py` or `src/hooks/validator.py`.

### 3. Run the runner

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/test-hook-locally/scripts/run_hook.py \
  --module ./exporter.py \
  --payload /tmp/rossum-hook-payload-1500-invocation-scheduled-abc123.json
```

The script prints the hook's return value as JSON to stdout. Debug logs go to stderr.

### 4. If the hook needs real secrets

`generate_payload` returns redacted values for `secrets`. If the hook actually reads them (e.g. `payload["secrets"]["password"]`), the redacted string will break things. Pass a local `.env` file via `--secrets`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/test-hook-locally/scripts/run_hook.py \
  --module ./exporter.py \
  --payload /tmp/rossum-hook-payload-1500-invocation-scheduled-abc123.json \
  --secrets ./.env.hook-secrets
```

The `.env` format is plain `KEY=VALUE` lines (one per line; `#` comments and quoted values supported). Values are merged into `payload["secrets"]`, overriding redacted values.

**Never commit `.env.hook-secrets` — remind the user to add it to `.gitignore`.**

## Safety: non-GET network calls are blocked

The runner enforces a **fail-closed** network policy. There is no opt-out.

**Allowed:** GET / HEAD / OPTIONS through known HTTP libraries.
**Blocked:** POST / PUT / PATCH / DELETE, and *any* non-loopback connection through an unknown library.

### Two layers of defense

1. **Library patchers** (method-level filtering) — applied to:
   - `requests` — covers anything using `requests` directly.
   - `httpx` sync + async — covers `rossum_api` and any direct httpx use.
   - `urllib.request` — covers stdlib urllib.
   - `http.client` — low-level catch for urllib3 / `boto3` / `botocore`.

2. **Socket backstop** (`socket.socket.connect` + `socket.create_connection`) — any connection attempt to a non-loopback address fails with `PermissionError` unless the current call was sanctioned by a library patcher above. This means if a hook uses an HTTP client we haven't patched, the runner fails loudly rather than silently allowing writes through.

When a non-GET call is attempted, the patcher **raises `BlockedByReadOnly`** immediately. The exception propagates up through the hook; the runner catches it at the top level, prints the traceback to stderr, and exits with code 1. No fake responses — the hook is never tricked into thinking its write succeeded.

Example stderr:

```
[run_hook] BLOCKED requests POST https://elis.rossum.ai/api/v1/annotations/42
[run_hook] hook raised an exception:
Traceback (most recent call last):
  ...
BlockedByReadOnly: requests POST https://elis.rossum.ai/api/v1/annotations/42 blocked by run_hook
```

### What's covered vs. not

| Library / tool | HTTP | Non-GET blocked? |
|---|---|---|
| `requests` | ✅ | ✅ |
| `httpx` (sync+async) | ✅ | ✅ |
| `rossum_api` SDK | uses `httpx` | ✅ |
| `boto3` / `botocore` | via `http.client` | ✅ |
| `urllib.request` | ✅ | ✅ |
| `paramiko` (SFTP) | raw sockets | ✅ (socket backstop blocks all) |
| subprocess (`curl`, `gpg`) | out-of-process | ❌ not covered |
| libcurl via `pycurl` | C sockets | ❌ may bypass backstop |

If a hook uses something not covered above, the socket backstop will block it loudly — you'll see `PermissionError` with the destination host, and can decide whether it was a legitimate GET that needs its own patcher, or a write that correctly got blocked.

## Flags reference

| Flag | Required | Default | Purpose |
|------|----------|---------|---------|
| `--module` | yes | — | Path to the hook `.py` file. |
| `--payload` | yes | — | Path to the payload JSON (typically from `rossum_generate_hook_payload`). |
| `--entry` | no | `rossum_hook_request_handler` | Name of the function to call. Override only if the hook uses a non-standard entry. |
| `--secrets` | no | — | Path to a `.env` file whose KEY=VALUE pairs are merged into `payload["secrets"]`. |
| `--log-level` | no | `DEBUG` | Python logging level for the hook. |

## Exit codes

- `0` — hook ran and returned a value (printed as JSON).
- `1` — import error, missing entry function, or hook raised an exception (traceback on stderr).

## Notes

- The runner adds the hook module's directory to `sys.path` so sibling `.py` imports work.
- It does **not** install dependencies. If the hook imports third-party packages, install them in the active Python environment first.
- For `function`-type hooks the entry is always `rossum_hook_request_handler`. For `webhook`-type hooks there's no local function to call — this skill doesn't apply.
