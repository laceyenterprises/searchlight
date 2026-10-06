#!/usr/bin/env bash
# Run after checkout on a disposable hosted runner; no host configuration needed.
set -euo pipefail
cd "$(dirname "$0")/.."
proof_root="$(mktemp -d)"
# Keep the proof's own exit status: an EXIT trap's last command would otherwise replace it.
# The offline suite runs as root, so fall back to sudo for anything root-owned.
cleanup() {
  local status=$?
  rm -rf "$proof_root" 2>/dev/null || sudo -n rm -rf "$proof_root" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
# Clone committed HEAD without hooks, persisted checkout credentials or local objects.
git -c core.hooksPath=/dev/null clone --no-local --no-hardlinks . "$proof_root/source"
cd "$proof_root/source"
git checkout --detach "$(git -C "$OLDPWD" rev-parse HEAD)"
python3 -m venv "$proof_root/venv"
# Isolated HOME/state and a minimal environment make host services unavailable.
mkdir "$proof_root/home"
env -i PATH="$proof_root/venv/bin:/usr/bin:/bin" HOME="$proof_root/home" \
  SEW_GAP_CODE_WHEELHOUSE="$proof_root/wheels" SEW_MODE=standalone SEW_STATE_ROOT="$proof_root/state" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 SEW_TEST_INSTALLED=1 SEW_PUBLICATION_BATTERY="${SEW_PUBLICATION_BATTERY:-1}" \
  bash -euo pipefail -c '
    python3 -m pip install ".[test]"
    python3 bin/prepare-gap-code-wheelhouse --wheelhouse "$SEW_GAP_CODE_WHEELHOUSE" --download
    sew doctor > doctor.txt
    grep "mode         standalone" doctor.txt
    scripts/test-offline.sh
    if [[ "$SEW_PUBLICATION_BATTERY" == 1 ]]; then
      python3 scripts/offline-fixture-battery.py
    fi
  '
echo 'public-clone proof passed'
