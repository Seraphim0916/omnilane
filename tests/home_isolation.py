# Keep HOME and OMNILANE_HOME in a temporary home until process exit.
# The audit hook watches open, os.listdir/os.scandir, os.chdir,
# os.remove/os.rmdir/os.mkdir, os.rename/os.link/os.symlink,
# os.chmod/os.chown/os.utime/os.truncate, and subprocess.Popen.
# It refuses real-home access and child escapes before OS access occurs.
# os.stat and os.lstat are replaced: neither emits an audit event.
# unittest.TestCase.run is wrapped only to name the test in diagnostics.
# The atexit handler forces failure even if a test swallows a refusal.
# A deliberate host check uses "with allow_real_omnilane_reads() as home:".
# It yields the recorded real .omnilane path and allows only this test's
# reads of that subtree during the context; writes and child escapes stay blocked.
"""Process-lifetime HOME isolation, with a fail-closed real-home tripwire."""
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import sys
import tempfile
import unittest


SUITE_ROOT = None
REAL_HOME = None
_READ_ALLOWANCE = ContextVar("omnilane_real_home_read_allowance", default=None)


def _test_case():
    frame = sys._getframe(1)
    while frame is not None:
        case = frame.f_locals.get("self")
        if isinstance(case, unittest.TestCase):
            return case
        frame = frame.f_back
    return None


@contextmanager
def allow_real_omnilane_reads():
    """Allow this test's in-process, read-only access to recorded host evidence."""
    activate()
    home = REAL_HOME / ".omnilane"
    token = _READ_ALLOWANCE.set((str(home), _test_case()))
    try:
        yield home
    finally:
        _READ_ALLOWANCE.reset(token)


def activate():
    global SUITE_ROOT, REAL_HOME
    if SUITE_ROOT is not None:
        return SUITE_ROOT

    real_home = os.path.abspath(os.path.expanduser("~"))
    REAL_HOME = Path(real_home)
    forbidden = {os.path.join(real_home, ".omnilane")}
    if os.environ.get("OMNILANE_HOME"):
        forbidden.add(os.path.abspath(os.environ["OMNILANE_HOME"]))
    temporary = tempfile.TemporaryDirectory(prefix="omnilane-python-")
    SUITE_ROOT = Path(temporary.name)
    home = SUITE_ROOT / "home"
    (home / ".omnilane").mkdir(parents=True)
    os.environ["HOME"] = str(home)
    os.environ["OMNILANE_HOME"] = str(home / ".omnilane")
    violations = []
    current_test = ""
    original_run = unittest.TestCase.run

    def run_test(case, *args, **kwargs):
        nonlocal current_test
        previous, current_test = current_test, case.id()
        try:
            return original_run(case, *args, **kwargs)
        finally:
            current_test = previous

    unittest.TestCase.run = run_test

    def refuse(reason):
        frame = sys._getframe(1)
        name = current_test or "<test import>"
        while frame is not None:
            case = frame.f_locals.get("self")
            if isinstance(case, unittest.TestCase):
                name = case.id()
                break
            module = frame.f_globals.get("__name__", "")
            if module.startswith(("test_", "tests.test_")):
                name = module
            frame = frame.f_back
        message = f"omnilane home tripwire: {name}: {reason}"
        violations.append(message)
        raise AssertionError(message)

    def check_path(path, *, read_only=False):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        path = os.path.abspath(os.fsdecode(path))
        if any(path == root or path.startswith(root + os.sep) for root in forbidden):
            allowance = _READ_ALLOWANCE.get()
            if read_only and allowance is not None:
                root, owner = allowance
                if (path == root or path.startswith(root + os.sep)) and _test_case() is owner:
                    return
            refuse("real omnilane path refused: " + path)

    def audit(event, args):
        # Refuse before OS access: missing/unreadable homes must fail too.
        if event == "open":
            flags = args[2]
            mutations = os.O_CREAT | os.O_TRUNC | os.O_APPEND
            mutations |= getattr(os, "O_TMPFILE", 0)
            read_only = flags & (os.O_ACCMODE | mutations) == os.O_RDONLY
            check_path(args[0], read_only=read_only)
        elif event in ("os.listdir", "os.scandir"):
            check_path(args[0], read_only=True)
        elif event in ("os.chdir", "os.remove", "os.rmdir", "os.mkdir",
                       "os.chmod", "os.chown", "os.utime", "os.truncate"):
            check_path(args[0])
        elif event in ("os.rename", "os.link", "os.symlink"):
            check_path(args[0])
            check_path(args[1])
        elif event == "subprocess.Popen":
            environment = os.environ if args[3] is None else args[3]
            child_home = environment.get("HOME") or environment.get(b"HOME")
            child_omnilane = environment.get("OMNILANE_HOME") or environment.get(b"OMNILANE_HOME")
            if child_home and os.path.abspath(os.fsdecode(child_home)) == real_home:
                refuse("child restores pre-isolation HOME")
            if child_omnilane:
                check_path(child_omnilane)
            elif not child_home:
                refuse("child has neither HOME nor OMNILANE_HOME")
            command = args[1]
            parts = [command] if isinstance(command, (str, bytes)) else command
            for part in parts:
                if isinstance(part, (str, bytes)):
                    value = os.fsdecode(part)
                    if any(root in value for root in forbidden):
                        refuse("child command references a real omnilane path")

    # stat/lstat have no audit event; cover exists()/is_file() as well.
    for operation in ("stat", "lstat"):
        original = getattr(os, operation)

        def guarded_stat(path, *args, _original=original, **kwargs):
            check_path(path, read_only=True)
            return _original(path, *args, **kwargs)

        setattr(os, operation, guarded_stat)
    sys.addaudithook(audit)

    def finish():
        temporary.cleanup()
        if violations:
            # Swallowing the immediate exception must not turn the run green.
            print("\n".join(violations), file=sys.stderr, flush=True)
            os._exit(1)

    atexit.register(finish)
    return SUITE_ROOT
