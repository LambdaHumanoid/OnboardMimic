#!/usr/bin/env bash
# Read-only DDS receivers/relay, manually started with the bridge service.
set -eo pipefail
umask 077
robot="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$(cd "$robot/../../.." && pwd)"
cd "$root"
mkdir -p "$root/var"
python="$root/.venv/bin/python"
cd "$root"
case "${1:-}" in
 relay) exec "$python" -m onboardmimic.robots.unitree_g1.continuity.relay ;;
 primary) port=16665 ;;
 standby) port=16666 ;;
 *) echo 'Expected primary, standby or relay' >&2; exit 64 ;;
esac
source "$robot/bridge/environment.sh"
"$python" -m onboardmimic.robots.unitree_g1.bridge.network --template "$CYCLONEDDS_URI" --output "$root/var/feedback-$1.xml"
export CYCLONEDDS_URI="file://$root/var/feedback-$1.xml"
exec "$robot/native_bridge/build/sonic_state_reader" --source "$1" --port "$port"
