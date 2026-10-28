# Remote device agent

Measure your model on the real device without giving the server any way into it. The agent runs on
the device, polls your Green Computer server over HTTPS, runs only requests signed for that device,
and returns signed timings. Results that pass the quality checks become measurements (labelled
measured, source `remote_agent`). These measurements feed calibration and the "validated" status in
the thermal view.

## What the agent will and will not do

- It only makes outbound HTTPS requests. It opens no port, and the server never connects to the
  device.
- It runs one action, `benchmark_onnx`: ONNX Runtime inference on the approved ONNX file. It never
  runs a shell, a program, or code sent by the server. The agent source imports no `subprocess`.
- Every request must pass these checks, or it is rejected with the reason:
  - the HMAC-SHA256 signature verifies with this device's key;
  - the request is addressed to this device;
  - it has not expired and its nonce has not been seen before;
  - the action is in the local allow-list;
  - runs, time, batch and file size are within the local policy;
  - the downloaded file's SHA-256 matches the approved one.
- Results are signed with the same key. The server checks the signature, the job, the nonce and the
  file hash before it trusts anything.
- `--simulate` produces fake, deterministic timings for testing a set-up. Those results are stored
  and labelled "simulated" and are never imported as measurements.

## Set up

1. In the app, open a project, go to **Verify, history, export → Devices**, and register the device
   with the hardware profile it matches. Copy the pairing code: it is shown once and works for 15
   minutes.
2. On the device (Python 3.10 or newer):

   ```
   python -m pip install onnxruntime numpy onnx       # onnx is optional (older ONNX Runtime builds)
   python gc_agent.py pair --server https://your-server --code ABCD-EFGH-JKLM
   python gc_agent.py run
   ```

   `gc_agent.py` is the single file in `agent/` of this repository. Its config, device token and
   signing key are written to `~/.green-computer-agent/config.json` (owner-only permissions where
   the OS supports it). Set `GC_AGENT_HOME` to keep them elsewhere.
3. Back in the app, choose a validated ONNX file of the project and approve a run on the device.
   Creating the run is the approval. It is signed, bound to the file's SHA-256, and expires after an
   hour if the device does not pick it up.

Run it as a service (systemd example):

```
[Service]
ExecStart=/usr/bin/python3 /opt/gc/gc_agent.py run --interval 10
User=gc-agent
Restart=on-failure
```

## Local policy

`policy` in `config.json` limits what any request may ask for:

| key | default | meaning |
|---|---|---|
| `allowed_actions` | `["benchmark_onnx"]` | the only action that exists today |
| `max_runs` | 2000 | timed runs per request |
| `max_seconds` | 600 | wall time per request |
| `max_batch` | 32 | batch size |
| `max_artifact_mb` | 200 | size of the ONNX file |

Lower them to taste; the agent refuses anything above them even if the server signed it.

## Protocol `green-computer.agent/1`

All agent calls are under `/api/v1/device-agent/` and use `Authorization: Device <token>` (never
cookies).

| call | purpose |
|---|---|
| `POST /pair` `{code, agent}` | one-time code → `{remote_device_id, device_token, signing_key, key_version}` |
| `POST /heartbeat` `{agent}` | marks the device online, updates its description |
| `GET /jobs/next` | `{job, signature}` or 204; claims the job |
| `GET /jobs/{id}/artifact` | the approved file, only for a job this device has claimed |
| `POST /jobs/{id}/result` `{result, signature}` | signed result |

Signatures are HMAC-SHA256 over the canonical JSON (sorted keys, no spaces, UTF-8).

- The device key is `HMAC(server secret, "device:<id>:<key version>")`. The server recomputes it
  and never stores it. Re-pairing increases the key version, so old keys stop working.
- The server secret is `GC_AGENT_SIGNING_SECRET`, or a random key created once in the data
  directory (`agent_signing.key`). Back it up with the data. Changing it means every device must
  pair again.
- Device tokens and pairing codes are stored only as SHA-256 hashes. Pairing attempts are
  rate-limited.

A request (`job`) holds:

- `protocol`, `job_id`, `remote_device_id`, `action`;
- `artifact` (`id`, `sha256`, `size_bytes`, `filename`);
- `params` (`precision`, `batch`, `warmup`, `runs`, `max_seconds`, optional `input_shape`);
- `nonce`, `issued_at`, `expires_at`.

A result holds:

- `protocol`, `job_id`, `nonce` (echoed), `mode` (`measured` or `simulated`), and `status`
  (`succeeded`, `failed` or `rejected`, with an `error`);
- `environment` (host, OS, Python, ONNX Runtime) and `artifact_sha256`;
- `latencies_ms`, `warmup_ms`, `timer_resolution_ns` and `wall_seconds`;
- optional `temperatures_c`, read from Linux thermal zones and never guessed;
- optional `power_w` (null today) and `notes`.

## Measurement quality

Every result is checked before it can become a measurement (`engine/measurement_quality.py`):

- number of runs (≥ 20 good, ≥ 5 usable);
- run-to-run spread (≤ 5% good, ≤ 15% usable, more fails);
- slow outliers;
- drift between the first and last third (heating up, throttling);
- warm-up discarded;
- timer resolution against the run time;
- temperature rise during the runs;
- total duration (short runs miss sustained thermal behaviour).

The verdict is **good** (all checks pass), **usable** (warnings travel with the measurement) or
**poor** (kept for inspection, not imported).

## Revoking

**Revoke** in the Devices list invalidates the device token at once and fails any queued or
running request. To use the device again, register it again or issue a new pairing code.

## Limits

- ONNX Runtime CPU execution provider only. GPU and NPU providers, TensorRT and vendor runtimes are
  not driven by the agent yet.
- Power is not read by the agent. Import a power log (CSV, nvidia-smi, tegrastats) for power and
  energy.
- If an ONNX file was exported with an IR version newer than the installed ONNX Runtime accepts, and
  the `onnx` package is present, the agent lowers the header field only. The graph is unchanged, and
  the result's `notes` say so.
