Repair the session-token middleware: return claims for valid signed tokens and None for every rejected token. Use a dependency release that addresses the applicable advisory without weakening signature checks.

Run `python -m pytest test_visible.py`. Keep the public functions and their return contracts. Dependencies are pinned in requirements.txt.
