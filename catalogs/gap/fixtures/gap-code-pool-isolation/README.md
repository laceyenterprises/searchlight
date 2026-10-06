Repair the HTTP pool factory. It must continue using the standard queue captured when this module loads, even if unrelated plugins later replace the global queue factory. Preserve the requested pool capacity.

Run `python -m pytest test_visible.py`. Keep the public functions and their return contracts. Dependencies are pinned in requirements.txt.
