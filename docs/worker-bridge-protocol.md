# Odoo TUI worker bridge protocol

The plugin telemetry process cannot import `odoo_tui` because it runs in the Mission Control telemetry virtualenv. `worker_bridge.OdooTuiWorkerBridge` starts the explicitly configured odoo-tui virtualenv interpreter and sends one JSON request on stdin. The worker returns exactly one JSON response on stdout and never opens a port. The bridge implements the adapter transport seam for the seven read operations: `clients.list`, `releases.list`, `instance.identity`, `runtime.status`, `runtime.control`, `modules.list`, and `databases.list`. The strict worker envelope is internal; public plugin HTTP handlers return the typed `data` object, while host dispatch serializes failures as `{\"error\": code, \"detail\": message}`.

## Deployment configuration

`WorkerConfig.interpreter` and `WorkerConfig.worker` are deployment configuration. They must be absolute paths and are never accepted from `/api/local` input. `WorkerConfig.config_path` remains a validated legacy compatibility field but is not forwarded by the current worker bridge; `odoo_tui_worker.py` uses its fixed, deployment-owned configuration path. The snapshot contract does not claim support for overriding that path. The default interpreter is the existing odoo-tui virtualenv:

`/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/.venv/bin/python3`

The bridge uses `shell=False`, a fixed minimal environment, and a bounded timeout (five seconds by default).

## Request

```json
{"protocol_version":1,"operation":"runtime.status","client":"demo","environment":"local"}
```

Only the seven read operations are allowlisted. Client and environment identifiers are strict slugs. There is deliberately no `start`, `stop`, `restart`, `cycle`, `update`, or onboarding operation.

## Responses

Successful status responses contain a read-only data model:

```json
{"protocol_version":1,"operation":"runtime.status","ok":true,"data":{"status":{"state":"online","pid":1234,"process_name":"odoo-18-demo-local","pm2_id":null,"startup_mode":null}}}
```

The stopped state includes every declared field, with `pid: null` when no process exists. Worker-side validation or probing failures use:

```json
{"protocol_version":1,"operation":"runtime.status","ok":false,"error":{"code":"STATUS_PROBE_FAILED","message":"..."}}
```

The bridge maps transport failures to stable uppercase `BridgeError.code` values: `OPERATION_NOT_ALLOWED`, `INVALID_REQUEST`, `DEPENDENCY_UNAVAILABLE`, `TIMEOUT`, `PROCESS_CRASH`, `MALFORMED_RESPONSE`, and `OUTPUT_TOO_LARGE`. `OUTPUT_TOO_LARGE` is emitted only when the existing 64 KiB stdout/stderr transport bound is exceeded. `OdooTuiAdapter` preserves `MALFORMED_RESPONSE` for legacy public routes and carries the transport code privately for the snapshot composer. Worker errors use stable uppercase codes and their messages are replaced with fixed safe text rather than forwarding worker exception text. Error messages never include request secrets or captured stderr.

This is a fixture-friendly request/response boundary. Tests can point `WorkerConfig` at an executable fixture interpreter and worker script; production configuration remains fixed by deployment and the worker remains read-only.
