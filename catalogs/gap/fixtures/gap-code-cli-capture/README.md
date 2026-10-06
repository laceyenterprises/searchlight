Repair the CLI test helper so native writers can access a real output descriptor during command execution. Keep captured output and avoid corrupting the parent test runner.

Run `python -m pytest test_visible.py`. Keep the public functions and their return contracts. Dependencies are pinned in requirements.txt.
