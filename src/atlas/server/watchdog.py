"""The server leaves with the app that started it.

A parent-death watchdog, decided on 11/10/2026 (docs/ARCHITECTURE.md, Desktop app):

- The desktop starts `atlas serve --parent-pid <its pid>`. Without the check, an app that crashes
  leaves its server running, holding the conversations' turns and the port.
- Every 2 seconds the server asks whether that process is alive (`atlas.home.Parent`, which the
  update waits on the app with too), and stops when it is not. A check that cannot decide keeps
  serving: stopping a live user's server is the worse mistake.
"""

from atlas.home import Parent

#: How often the app is looked for.
POLL_SECONDS = 2.0
#: After the app is gone, the longest a graceful stop may take before the process just ends.
EXIT_CEILING_SECONDS = 10.0

__all__ = ["EXIT_CEILING_SECONDS", "POLL_SECONDS", "Parent"]
