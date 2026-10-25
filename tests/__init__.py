"""Use the same bootstrap for package-style unittest invocations."""
import os
import sys

# `discover -s tests` puts this directory first on sys.path; package-style runs
# (`python -m unittest tests.<name>`) need the same so sibling helpers and test
# modules imported by bare name (probe_fixture, test_resign, ...) resolve.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

if "offline_env" not in sys.modules:
    from . import home_isolation
    sys.modules.setdefault("home_isolation", home_isolation)
    from . import offline_env
    sys.modules.setdefault("offline_env", offline_env)
