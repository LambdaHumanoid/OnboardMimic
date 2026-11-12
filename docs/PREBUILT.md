# Orin NX prebuilt packages

English · [简体中文](PREBUILT_zh.md)

The prebuilt package targets **G1 29-DOF body / Jetson Orin NX 16 GB / Ubuntu 20.04 / L4T 35.3.1 / CUDA 11.4 / TensorRT 8.5.2**. Hand actuators are not controlled. Other GPUs, 8 GB Orin NX, JetPack 6 and TensorRT 10 require source adaptation, building and validation on the target device.

## Before installing

- Run as the robot's regular user, with network access and sudo permission. The installer installs `python3-venv` and updates pip inside the venv.
- The wizard discovers ROS2 Foxy and the `unitree_hg` / `unitree_api` workspace. If missing, use the [dependency repair script](#missing-ros2-or-unitree-message-packages).
- Installation runs GPU inference checks. Stop SONIC and other GPU-intensive tasks first.

## Download and extract

Download the complete [OnboardMimic v0.1.0 package set](https://github.com/LambdaHumanoid/OnboardMimic/releases/tag/v0.1.0). The runtime, models and engines form one installation and must be extracted together.

- `OnboardMimic-v0.1.0-runtime.tar.gz`: source, scripts, ARM64 controller, ONNX Runtime / CycloneDDS libraries and licenses.
- `OnboardMimic-v0.1.0-models.tar.gz`: three original ONNX models and the TensorRT 8.5-compatible planner ONNX.
- `OnboardMimic-v0.1.0-engines.tar.gz`: three FP32 TensorRT engines.
- `prebuilt_manifest.json`: platform requirements, provenance and per-file SHA-256 hashes.
- `SHA256SUMS`: checksums for the downloaded files.

The Release description and its source revision define package contents. Changes to the latest source do not automatically update existing downloads.

Private repositories require read access. Authenticated GitHub CLI users can download everything together:

```bash
mkdir onboardmimic-download
cd onboardmimic-download
gh release download v0.1.0 --repo LambdaHumanoid/OnboardMimic --pattern 'OnboardMimic-v0.1.0-*.tar.gz' --pattern prebuilt_manifest.json --pattern SHA256SUMS
sha256sum -c SHA256SUMS
```

Extract all three archives in a new directory. Together they form `OnboardMimic/`. Do not overwrite a running deployment:

```bash
tar -xzf OnboardMimic-v0.1.0-runtime.tar.gz
tar -xzf OnboardMimic-v0.1.0-models.tar.gz
tar -xzf OnboardMimic-v0.1.0-engines.tar.gz
cd OnboardMimic
python3 scripts/setup.py
```

Allow at least **6 GB of free space** for extracted files, dependencies and native caches, plus space for the downloaded archives. Installation does not compile C++ or rebuild TensorRT engines. It installs dependencies, checks files and inference outputs, and creates native cache files.

The wizard asks for access mode (`lan` for direct desktop/phone access, `local` for an SSH tunnel) and whether to enable PICO. It configures communication, enables console/API startup at boot, starts the idle backend and prints the actual URLs. **Connect services, Prepare posture and Start SONIC remain manual operations.**

For an agent or noninteractive installation:

```bash
python3 scripts/setup.py --yes --access lan --pico
# Without PICO, replace --pico with --no-pico.
```

For a nonstandard workspace, add `--unitree-ros-setup /ACTUAL/PATH/install/setup.bash`. Existing tokens and extra bridge arguments are preserved. Installation refuses to proceed while a controller or command bridge is running; it does not stop other programs. For existing services, follow the [upgrade guide](UPGRADE.md).

## What the wizard verifies

1. Install libzmq and Python venv dependencies.
2. Check file hashes, CPU/GPU, memory, L4T/CUDA/TensorRT versions and ELF dependencies of the controller, native command bridge and state receiver. The dynamic loader checks dependencies without executing those programs' entry points.
3. Create an isolated Python venv, install Python 3.8-compatible pip 25.0.1 and dependencies, and use the system TensorRT 8.5.2 Python package.
4. Run basic offline tests (no SciPy required), encoder/decoder numerical checks and 24 planner checks. PICO additionally installs SciPy and runs its tests.
5. Add SONIC cache headers to the three validated engines. This writes files; it does not rebuild engines.
6. Select the robot's directly connected Ethernet interface, generate DDS/ROS configuration and services, and start only the idle API, console and optional receive-only PICO service. Follow the [PICO guide](CONTROL_EXTENSIONS.md#pico-setup) for headset connection and calibration.

**These steps do not start the controller or bridge, release the original control mode, or send motor commands.** Keep other controllers and GPU-intensive tasks stopped during validation. After `Console ready / 控制台就绪`, open the printed URL and follow [manual startup](DEPLOYMENT.md#4-start-from-the-console). No further compilation or service-file editing is needed.

Use `python3 scripts/setup.py --check` for read-only diagnostics. `--install-only` installs and validates without changing services; use `--configure-only` afterward. Advanced users can run `install_scripts/install_prebuilt.sh [--with-pico]` separately after setting `ROS_SETUP` and `UNITREE_ROS_SETUP`; that lower-level command does not configure services.

## Missing ROS2 or Unitree message packages

Run as the robot's regular user:

```bash
bash install_scripts/install_ros_dependencies.sh
python3 scripts/setup.py
```

The script installs apt dependencies using the [official ROS Foxy instructions](https://docs.ros.org/en/foxy/Installation/Ubuntu-Install-Debians.html), then builds pinned CycloneDDS 0.10.2, Foxy RMW, `unitree_hg` and `unitree_api` following [Unitree ROS2](https://github.com/unitreerobotics/unitree_ros2). Source revisions are pinned in the script. The workspace is `~/.local/share/onboardmimic/ros2_ws`; existing workspaces are preserved, and motor-control examples are neither built nor run. Network access, sudo and additional build time are required.

Resolve apt repository errors first; the script does not bypass signature checks. Dependency repair targets the Ubuntu 20.04 ARM64 configuration above, not arbitrary systems.

Do not bypass failed checks and launch the controller. The machine may need a source build. Matching versions does not replace initial physical commissioning.

## Provenance and scope

ARM64 programs are cross-compiled from the current native source using GCC 10.5 and JP5 / L4T 35.3.1 headers and libraries. Packaging adjusts ELF library search paths. Source hashes, build details and original binary hashes are in `native_build_report.json`; packaged hashes are in `prebuilt_manifest.json`.

Model and engine identities are checked against the release manifest. Packaging does not certify target-device inference or physical operation. See the [validation scope](VALIDATION.md); installation repeats inference checks without motor output.

Library, model and source licenses are included. CUDA, TensorRT, ROS2, Unitree message packages and the Python SDK use the target environment; no system image is redistributed.

For an existing deployment, use [upgrade and rollback](UPGRADE.md) rather than overwriting its directory. Packages include the native command bridge, state receiver and `bridge_build_report.json`; ROS2 Foxy and the Unitree message workspace come from the target environment. See [native bridge details](NATIVE_BRIDGE.md).

## Automatic TensorRT installation

The updated installer detects JetPack first and installs missing **TensorRT 8.5.2** libraries and system Python bindings for JP5. Matching installations are preserved; existing packages are not automatically upgraded or downgraded. Network access to the corresponding NVIDIA Jetson apt source is required. Conflicting versions or missing repositories produce an actionable error.

Previously downloaded v0.1.0 archives do not change with the source repository. From the extracted `OnboardMimic/` directory, run the repair once before the existing wizard; no model or engine download is needed:

```bash
curl -fL https://raw.githubusercontent.com/LambdaHumanoid/OnboardMimic/main/scripts/install_tensorrt.py -o scripts/install_tensorrt.py
/usr/bin/python3 scripts/install_tensorrt.py --profile jp5
python3 scripts/setup.py
```

Add `--check` for inspection only, or `--development` to include headers and `trtexec` for source builds. This tool installs TensorRT only: it does not flash the device, switch JetPack or start motor control. If CUDA itself is missing, install the CUDA components matching the existing JetPack first.
