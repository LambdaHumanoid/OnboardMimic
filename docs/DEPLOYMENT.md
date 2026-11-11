# OnboardMimic: first deployment on G1

English · [简体中文](DEPLOYMENT_zh.md)

**Install → Configure communication → Open console → Connect services → Prepare posture → Start SONIC.**

Target: G1 29-DOF body, Orin NX 16 GB, JetPack 5 / L4T 35.3.1, CUDA 11.4 and TensorRT 8.5.2. Unless stated otherwise, run commands as the robot's regular user from the `OnboardMimic` root.

## 1. Install

For the supported G1 / JP5 configuration, use the v0.1.0 prebuilt packages:

- **Prebuilt (recommended):** download the complete [v0.1.0 package set](https://github.com/LambdaHumanoid/OnboardMimic/releases/tag/v0.1.0). Its `python3 scripts/setup.py` wizard completes steps 2 and 3. See the [package guide](PREBUILT.md) for the required file set.
- **Source:** follow the [source build guide](SOURCE_BUILD.md), including dependencies, models, compilation, engines and tests, then continue to step 2. Run native and model builds sequentially.

Installation installs `python3-venv` and updates pip inside the venv; network and sudo access are required. It does not upgrade JetPack or start motion control. Missing ROS2 dependencies are repaired with `bash install_scripts/install_ros_dependencies.sh`. Steps 2 and 3 are for source builds or manual configuration.

## 2. Configure communication

### Locate ROS2 and the Unitree message workspace

ROS2 commonly uses `/opt/ros/foxy/setup.bash`; the Unitree workspace varies. Locate candidates without modifying them:

```bash
find /opt/ros -maxdepth 2 -name setup.bash 2>/dev/null
find "$HOME" -maxdepth 5 -path '*/install/setup.bash' 2>/dev/null
```

Replace `/ABSOLUTE/WORKSPACE` with the actual workspace and verify:

```bash
source /opt/ros/foxy/setup.bash
source /ABSOLUTE/WORKSPACE/install/setup.bash
ros2 pkg prefix unitree_hg
ros2 pkg prefix unitree_api
ros2 pkg prefix rmw_cyclonedds_cpp
/usr/bin/python3 -c 'import rclpy; from unitree_hg.msg import LowState; from unitree_api.msg import Request, Response; print("ROS messages OK")'
```

The first three checks print package directories; the last prints `ROS messages OK`. They do not create ROS nodes or send commands. Use [dependency repair](PREBUILT.md#missing-ros2-or-unitree-message-packages) if needed. The Unitree Python SDK alone does not replace the ROS message workspace.

### Create services and DDS configuration

```bash
.venv/bin/python scripts/install_services.py
cp -n onboardmimic/robots/unitree_g1/systemd/cyclonedds.xml.example "$HOME/.config/onboardmimic/cyclonedds.xml"
chmod 600 "$HOME/.config/onboardmimic/cyclonedds.xml"
nano "$HOME/.config/onboardmimic/bridge.env"
```

The installer generates a private token and enables console/API startup at boot without starting services immediately. Repeated installation preserves the token; existing deployments should use the [upgrade guide](UPGRADE.md). If linger permission is missing, run the suggested `sudo loginctl enable-linger USERNAME` command and retry.

Enter three actual **absolute paths** in `bridge.env`, replacing the example user/workspace. Do not use `$HOME` or `~`:

```ini
ROS_SETUP=/opt/ros/foxy/setup.bash
UNITREE_ROS_SETUP=/ABSOLUTE/WORKSPACE/install/setup.bash
CYCLONEDDS_URI=file:///home/ROBOT_USER/.config/onboardmimic/cyclonedds.xml
```

`cp -n` preserves existing XML. The connection workflow replaces `auto` in the new template with the robot's wired interface; do not select Wi-Fi. Detection requires a direct route to the internal `192.168.123.0/24` network and does not modify routes.

Load configuration and check without sending commands:

```bash
set -a
source "$HOME/.config/onboardmimic/bridge.env"
set +a
source onboardmimic/robots/unitree_g1/bridge/environment.sh
.venv/bin/python -c 'import rclpy; from unitree_hg.msg import LowState; from unitree_api.msg import Request, Response; print("Bridge Python imports OK")'
.venv/bin/python -m onboardmimic.robots.unitree_g1.bridge.network \
  --template "$CYCLONEDDS_URI" --output /tmp/onboardmimic-dds-check.xml
```

Expect `Bridge Python imports OK` and `Robot DDS NIC: <actual-interface>`. The final command only reads network configuration and writes temporary XML. It does not start the bridge or release Unitree mode. If it fails, use section 6.

## 3. Open the console

```bash
systemctl --user start onboardmimic.service onboardmimic-console.service
.venv/bin/python scripts/control.py status
```

Expect `state: stopped`, `pids: []`. Feedback being unavailable before **Connect services** is normal. On a laptop, use the [README SSH tunnel](../README.md#open-the-console--打开控制台). For a phone, configure [LAN access](CONTROL_EXTENSIONS.md#lan-access) and open `http://ROBOT_IP:8769/remote`.

Console and API start at boot and restart three seconds after exit. Recovery stays idle; it does not restore the last policy or motion request. Connect, prepare and start remain manual. Validate Planner first, then configure the optional headset using the [PICO guide](CONTROL_EXTENSIONS.md#pico-setup).

## 4. Start from the console

For initial operation, have someone present, reliably support the robot and have the local emergency stop ready. Stop any additional motion controller using its own procedure first.

| Button | What happens | Success indication |
| --- | --- | --- |
| **Connect services** | Check feedback, request release of Unitree's original motion mode if needed, start this project's bridge; no posture movement | Services connected; Prepare posture enabled |
| **Prepare posture** | Interpolate from current joints to the default posture over about 3 seconds | `prepared`; physical posture correct |
| **Start SONIC** | Policy takes over through about 1 second of interpolation | `active`; Planner controls available |

Connection includes about three seconds of command-source observation. Wait for completion. Another active controller causes rejection; it is not killed or preempted. Opening the page, reconnecting the network or restoring feedback does not press these buttons for you.

First verify preparation, stop and takeover with support, then short ground walking. Planner defaults to 0.5 m/s. WASD / arrows walk, Q/E turn; release to stop. See [controls](CONTROL_EXTENSIONS.md) for phone joysticks and PICO switching.

## 5. Stop

- **Stop motion:** clear walking/turning requests; SONIC remains running.
- **Emergency stop:** terminate the SONIC process owned by this gateway. This is not a power-cut emergency stop and does not guarantee continued standing.

Before shutting down the backend or upgrading, reliably support the robot, emergency-stop and confirm the policy has exited, then stop services using [upgrade and rollback](UPGRADE.md). Stop motion does not mean SONIC has exited.

## 6. Troubleshooting

| Symptom | Check first |
| --- | --- |
| Missing `setup.bash`, `unitree_hg` or `unitree_api` | Step 2 paths, package/import checks, whether the workspace was built and sourced |
| `No direct robot NIC` / ambiguous route | `ip -br address`, `ip route get 192.168.123.161`; require a direct wired route without a gateway |
| `DDS template must select exactly one NIC` | Use the template or select exactly one interface in existing XML |
| Connection fails / bridge unavailable | Read the page error and logs below; do not skip connection |
| Command source or port occupied | Stop the other controller using its own procedure; do not indiscriminately kill processes |
| Page works but feedback unavailable | Normal before connection; otherwise check ROS and wired networking |
| Phone cannot open page | [LAN access](CONTROL_EXTENSIONS.md#lan-access): listen address, actual IP, device isolation and TCP 8769 |
| Missing venv / pip conflicts | Use the current installer; resolve apt/pip errors before continuing |
| API exits / page briefly disconnects | Check `journalctl --user -u onboardmimic.service -n 80` and `var/logs/runtime-events.log` for stop signals, communication failures, heartbeat timeouts or clock changes; do not relax protection thresholds to bypass failures |
| Model hash / TensorRT version mismatch | Check package consistency and supported platform; do not bypass validation |

```bash
journalctl --user -u onboardmimic.service -u onboardmimic-console-connect.service \
  -u onboardmimic-bridge.service -n 80 --no-pager
.venv/bin/python -m onboardmimic.robots.unitree_g1.preflight
```

Preflight is a diagnostic snapshot. `physical_commissioning_pending` is not automatic motion permission. See [native bridge](NATIVE_BRIDGE.md) for feedback/ports and [control ownership](CONTROL_AUTHORITY.md) for source-check boundaries.

## Advanced: CLI and HTTP

After validating the console workflow, the CLI is also available. Execute only the command appropriate to the current stage. Wait for `prepared` after preparation and `active` after startup before proceeding.

| Operation | Command |
| --- | --- |
| Connect services (may release Unitree mode) | `systemctl --user restart onboardmimic-console-connect.service` |
| Status | `.venv/bin/python scripts/control.py status` |
| Prepare with support | `.venv/bin/python scripts/control.py prepare` |
| Start after checking posture | `.venv/bin/python scripts/control.py start` |
| Walk forward for one second when ground conditions allow | `.venv/bin/python scripts/control.py forward --speed 0.5 --duration 1` |
| Stop motion, keep SONIC running | `.venv/bin/python scripts/control.py stop` |
| Terminate SONIC | `.venv/bin/python scripts/control.py estop` |

`left/right` mean lateral walking; `turn-left/turn-right` mean turning. Target speed accepts 0..5 m/s; this is an input range, not high-speed walking qualification.

HTTP supports `GET /api/controller`, `POST /api/controller` and `POST /api/control`. POST requests are not automatically retried. Motion requests acquire an epoch/ticket, use increasing seq values and have an approximately 550 ms lease. The gateway listens only on `127.0.0.1:18921`; remote clients use a tunnel:

```bash
ssh -N -L 127.0.0.1:18922:127.0.0.1:18921 USER@ROBOT_IP
```

Privately configure the same HTTP token on the client (file mode 0600) and send requests to `http://127.0.0.1:18922`. Never commit tokens or SSH passwords. For custom clients and port rules, see [native bridge](NATIVE_BRIDGE.md) and [control ownership](CONTROL_AUTHORITY.md).
