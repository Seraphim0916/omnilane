"""Use the same bootstrap for package-style unittest invocations."""
import sys

if "offline_env" not in sys.modules:
    from . import home_isolation
    sys.modules.setdefault("home_isolation", home_isolation)
    from . import offline_env
    sys.modules.setdefault("offline_env", offline_env)
