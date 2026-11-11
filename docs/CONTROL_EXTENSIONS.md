# G1 console: Planner and PICO Pose

English · [简体中文](CONTROL_EXTENSIONS_zh.md)

**Installation checks → Connect services → Prepare posture → Start SONIC → Planner or PICO Pose.** PICO is optional; Planner works without a headset.

This guide covers the OnboardMimic layout on the `main` branch. Do not copy source over a running installation; follow the [upgrade guide](UPGRADE.md).

## Open the console

**Prebuilt wizard users:** after `python3 scripts/setup.py` reports `Console ready`, the console is running and enabled at boot. For `lan`, open the printed URL; for `local`, run the printed SSH tunnel command on your laptop. Continue with the buttons below; do not reinstall or restart services unnecessarily.

<details>
<summary>Source builds or manual configuration: install and start services</summary>

Complete the environment configuration in [first deployment](DEPLOYMENT.md), then run from the project root:

```bash
.venv/bin/python scripts/install_services.py
systemctl --user start onboardmimic.service onboardmimic-console.service
```

`install_services.py` creates service definitions and a token, enables user linger and console/API boot startup without requiring an SSH login. It installs/enables services; the start command starts them now. The unified `setup.py` wizard also starts the idle backend. The bridge starts through **Connect services**; posture preparation and SONIC startup remain manual. If linger permission fails, run the suggested sudo command and retry. If existing units differ, stop the previous deployment and use `--upgrade` as described in [upgrading](UPGRADE.md); custom drop-ins are retained. The default console address is `127.0.0.1:8769`.

```bash
ssh -N -L 8769:127.0.0.1:8769 USER@ROBOT_HOST
# Browser: http://127.0.0.1:8769/
```

</details>

### Follow the console sequence

Desktop routes are `/` or `/real`; mobile routes are `/remote` or `/real/remote`. Use landscape on phones: left joystick walks, right joystick turns; releasing clears that input. Select `lan` in the wizard for direct phone access, or use [manual LAN setup](#lan-access). Desktop SSH access remains available.

1. **Connect services:** reliably support the robot first. The workflow checks feedback, requests release of Unitree's original motion mode if necessary, then starts the bridge. Allow about three seconds for admission checks. Wait for services connected and Prepare posture enabled. This does not prepare posture or start SONIC.
2. **Prepare posture:** explicitly request it with reliable support. Wait for physical completion of the three-second interpolation.
3. **Start SONIC:** separately request takeover and wait for confirmation, with a one-second handover.
4. The **Planner / PICO Pose** tabs appear after startup. Selecting a tab only changes the page; it does not change control mode.

Emergency stop is always visible and terminates the session's controller. It does not cut power or guarantee continued standing. Opening the page or restoring the network does not prepare or start automatically.

Failed connection shows its reason. Check for other running controllers. If Unitree mode release fails or the bridge is unavailable, preparation stays disabled. Reconnecting does not replace another controller. See [control ownership](CONTROL_AUTHORITY.md).

<a id="lan-access"></a>

## Phone and LAN access

Select `lan` in the wizard; no service editing is needed. It prints the actual desktop URL `http://ROBOT_IP:8769/` and phone URL `http://ROBOT_IP:8769/remote`. `local` uses an SSH tunnel. If you already selected `lan`, skip manual configuration below. Use the robot's actual Wi-Fi/hotspot IP, not `127.0.0.1` or `0.0.0.0`.

<details>
<summary>Manual configuration: change local-only access to LAN access</summary>

If the wizard already generated `90-setup-access.conf`, edit that file instead of creating another listen configuration.

Services created only by `install_services.py` default to `127.0.0.1:8769`; sharing Wi-Fi alone does not make them accessible. With SONIC stopped, run as the robot's regular user:

```bash
# Get the actual project path to replace /ABSOLUTE/OnboardMimic below.
pwd
systemctl --user edit onboardmimic-console.service
```

Enter the following, replacing **both** occurrences of `/ABSOLUTE/OnboardMimic`. Keep the quotes; systemd expands `%h` to the user's home directory. Edit an existing listen drop-in rather than adding conflicting `ExecStart` definitions.

```ini
[Service]
ExecStart=
WorkingDirectory=/ABSOLUTE/OnboardMimic
ExecStart="/ABSOLUTE/OnboardMimic/.venv/bin/python" -m onboardmimic.console.server --host 0.0.0.0 --port 8769 --token-file "%h/.config/onboardmimic/http.token" --robot "unitree_g1" --backend "sonic"
```

Save, then run:

```bash
systemctl --user daemon-reload
systemctl --user restart onboardmimic-console.service
hostname -I
```

Choose the robot's address on the phone's Wi-Fi or robot hotspot. Open `http://ROBOT_IP:8769/remote` on the phone or `http://ROBOT_IP:8769/` on a computer. Do not assume a universal hotspot IP. If unavailable, check device isolation and TCP `8769` access.

The setting persists across boots and changes only the web listen address; it does not start the policy. Expose this robot-control page only on a trusted LAN, not the public internet. The page's session token is an origin safeguard, not a user login system. To restore local-only access, change `--host` back to `127.0.0.1` and restart the console service.

</details>

## Planner walking

Hold to walk forward/backward/left/right or turn; release to stop. WASD / arrows walk, Q/E turn, Space/Esc stop motion. Desktop buttons support holding the left mouse button. Mobile joysticks send discrete commands along the dominant direction; joystick displacement does not change speed.

One **target speed** applies to every walking direction. Default: 0.5 m/s. The page and HTTP `speed` accept 0..5 m/s; this is input validation, not a guarantee of stable walking. Change the default with `--default-speed` on the `onboardmimic.core.runtime` gateway, or explicitly supply `speed` per request.

```bash
.venv/bin/python scripts/control.py forward --speed 0.4 --duration 1
.venv/bin/python scripts/control.py left --speed 0.4 --duration 1
.venv/bin/python scripts/control.py turn-left --duration 1
.venv/bin/python scripts/control.py stop
```

`stop` clears motion requests while SONIC keeps running. Focus loss, touch cancellation or a lease not renewed within 550 ms expires the motion request. HTTP control accepts only `client/seq/epoch/ticket/move/turn/speed/stop`; other parameters are rejected. Session, feedback and control-conflict checks still apply.

<a id="pico-setup"></a>

## PICO full-body Pose (optional, disabled by default)

Select PICO in the wizard or pass `--pico` to install mapping dependencies, download/build the image, configure devices and the receiver service, and start reception only. Docker operations use sudo; no Docker group membership or new login is required. If Docker is missing, follow the prompt to run `sudo apt-get install -y docker.io`, then retry. Continue to [headset setup](#pico-headset); do not repeat image/service installation.

<a id="pico-headset"></a>

### Connect and use the headset

Complete device pairing and full-body tracking calibration in the headset app. Check the console body preview and body-data readiness. For separate tracker-role assignments, put your serial numbers in `onboardmimic/inputs/pico/devices.json`. Personal serial numbers are not distributed with the repository.

The headset and robot must be mutually reachable. Enter the **robot's actual IP** in XRoboToolkit. Reception uses TCP `63901`, not web port `8769`. Hotspot addresses depend on local configuration; do not assume `10.42.0.1`. Receiver status is in `onboardmimic/inputs/pico/runs/live/status.json`; inspect connections with `sudo journalctl -u onboardmimic-pico-receiver.service -n 50`. The receiver only receives tracking; boot does not enter Pose or start SONIC.

After preparation and SONIC startup, stop walking, establish a stable stance and align the operator's posture with the robot. Open the PICO Pose tab and click **Enter PICO Pose**. Selecting the tab alone does not take control. `scripts/control.py pose` / `planner` also switch modes. Confirmation comes from `g1_debug` encoder_mode/play; Planner commands are rejected during transitions. Stale tracking requests zero-speed Planner. Reconnection does not automatically re-enter Pose. A mode-confirmation timeout falls back; if Planner fallback also times out, the session controller terminates. Software fallback does not guarantee protection against falls.

The body preview only reads receiver files; it does not mean the policy has taken control. For a custom receiver directory, pass `--pico-runs /ABSOLUTE/RUNS_PARENT` to the console.

<details>
<summary>Source or manual installation: build and configure the PICO receiver</summary>

Skip this section if the wizard configured PICO. Manual setup has three steps: **check Docker → build the receiver image → configure devices and services**. The prebuilt archive includes scripts, not the image. `--with-pico` installs mapping dependencies only.

### 1. Check Docker

Run as the robot's regular user:

```bash
command -v docker
docker info --format '{{.Architecture}}'
```

Expect Docker and an ARM64 architecture such as `aarch64`. Install Docker if missing; start `docker.service` if the daemon is stopped. For socket permission errors, first check `sudo docker info`, then configure user access according to the machine's policy. Where Docker group access is allowed:

```bash
sudo usermod -aG docker "$USER"
# Log out of SSH, log back in, then run docker info again.
```

Docker group access is effectively root-level access. Do not run all of `install_pico_service.py` under sudo: it must write user services to the robot user's directory.

### 2. Build the image and configure devices

From the repository root:

```bash
.venv/bin/python -m pip install -r onboardmimic/backends/sonic/pico/requirements.txt
cp -n onboardmimic/inputs/pico/devices.example.json onboardmimic/inputs/pico/devices.json
nano onboardmimic/inputs/pico/devices.json
# Download and verify pinned dependencies, then build the image:
bash onboardmimic/inputs/pico/prepare_container.sh
docker image inspect onboardmimic-pico-receiver:20261020 --format '{{.Architecture}}'
```

Expect `arm64`. Build on the ARM64 robot with network access; on x86, the script only downloads dependencies. Enter your own tracker serials; `cp -n` preserves existing configuration. Tracker count alone does not establish Body-data readiness.

### 3. Configure and start reception only

Create the services described in [first deployment](DEPLOYMENT.md). If SONIC is running, reliably support the robot, emergency-stop and confirm policy exit before stopping services. Stop motion alone is insufficient. The installer checks that all seven user services are stopped:

```bash
systemctl --user stop onboardmimic-console.service onboardmimic-console-connect.service \
  onboardmimic.service onboardmimic-bridge.service onboardmimic-state-primary.service \
  onboardmimic-state-standby.service onboardmimic-state-relay.service
```

If a PICO system service exists, also run `sudo systemctl stop onboardmimic-pico-receiver.service`. For a directory migration, first back up and migrate configuration using [upgrading](UPGRADE.md), then continue:

```bash
.venv/bin/python scripts/install_pico_service.py --preview
.venv/bin/python scripts/install_pico_service.py
sudo systemctl enable --now onboardmimic-pico-receiver.service
systemctl --user start onboardmimic.service onboardmimic-console.service
systemctl is-active onboardmimic-pico-receiver.service
```

Expect `active`. The configuration script installs the receiver service, sets container read/write permissions and adds API `--pose-status`; the script itself does not start or stop services. The API starts in idle Planner mode. The page should show reception status; waiting for data is normal without a connected headset.

A custom API unit or conflicting `60-pico.conf` is not overwritten; merge manually using the preview. The container uses CPU and host networking, mounts no device or motion-control credentials, and does not record by default. A running receiver does not mean SONIC has taken control.

</details>
