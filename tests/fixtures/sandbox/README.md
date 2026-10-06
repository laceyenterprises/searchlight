# Recorded isolated pip canary output

These are excerpts of real `python -m pip --isolated download -vv
--no-cache-dir --retries 0 --timeout 5 --index-url https://1.1.1.1/simple
--dest . pip` output captured on 2026-10-04. Only the index, connection-error
and terminal ERROR lines are retained, verbatim.

- `seatbelt-pip.txt`: Homebrew Python 3.14, pip 26.1.2, macOS Seatbelt
  `(version 1)(allow default)(deny network-outbound)`; exit 1, errno 1.
- `bubblewrap-pip.txt`: Python 3.12, pip 25.0.1, Debian Linux in a disposable
  container, bubblewrap 0.12.0 with `--unshare-net`; exit 1, errno 101.

Without `-vv`, both runs printed only the index and two terminal ERROR lines.
A single `-v` also omitted denial text on the macOS host. Those generic errors
must never qualify containment. The runtime canary uses a shorter two-second
network timeout; real-backend tests execute it rather than replaying fixtures.
