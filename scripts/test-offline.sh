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
    "$python_bin" -m pytest -q -p no:cacheprovider -p xdist.plugin -n auto --durations=30 "$@"
else
  # A Seatbelt wrapper would prevent the suite from applying its own profiles.
  # Use the hosted runner's firewall to preserve real nested verifier coverage.
  [[ "${GITHUB_ACTIONS:-}" == true ]] || {
    printf 'Offline runner failed (runner check): macOS offline runner requires a disposable hosted runner\n' >&2
    exit 1
  }
  run_pfctl() {
    local status=0
    # The clone proof's minimal PATH does not include /sbin.
    sudo /sbin/pfctl "$@" || status=$?
    if (( status != 0 )); then
      printf 'Offline runner failed (exit %d): sudo /sbin/pfctl %s\n' "$status" "$*" >&2
    fi
    return "$status"
  }
  firewall_info="$(run_pfctl -s info)"
  firewall_was_enabled="$(sed -n 's/^Status: \([^ ]*\).*/\1/p' <<< "$firewall_info")"
  case "$firewall_was_enabled" in
    Enabled|Disabled) ;;
    *)
      printf 'Offline runner failed (unexpected status): sudo /sbin/pfctl -s info\n%s\n' "$firewall_info" >&2
      exit 1
      ;;
  esac
  firewall_rules="$(mktemp)"
  printf 'set skip on lo0\nblock return out proto { tcp udp } all user root\n' > "$firewall_rules"
  restore_firewall() {
    local status=$? restore_status=0
    run_pfctl -f /etc/pf.conf || restore_status=$?
    if [[ "$firewall_was_enabled" == Disabled ]]; then
      run_pfctl -d || restore_status=$?
    fi
    rm -f "$firewall_rules" || restore_status=$?
    if (( status == 0 )); then status=$restore_status; fi
    exit "$status"
  }
  trap restore_firewall EXIT
  run_pfctl -f "$firewall_rules"
  if [[ "$firewall_was_enabled" == Disabled ]]; then run_pfctl -e; fi
  export SEW_REQUIRE_SEATBELT=1
  # The Actions agent runs as the unprivileged runner; only root test traffic
  # matches the firewall rule. Give Git the explicitly trusted checkout path.
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$PWD"
  sudo --preserve-env=PATH,SEW_REQUIRE_CONTAINMENT_TESTS,SEW_MODE,SEW_OFFLINE_TESTS,SEW_REQUIRE_SEATBELT,SEW_GAP_CODE_WHEELHOUSE,PYTEST_DISABLE_PLUGIN_AUTOLOAD,PYTHONDONTWRITEBYTECODE,GIT_CONFIG_COUNT,GIT_CONFIG_KEY_0,GIT_CONFIG_VALUE_0 \
    "$python_bin" -m pytest -q -p no:cacheprovider -p xdist.plugin -n auto --durations=30 "$@"
fi
