"""Run full or archive-free repository tests with Python socket access blocked."""

import argparse
import os
import socket
import sys
import unittest
from pathlib import Path

PUBLIC_MODULES = (
    "test_candidate_history.py",
    "test_exploratory_ablation.py",
    "test_exploratory_comparison.py",
    "test_report_exploratory.py",
)
PRIVATE_ARCHIVE_CASES = {
    ("test_exploratory_ablation", "test_separate_freeze_does_not_require_or_write_primary_results"),
    ("test_exploratory_comparison", "test_real_four_group_overlap_and_manifest_tamper_guards"),
}


class PublicTestLoader(unittest.TestLoader):
    def getTestCaseNames(self, test_case):
        return [name for name in super().getTestCaseNames(test_case)
                if (test_case.__module__, name) not in PRIVATE_ARCHIVE_CASES]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true", help="Run synthetic tests without the team's private archive")
    args = parser.parse_args()
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
    scope = "archive-free" if args.public else "full"
    print(f"Python {sys.version.split()[0]} | {scope} | PYTHONHASHSEED=0 | socket/urllib audit enabled", flush=True)
    test_dir = str(Path(__file__).resolve().parent)
    if args.public:
        loader = PublicTestLoader()
        suite = unittest.TestSuite(loader.discover(test_dir, pattern=pattern) for pattern in PUBLIC_MODULES)
    else:
        suite = unittest.defaultTestLoader.discover(test_dir)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    print(f"Summary: total={result.testsRun}, passed={passed}, failures={len(result.failures)}, "
          f"errors={len(result.errors)}, skipped={len(result.skipped)}, forbidden_network_attempts={len(blocked)}")
    return int(not result.wasSuccessful() or bool(blocked))


if __name__ == "__main__":
    raise SystemExit(main())
