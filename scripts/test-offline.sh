#!/usr/bin/env bash
# Install dependencies before invoking this runner. Only loopback is available.
set -euo pipefail
export SEW_MODE=standalone
export SEW_OFFLINE_TESTS=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
# The suite runs as root below; never leave root-owned bytecode or pytest cache in the tree,
# or the unprivileged caller cannot clean up its checkout afterwards.
export PYTHONDONTWRITEBYTECODE=1
python_bin="$(command -v python3)"
if [[ "$(uname -s)" == Linux ]]; then
  export SEW_REQUIRE_BUBBLEWRAP=1
  exec sudo --preserve-env=PATH,SEW_REQUIRE_CONTAINMENT_TESTS,SEW_MODE,SEW_OFFLINE_TESTS,SEW_REQUIRE_BUBBLEWRAP,SEW_GAP_CODE_WHEELHOUSE,PYTEST_DISABLE_PLUGIN_AUTOLOAD,PYTHONDONTWRITEBYTECODE \
    unshare --net -- bash -c 'ip link set lo up; exec "$@"' bash \
    "$python_bin" -m pytest -q -p no:cacheprovider
else
  # A Seatbelt wrapper would prevent the suite from applying its own profiles.
  # Use the hosted runner's firewall to preserve real nested verifier coverage.
  [[ "${GITHUB_ACTIONS:-}" == true ]] || { echo 'macOS offline runner requires a disposable hosted runner' >&2; exit 1; }
  firewall_rules="$(mktemp)"
  printf 'set skip on lo0\nblock return out proto { tcp udp } all user root\n' > "$firewall_rules"
  firewall_was_enabled="$(sudo pfctl -s info 2>/dev/null | sed -n 's/^Status: \([^ ]*\).*/\1/p')"
  restore_firewall() {
    sudo pfctl -f /etc/pf.conf
    if [[ "$firewall_was_enabled" == Disabled ]]; then sudo pfctl -d; fi
    rm -f "$firewall_rules"
  }
  trap restore_firewall EXIT
  sudo pfctl -f "$firewall_rules"
  sudo pfctl -e 2>/dev/null || [[ "$firewall_was_enabled" == Enabled ]]
  export SEW_REQUIRE_SEATBELT=1
  # The Actions agent runs as the unprivileged runner; only root test traffic
  # matches the firewall rule. Give Git the explicitly trusted checkout path.
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"
  sudo --preserve-env=PATH,SEW_REQUIRE_CONTAINMENT_TESTS,SEW_MODE,SEW_OFFLINE_TESTS,SEW_REQUIRE_SEATBELT,SEW_GAP_CODE_WHEELHOUSE,PYTEST_DISABLE_PLUGIN_AUTOLOAD,PYTHONDONTWRITEBYTECODE,GIT_CONFIG_COUNT,GIT_CONFIG_KEY_0,GIT_CONFIG_VALUE_0 \
    "$python_bin" -m pytest -q -p no:cacheprovider
fi
