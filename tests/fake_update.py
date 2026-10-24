"""A stand-in for `python -m atlas.installation`, for the app's end-to-end test of its update.

The app runs it as it runs the module (`ATLAS_E2E_UPDATE` names it, only under `--atlas-e2e`),
with the same arguments, read by the module's own parser (a parser of its own here once missed a
flag the app had begun to send, and the hand-off never took). `check` says `main` brings two
commits. `handoff` does what the real one does around the update, for real: its marker with its
own process id at once, then it waits for the app to be gone (`atlas.home.Parent`, the real
wait), writes how it went to `--result` and goes, the marker removed. It changes nothing and opens
nothing: the test opens the app again itself. The update between is proved by
`tests/installation/test_installation.py`, on real git.
"""

import json
import os
import sys
import time
from datetime import UTC, datetime

from atlas.home import Parent
from atlas.installation.__main__ import arguments

COMMITS = ["Step 18: the button", "Step 18: the hand-off"]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"


def main() -> int:
    args = arguments().parse_args()
    if args.command == "check":
        status = {
            "supported": True,
            "available": True,
            "reason": "",
            "current": "a" * 40,
            "target": "b" * 40,
            "behind": len(COMMITS),
            "commits": COMMITS,
            "commits_pt_br": ["", ""],
            "dirty": False,
            "error": "",
        }
        sys.stdout.write(json.dumps(status) + "\n")
        return 0
    app = Parent(args.app_pid)
    args.marker.write_text(json.dumps({"pid": os.getpid(), "started_at": _now()}), "utf-8")
    deadline = time.monotonic() + 120
    while app.alive() and time.monotonic() < deadline:
        time.sleep(0.2)
    closed = not app.alive()
    args.log.write_text("The app closed.\n" if closed else "The app stayed open.\n", "utf-8")
    ended = {
        "ok": closed,
        "before": "a" * 40,
        "after": "b" * 40 if closed else "",
        "commits": [f"bbbbbbb {subject}" for subject in COMMITS] if closed else [],
        "error": "" if closed else "The app did not close, so nothing was updated.",
        "log": str(args.log),
        "ended_at": _now(),
        "reopen": "",
    }
    args.result.write_text(json.dumps(ended), "utf-8")
    args.marker.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
