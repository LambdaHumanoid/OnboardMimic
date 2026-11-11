# OnboardMimic architecture

English · [简体中文](ARCHITECTURE_zh.md)

OnboardMimic integrates existing S0 motion controllers. The implemented hardware
pair is **Unitree G1 + SONIC**. The extension mechanism is tested with an in-memory
two-joint robot and an unrelated controller protocol; that fixture is not a
supported physical robot or another deployable controller.

## Boundaries

| Module | Owns | Does not own |
| --- | --- | --- |
| `core/` | Lifecycle, expiring commands, session/ticket validation, mode handover, process supervision, adapter contracts | DDS messages, joint ordering, model loading, retargeting |
| `console/` | Desktop/mobile UI, authenticated local API proxy, capability presentation | Controller-specific wire messages or direct motor output |
| `inputs/pico/` | PICO SDK container, tracking reception, device configuration, read-only skeleton preview | Robot admission, SONIC pose encoding, control ownership |
| `robots/unitree_g1/` | 29-joint feedback and thresholds, ROS/DDS, vendor-mode connection, native command bridge, single-writer admission | SONIC model conversion and Planner message format |
| `backends/sonic/` | Controller launch/configuration, SONIC protocol, progress decoding, PICO-to-SONIC mapping, models, build/install/release profile | Console layout or another robot's hardware assumptions |

The native controller still contains its upstream G1 policy-specific code. It is
not a generic controller for arbitrary bodies. Supporting a new body with SONIC
requires a compatible model and native implementation, in addition to a hardware
adapter.

## Composition

`core/registry.py` contains lazy factories for robots, backends and tracking inputs.
`select(robot, backend)` validates the pair, reference joint count, finite target
positions and backend capabilities **before starting services or transport**.
Constructors must not connect to hardware or send commands.

```python
from onboardmimic.core.registry import select
robot, backend = select('unitree_g1', 'sonic')
```

Installed API and console units include the same explicit `--robot` / `--backend`
selection. Installations use `onboardmimic*` services. The console rejects a runtime
with a different pair; emergency stop remains available.

The setup entrypoint dispatches to `backend.installer(robot)`. Today, the install
recipe supports G1/SONIC on the specified Orin NX/JP5 platform only. Installing an
adapter is not permission to start it. Boot runs the idle API/UI; connection,
posture preparation and policy start remain separate manual actions.

## Console robot / WBC selection

Desktop and mobile share compact, registry-driven robot/WBC selectors in the header. `GET /api/platform`
returns `schema_version: 1`, `robots`, `backends`, validated compatible `pairs`,
the console's `selected: {robot_id, backend_id}`, and `switching`. Registered
adapters describe capabilities, not model availability or hardware readiness.
`GET /api/controller` remains the authority for the actual runtime pair and state.

The current server reports `switching.supported: false` with
`reason: service_configuration_required`. The dropdowns remain openable and show the deployed pair plus a disabled “Coming soon” option;
change both service configurations while stopped to use another supported pair.
There is no HTTP endpoint for changing the runtime pair and no automatic restart.

`console/static/platform.js` reserves `setSelectionHandler(async pair => …)` on
`window.onboardMimicPlatform` for a future lifecycle-managed switch implementation.
The frontend requires **both** that handler and advertised server support, a
matching runtime, and a stopped controller with no policy process before enabling
a different selection. Browsing the dropdowns does not send a request. It filters incompatible pairs and reloads server configuration after
application. A handler must validate ownership and stopped state again on the
server, apply the pair atomically, and leave preparation/start to the operator.
Binding a handler alone does not enable switching on the shipped server.

## Robot contract

Implement `RobotAdapter` in `robots/<id>/adapter.py`:

- `id`, `label`, `joint_count`, `state_topics`: stable identity and accepted feedback topics.
- `telemetry(latest, target, now)`: decode measured state to `q`, `dq`, `fresh`,
  `valid`, `faults`, `upright`, `max_speed_rad_s` and any diagnostics. Use a
  monotonic clock. Joint ordering must match the backend reference.
- `safe(telemetry)`: robot-specific admission and runtime limits. Missing, stale or
  malformed measurements must not pass.
- `guard(owned_pid)`: live owner/readiness checks; never stop another owner to gain control.
- `ingest`, `feedback_ready`, `feedback_status`: optional health information;
  G1 uses these for redundant feedback and handover admission.
- `connection()`: explicit connection/status/cancel workflow for the console.
- `service_units(root, config)` and `environment_template(root)`: hardware services
  and configuration. Hardware services must not gain implicit boot motor output.

Register the factory with `register_robot`. Add a matching backend profile and
hardware-free protocol tests before target-device commissioning. Hardware ownership
must span installations: G1 retains `/tmp/sonic-motor-control.lock` unchanged. Do not rename or delete it to take control.

Startup admission is strict: a conflicting controller or incomplete inspection
blocks preparation. Once a controller is running, `core/admission.py` treats
inventory failures as diagnostics, preserving the current owner. It records
`running_admission_warning` at most every five seconds during a continuous failure
and `running_admission_recovered` on recovery in `var/logs/runtime-events.log`.
Process diagnostics include bounded PID/program/error details, never full command
arguments. The shared bridge lock still refuses later owners; transport watchdogs,
measured-feedback limits and emergency stop remain active. This inventory check is
not a guarantee of isolation from external DDS writers.

## S0 backend contract

Implement `S0Backend` in `backends/<id>/adapter.py`:

- `compatible_robots`, `capabilities`, `supported_inputs`: explicit support matrix.
- `baseline(robot)`: finite reference positions matching that robot's joint count and hardware ordering.
- `launch_spec(robot)`: `LaunchSpec(argv, cwd, env)`. Called only after an explicit
  prepare request passes admission. Use argument lists, not a shell command.
- `progress(log)`: translate native progress to `BackendProgress`; elapsed time
  alone must not imply preparation or takeover succeeded.
- `decode_status(topic, value)`: normalized `{mode, playing}` or `None`.
- `transport(gateway, robot)`: backend wire encoding/decoding; expose `thread`,
  `stop`, `ready`, `last_tick`, `error`, `stage`, `shutdown_reason` for supervision.
  Forward measured data through `gateway.ingest`, tick the lifecycle, then publish
  its permitted command. Never infer motion permission from model readiness.
- `prepare_seconds`, `handoff_seconds`, preparation/handoff timeouts and `max_speed`:
  backend limits and display metadata.
- `pose_reader(input_id, path)`: optional backend mapping worker with `thread`,
  `stop`, and `snapshot()` returning `ready`, `reason` and a backend packet.
- `installer(robot)`: optional pair-specific installation recipe. Unsupported
  combinations fail before installation changes anything.

The shared lifecycle currently requires a `planner` idle/velocity mode; `pose` is
optional. Pose loss returns to Planner and waits for acknowledgement. A controller
without that fallback needs a deliberate lifecycle extension, not a fabricated
capability declaration. SONIC's encoder indices and ZMQ messages stay inside its
backend.

Planner commands are semantic directions, target speed and heading. The core owns
request sequence validation, the 550 ms lease and heading integration; the backend
owns how those values become controller messages. The existing SONIC numerical
conventions are preserved by its transport adapter.

## Tracking input contract

Implement `TrackingInput` and register it with `register_input`. Its `status()` and
`skeleton()` are read-only diagnostics. A new input also needs an explicit mapping
in each compatible backend; receiving a human skeleton does not authorize motion.
PICO's SDK coordinates remain in `inputs/pico/`; SMPL/wrist conversion and SONIC pose
wire encoding live in `backends/sonic/pico/`.

## Files and compatibility

- `backends/sonic/resources.json`: pinned model/SDK identities and model checksums.
- `backends/sonic/native_source_manifest.json`: current native-source checksums.
- `backends/sonic/deploy/`: native CMake/source/policy/reference layout.
- `backends/sonic/deploy/onboard/`: conversion, TensorRT validation and cache tools.
- `var/`: runtime logs, private DDS configuration and connection status; ignored by Git.
- `inputs/pico/runs/`, `devices.json`: generated captures/device configuration; ignored by Git.
- `scripts/` and `install_scripts/`: stable entrypoints; backend/hardware-specific
  implementation lives in its adapter module.

Use `python -m onboardmimic.core.runtime --help` and
`python -m onboardmimic.console.server --help` from the repository root. Do not run
package files by path: relative imports intentionally follow package boundaries.
The HTTP routes are `/api/controller` and `/api/control`. Configuration uses
`~/.config/onboardmimic`. Admission locks also coordinate with existing bridges;
protocol bytes and third-party notices retain their original identities.

The upgrade installer recognizes generated baseline units, rebuilds standard
LAN/PICO overrides for the new layout, preserves the token and copies an existing
PICO device configuration. Custom overrides are preserved and require explicit
review. It never stops live services automatically. Keep the old installation
until target validation completes; see [upgrade and recovery](UPGRADE.md).

## Verification

Run `PYTHON=python3 bash scripts/test.sh --all` with the documented dependencies.
`tests/test_adapters.py` exercises a two-joint robot, an unrelated S0 status
protocol, mismatched pairs, capability rejection and per-backend speed limits.
Regression tests cover G1 lifecycle/feedback, PICO mapping, HTTP permissions,
console requests, service migration and packaging guards.

Native checks without ROS or motor output:

```bash
cmake -S onboardmimic/robots/unitree_g1/native_bridge -B /tmp/onboardmimic-bridge-test -DBUILD_ROS_BRIDGE=OFF
cmake --build /tmp/onboardmimic-bridge-test
ctest --test-dir /tmp/onboardmimic-bridge-test --output-on-failure
python3 onboardmimic/robots/unitree_g1/native_bridge/differential_test.py \
  --reference onboardmimic/robots/unitree_g1/bridge/lowcmd_transport.py \
  --binary /tmp/onboardmimic-bridge-test/core_test
```

These checks do not replace installation, inference, boot and physical acceptance
on the target robot. See [validation scope](VALIDATION.md) for the released G1 profile.
