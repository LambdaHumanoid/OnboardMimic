# Build a prebuilt release

English · [简体中文](RELEASE_BUILD_zh.md)

Packaging never executes the ARM64 controller. Use a clean committed source tree,
an ARM64 binary and build report matching its native sources, and the audited
model/engine snapshot. Rebuild the binary when native sources change.

```bash
python3 scripts/build_prebuilt_release.py \
  --snapshot /PATH/TO/AUDITED_MODEL_ENGINE_SNAPSHOT \
  --native-binary /PATH/TO/NEW/g1_deploy_onnx_ref \
  --build-report /PATH/TO/native_build_report.json \
  --bridge-build /PATH/TO/ARM64_BRIDGE_BINARIES \
  --bridge-report /PATH/TO/bridge_build_report.json \
  --patchelf /PATH/TO/patchelf \
  --version v0.1.1 --output /PATH/TO/EMPTY/OUTPUT
```

`native_build_report.json` records `architecture: aarch64`, `binary_sha256`,
`native_source_hashes` (matching `onboardmimic/backends/sonic/native_source_manifest.json`), `method`, and
`built_at_utc`, and `runtime_library_sha256` for the linked Unitree SDK/DDS and ONNX Runtime libraries. Include compiler, sysroot version and relevant validation details.
The packager rejects source/hash mismatches and non-AArch64 binaries. The report describes
how the maintainer built the artifact; it does not substitute for target-device validation.

Both build reports must record `validation.cxx_static_initialization: "passed"`.
Build `tests/native_startup_smoke.cpp` with the same compiler, sysroot and link
flags, and run this pure probe under QEMU with the target sysroot. It has no ROS,
GPU, network or robot dependencies. Record hashes of the probe and target CRT
objects. Ensure `Scrt1.o` comes from the target glibc, as well as `crt1.o`, `crti.o`
and `crtn.o`; a missing file can make GCC silently use its newer host-toolchain
copy. Dynamic loader `--list` checks alone do not detect skipped C++ constructors.

The bridge report separately records `architecture`, `source_hashes` for native bridge
CPP/HPP/CMake/build-script files, and `binary_sha256` for `sonic_state_reader` and
`sonic_command_bridge`. Both executables are required, verified and included. ROS2 Foxy
and the matching target `unitree_hg` runtime are not redistributed. Validate their
dynamic loading separately with the recorded target sysroot.

The snapshot supplies unchanged original models, converted planner, strictly FP32 engines,
the 24-case planner validation report, ONNX Runtime 1.16.3 and the pinned Unitree DDS libraries.
The native controller binary is supplied separately from the build output. Keep the snapshot SDK libraries identical
to those used to link the new controller.

Outputs use the `OnboardMimic-v0.1.1-{runtime,models,engines}.tar.gz` names and extract into
`OnboardMimic/`. They include file hashes, source commit, native build report and distinct
provenance for rebuilt native code versus reused engines. Publish a matching
source tag and validated package set
before adding an OnboardMimic download link.

The source tag, runtime archive and linked guides must describe the same release.
Record the source revision in the manifest; keep its embedded and standalone
copies identical and include all installation asset hashes in SHA256SUMS. Publish the runtime,
manifest, checksums and Release description as a consistent set, then re-download
and verify the uploaded files. Retain native build provenance and model identities.

Before delivery, extract all three archives into a clean directory, run
`python3 scripts/check_prebuilt.py --files-only`, run core and optional PICO offline tests,
and check the packaged ELF architecture, shared-library dependencies and ABI versions.
These steps can run on a workstation. They are not GPU inference, reboot, head-tracking,
or physical-robot validation.

The release cross-build uses an x86-64 Ubuntu 22.04 container with GCC 10.5's AArch64 cross
compiler and a read-only snapshot of the target JetPack 5 / L4T 35.3.1 headers/libraries.
No controller or inference runs on the robot as part of this build. Full source compilation
and linkage occur on the workstation, followed by package checksum verification.
