# Software WBC contract / 软件 WBC 契约

This page specifies `capture_adapter=reference-v1`, the software reference backend.
It is not the native SONIC recording or physical replay contract. SONIC uses
`sonic-native-v2`, an exported spec with actual per-field history timestamps,
[mode-independent readiness](../../docs/SONIC_RECORD_READY.md),
[LeRobot v2.1 storage](lerobot-storage.md) and
[reference-plan v2 execution](../../docs/SONIC_REFERENCE_REPLAY.md).

本文只描述软件参考后端。其等间隔历史和软件计划限制不套用到 SONIC 原生真机链路。
旧 SONIC 数据格式、旧配置探测命令和旧真机回放客户端已移除。

OM owns execution, control ownership and protected exits. Lab observes references,
stores datasets and validates plans. A new physical WBC must explicitly integrate
OM's sole protected writer and lifecycle; declaring a contract alone does not grant
motor authority. See [protection](../../docs/WBC_PROTECTION.rst).

## Configuration

Use an explicit `reference-v1` collection JSON and validate it with
`onboardmimic-lab validate-collection --config FILE`. No OM checkout discovery or
Python package import is required. The fixture in
[reference-collection.fixture.json](../examples/reference-collection.fixture.json)
illustrates the software schema; it is not a physical controller identity.

## Controller spec v1

The executable example config is the canonical complete example. Validation lives in
`contracts.py`. Required fields:

| Field | Meaning |
|---|---|
| `schema`, `schema_version` | `onboardmimic.lab.controller-spec`, integer `1` |
| `robot_id`, `backend_id`, `execution_id` | Robot, WBC implementation, versioned execution semantics |
| `reference_space` | Versioned semantic identity; token and joint targets are different spaces |
| `artifacts` | Nonempty map of names to lowercase SHA256; actual native controller/model/normalization identity |
| `clock` | `same_host_monotonic_ns`; cross-host recording is unsupported in this version |
| `period_ns`, `max_gap_ns` | Integer nominal reference period and maximum continuous gap |
| `state`, `reference`, `control_output` | Named tensor field specs; output may be null/omitted |
| `history` | `length`, `period_ns`, `order: oldest_first`, `reset: required/stateless`, explanatory `layout` |
| `record_modes` | Explicit OM input modes allowed to produce demonstrations |

Each tensor declares `shape`, `unit`, `frame`, `semantic`; joint fields additionally
require `joint_names` matching the last dimension, with no duplicates. State tensors
have a leading history axis equal to `history.length`, including length 1. Values are
nested finite numeric arrays, not a flattened guessed vector. Token fields may use
`unit: latent`, `frame: encoder_space` and a declared latent semantic.
History is the **actual sampled input history** with explicit timestamps, uniformly
sampled at the declared period. This version rejects irregular/padded histories;
publish only after warm-up. Do not fabricate histories from receiver snapshots.

`contract_sha256` is SHA256 of UTF-8
`json.dumps(spec, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)`.
Hash the exact supplied spec; extra fields also bind compatibility. Contract validation
must precede hashing. Semantics, ordering and artifacts bind compatibility even if
shapes match. Hashes are identity assertions, not cryptographic proof of running code;
OM must bind them to the actually launched native process and loaded artifacts.

## Readiness

The authenticated `GET /api/controller` provides `lab.record_ready`,
`contract_sha256` and `session_id`. Lab requires actual fresh consumed data,
a matching contract, a valid allowed software mode and a stable session.
Source loss, unauthorized mode or session change fails collection.
These software mode checks do not override SONIC's mode-independent policy.
RuntimeStatus exposes safe readiness/reason fields, never tickets or credentials.

## Standard read-only telemetry

Local ZMQ PUB endpoint in `telemetry_endpoint`, SUB topic `oml/controller/v1`.
Exactly **two multipart frames**: topic bytes, UTF-8 JSON sample. No MessagePack prefix
and no reuse of legacy `g1_debug` on this path. Samples are size-bounded to 1 MiB.

```json
{
  "contract_sha256": "<spec SHA256>",
  "session_id": "<same session as readiness>",
  "index": 123,
  "timestamp_monotonic_ns": 5000000000,
  "timestamp_realtime_ns": 1800000000000000000,
  "state_history_ns": [4990000000, 5000000000],
  "state": {"q": [[0, 0, 0], [0.01, 0.01, 0.01]]},
  "reference_id": "ref-123",
  "reference_evidence": "consumed",
  "reference": {"q_target": [0.02, 0.02, 0.02]},
  "control_output": {"q_target": [0.02, 0.02, 0.02]}
}
```

`reference` at `consumed` means the target **actually used on that execution tick**,
not the last input sent over a socket. Use `submitted` if consumption is unverified;
such rows are retained raw but excluded from aligned labels and executable plans.
`submitted_reference` may additionally preserve pre-transform targets with the same
field spec. If its representation differs, use a separately designed raw stream;
do not label an incompatible representation with the consumed reference spec.
`control_output` is optional; if present, it must match its declared spec.

Indices and both source clocks must advance. Missing indices or excessive time gaps
split segments. Session/clock resets end the episode. Source-to-recorder age must be
0..250 ms, consumed telemetry and GoPro ingress must each remain fresh within 500 ms.
The source timestamp anchors the consumed tick; history cannot be future/stale.

## Software datasets and plans

The software backend uses raw controller/camera/runtime streams and a compact
video index for contract checks. Physical SONIC export instead produces LeRobot
v2.1; software storage is not a compatibility path for old robot episodes.

Software `onboardmimic.lab.reference-plan` schema 1 contains original samples and
source-relative `offset_ns`, confined to one continuous consumed segment.
Its validator accepts 2..100000 frames and at most 120 seconds. These are software
reference limits; native SONIC replay has no configured total-duration limit.
State is provenance or simulator initialization, never a replacement for live
robot feedback.

`ReferencePolicy` checks model/execution identity and reference chunks.
Registered software simulation adapters own their physics and simulated history.
Use fixtures for contract checks and onsite observations for physical motion checks.
Current SONIC LeRobot recordings store motion tokens, state and video;
[the replay page](replay-console.md) provides video preview and physical replay.
Diagnostic simulation requires full inference histories from an appropriate data source.
