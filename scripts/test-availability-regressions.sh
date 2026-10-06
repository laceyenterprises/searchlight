#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec python3 -m pytest -q tests/test_live_runner.py tests/test_provider_availability.py tests/test_gap_runner.py tests/test_gap_report.py tests/test_schema.py tests/test_vendor_pricing.py tests/test_gap_workspace.py::test_brief_search_arm_uses_the_gap_catalog_prompt_and_budgets
