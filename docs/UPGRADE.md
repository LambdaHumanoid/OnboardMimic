# Upgrade and rollback

English · [简体中文](UPGRADE_zh.md)

An **existing deployment** means an OnboardMimic installation directory and service configuration. Keep that directory for rollback; do not run two controllers together. First-time users do not need this guide. Services from other projects are not migrated by `--upgrade`.

**Back up → Stop → Install in a new directory → Update paths → Migrate services → Open console.** Configuration and tokens are retained; posture preparation and SONIC startup remain manual.

## 1. Back up configuration

Record the current installation path. From the new `OnboardMimic` source or package root, replace the old path and run:

```bash
old_root=/ABSOLUTE/OLD/INSTALLATION
new_root="$(pwd)"
backup_dir="$HOME/.config/onboardmimic/manual-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir/user-units" "$backup_dir/config"
chmod 700 "$backup_dir"
cp -a "$HOME/.config/systemd/user/"onboardmimic* "$backup_dir/user-units/"
for name in installed_services.json bridge.env cyclonedds.xml http.token; do
  if [ -f "$HOME/.config/onboardmimic/$name" ]; then
    cp -a "$HOME/.config/onboardmimic/$name" "$backup_dir/config/"
  fi
done
printf 'Backup: %s\n' "$backup_dir"
```

Back up **before editing paths**. These variables are reused below; set them again if you change terminals. Verify that `old_root` matches the active service configuration:

```bash
systemctl --user show onboardmimic.service --property=ExecStart
```

PICO users must also back up its system service, any system drop-ins and device configuration:

```bash
cp -a /etc/systemd/system/onboardmimic-pico-receiver.service "$backup_dir/"
if [ -d /etc/systemd/system/onboardmimic-pico-receiver.service.d ]; then
  cp -a /etc/systemd/system/onboardmimic-pico-receiver.service.d "$backup_dir/"
fi
for relative in onboardmimic/inputs/pico/devices.json; do
  if [ -f "$old_root/$relative" ]; then
    cp -a "$old_root/$relative" "$backup_dir/pico-devices.json"
    break
  fi
done
```

Backups contain private tokens and device serials; do not commit them. Also preserve external DDS XML or device configuration files if used.

## 2. Stop the current deployment

With someone present and reliable robot support, emergency-stop from the current console and confirm the policy process exited. Stop motion does not exit SONIC. Then stop all seven user services:

```bash
systemctl --user stop onboardmimic-console.service onboardmimic-console-connect.service \
  onboardmimic.service onboardmimic-bridge.service onboardmimic-state-primary.service \
  onboardmimic-state-standby.service onboardmimic-state-relay.service
```

A `not loaded` message for a unit absent from an earlier installation can be ignored; all existing services must actually stop. PICO users also run:

```bash
sudo systemctl stop onboardmimic-pico-receiver.service
```

The installer does not stop motion, release Unitree mode or preempt another controller for you.

To remove PICO from this deployment, after backup and stop run `sudo systemctl disable onboardmimic-pico-receiver.service`, move `~/.config/systemd/user/onboardmimic.service.d/60-pico.conf` into the backup directory, and run `systemctl --user daemon-reload`. You can then use `--no-pico`. Keep device configuration and the image for recovery.

## 3. Install in a new directory

For the supported G1 / JP5 configuration, download and extract the complete [v0.1.0 package set](PREBUILT.md) in the new directory, then install without changing services:

```bash
python3 scripts/setup.py --yes --install-only --pico
# Use --no-pico if PICO is not needed.
```

For custom source builds, complete the [source guide](SOURCE_BUILD.md), including engines and tests, then continue to step 4 instead.

This step does not change services. Do not overwrite the previous directory or delete its models, caches or venv.

PICO users copy their device configuration:

```bash
cp -n "$backup_dir/pico-devices.json" "$new_root/onboardmimic/inputs/pico/devices.json"
```

`cp -n` does not overwrite files. If a blank example was already generated, manually merge the real serials. Do not copy old `runs/live/status.json` or historical tracking data.

## 4. Update paths and migrate services

For a source build, migrate the standard services from the new directory:

```bash
.venv/bin/python scripts/install_services.py --upgrade
```

Retain existing communication settings, checking [deployment](DEPLOYMENT.md) if needed. For LAN access, use the new module command in the [console guide](CONTROL_EXTENSIONS.md#lan-access). PICO users continue with the receiver section below, then step 5.

The following wizard command applies only to a matching OnboardMimic prebuilt package:

```bash
python3 scripts/setup.py --yes --configure-only --upgrade --access lan --pico
# Use --access local for SSH-only access, or --no-pico without PICO.
```

The wizard backs up and migrates services, retains tokens/ROS configuration, updates standard console/PICO drop-in paths, configures reception, starts the idle backend and prints URLs. On success, go to step 5; manual editing is unnecessary. API/console processes recover after exit, but the policy never starts automatically.

Custom units or unrecognized paths cause migration to stop while preserving the original files. Use the manual procedure below for those installations.

### Manually migrate custom configuration

Inspect user drop-ins:

```bash
systemctl --user cat onboardmimic.service onboardmimic-console.service
```

Custom overrides need both the new root and the new module layout: use `-m onboardmimic.core.runtime` for the API and `-m onboardmimic.console.server` for the console, with `WorkingDirectory` set to the new project root. PICO data uses `onboardmimic/inputs/pico/runs/live/status.json`. Preserve custom ports, speeds and other arguments; replacing the root alone is insufficient. Edit only overrides that exist in your deployment:

```bash
systemctl --user edit onboardmimic.service
systemctl --user edit onboardmimic-console.service
.venv/bin/python scripts/install_services.py --upgrade
```

The installer checks all related services are stopped and base units have no unrecognized edits, then backs up and migrates base services while retaining tokens, `bridge.env` and user drop-ins. Standard listener/PICO paths migrate automatically; custom arguments need review. If `bridge.env` refers to XML inside the old repository, update that path too. Configuration in `~/.config/onboardmimic/` usually needs no path change.

Resolve custom unit/drop-in conflicts by reviewing differences, not removing checks. If you edited a base `.service`, move custom arguments into a drop-in and restore the base unit to the generated content recorded by the installer. Migration itself does not start services.

### PICO: migrate the receiver

`install_services.py --upgrade` **does not migrate the system-level PICO service**. Keep all services stopped and run from the new directory:

```bash
.venv/bin/python scripts/install_pico_service.py --preview
.venv/bin/python scripts/install_pico_service.py
```

This points container code, device configuration, output directories and API `--pose-status` at the new installation. For custom API arguments or system PICO units/drop-ins, merge against the preview and backup rather than replacing custom settings with defaults. Confirm that `60-pico.conf` and the system service use consistent paths before starting.

## 5. Open the new console

PICO users start reception first; Planner-only users skip the first command:

```bash
sudo systemctl enable --now onboardmimic-pico-receiver.service
systemctl --user start onboardmimic.service onboardmimic-console.service
.venv/bin/python scripts/control.py status
```

Expect `state: stopped`, `pids: []`. Check desktop, mobile and any PICO reception status, then manually connect, prepare and start using [first deployment](DEPLOYMENT.md). Inspect system/user paths with `systemctl cat` / `systemctl --user cat` respectively.

The backend and console are ready at boot. An enabled receiver only receives data; it does not enter Pose or start SONIC.

## Rollback

Stop the current deployment as in step 2, then restore the step 1 backup:

1. Restore backed-up user `.service` files and their drop-in directories to `~/.config/systemd/user/`. First save and remove the corresponding new drop-in directories so no new paths remain.
2. Restore `installed_services.json` and, as needed, `bridge.env`, XML and token files, preserving permissions. If the previous version had no installation record, move the new record aside.
3. PICO users restore `/etc/systemd/system/onboardmimic-pico-receiver.service` and its original system drop-ins. Code/device paths must point to the retained directory. Verify the referenced Docker image still exists.
4. Reload as below, then manually start the idle backend, console and optional receiver according to the restored configuration.

```bash
systemctl --user daemon-reload
# If you restored the PICO system service:
sudo systemctl daemon-reload
```

Do not copy the whole backup directory over systemd directories or run two controllers. Console/API boot enablement and user linger are not automatically undone. For fully manual startup, run `systemctl --user disable onboardmimic.service onboardmimic-console.service`. Do not disable linger if other services use it.
