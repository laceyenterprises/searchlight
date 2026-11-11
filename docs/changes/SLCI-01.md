# Standalone CI

## Change notes

SLCI-01: Pull requests run Python 3.12 on Linux. Main pushes retain Python
3.11–3.13 on Linux; scheduled and manual runs retain all three versions on Linux
and macOS. Each job has a 75-minute cap. The fresh installed offline suite uses
pytest-xdist (`-p xdist.plugin -n auto`, including with plugin autoload disabled)
and prints the 30 slowest test phases (`--durations=30`). Network isolation and
mandatory Linux containment checks still apply.
