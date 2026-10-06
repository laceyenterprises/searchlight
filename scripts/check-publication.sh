#!/usr/bin/env bash
# Keep developer/worker host packages outside the public dependency audit.
set -euo pipefail
cd "$(dirname "$0")/.."
audit_venv="$(mktemp -d)"
trap 'rm -rf "$audit_venv"' EXIT
python3 -m venv "$audit_venv"
unset PYTHONPATH
"$audit_venv/bin/python3" -m pip install '.[test]'
for gate in hardcodes secrets licences; do
  "$audit_venv/bin/python3" scripts/publication-gates.py "$gate"
done
