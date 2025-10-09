# Privileged Helpers

This directory hosts small privileged executables that RosDeck deploys on the host.

- `rosdeck_auth_helper.c` lives at the top level because it links against PAM and is
  typically only updated when authentication changes.
- `src/` keeps operational helpers (currently `rosdeck_control_helper.c`) that can
  be extended with additional tasks such as disk or network management.

Helpers are compiled and installed by `scripts/run_dev.sh`, which places the
resulting binaries in `/usr/local/libexec/` and manages the setuid bits. Keep
any new helpers within `src/` so the script can pick them up consistently.
