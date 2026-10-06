Repair upload retry preparation. Rewind the body to the recorded position and return True; a body that cannot rewind returns False without crashing the batch.

Run `python -m pytest test_visible.py`. Edit app.py and requirements.txt.
