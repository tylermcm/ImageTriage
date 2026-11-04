# Immutable CLI-Culler build inputs

These pure-Python wheels were built unchanged with
`scripts/build_cli_culler_wheels.py` from the exact revisions in
`../cli_culler_pins.json`. `provenance.json` records their source commits and
SHA-256 hashes; runtime preparation verifies those hashes before installation.

The engine wheel retains its original dependency metadata. The runtime resolver
overrides its branch-based bracketeering reference with the pinned wheel.
Vendoring these small build inputs lets the Windows runner build without a
separate cross-repository GitHub credential. License files and attribution
inside the original distributions are preserved.
