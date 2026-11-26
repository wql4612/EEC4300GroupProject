"""Run the full repository test suite with Python socket access blocked."""

import os
import socket
import sys
import unittest
from pathlib import Path


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit("Set PYTHONHASHSEED=0 before Python starts for the pinned official Prompt renderer")
    # Skip urllib3's optional local socket probe at import. The audit hook
    # still blocks every socket creation, bind, DNS lookup and outbound request.
    socket.has_ipv6 = False
    blocked = []

    def deny_network(event, args):
        if (event.startswith("socket.") and event != "socket.gethostname") or event == "urllib.Request":
            blocked.append(event)
            raise AssertionError(f"Network access during offline tests: {event}")

    sys.addaudithook(deny_network)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    print(f"Python {sys.version.split()[0]} | PYTHONHASHSEED=0 | socket/urllib audit enabled", flush=True)
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print(f"Summary: total={result.testsRun}, passed={passed}, failures={len(result.failures)}, "
          f"errors={len(result.errors)}, skipped={len(result.skipped)}, forbidden_network_attempts={len(blocked)}")
    return int(not result.wasSuccessful() or bool(blocked))


if __name__ == "__main__":
    raise SystemExit(main())
