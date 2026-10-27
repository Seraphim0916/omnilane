"""Codex work-mode setup the runner cannot do safely in shell.

  headers VALUE   print VALUE re-serialized as a TOML inline table of string
                  header names to string values; exit 2 if it is anything else
  tmpdir WORKDIR  create a per-job directory in WORKDIR/.omnilane-tmp, print
                  its path; exit 125 if the name is not a directory we own
  cleanup PATH    remove the per-job directory without following parent links
"""

import os
import re
import stat
import subprocess
import sys
import uuid

TMP_NAME = ".omnilane-tmp"
EXCLUDE_LINE = "/" + TMP_NAME + "/"
# RFC 9110 token for the name; the value is printable ASCII without the two
# characters a TOML basic string would have to escape.
HEADER_NAME = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
HEADER_VALUE = r'[ !#-\[\]-~]*'
# Matched left to right, so a comma inside a quoted value never splits a pair.
PAIR = re.compile(r'\s*(?:"(%s)"|(%s))\s*=\s*"(%s)"\s*(,|$)' % (HEADER_NAME, HEADER_NAME, HEADER_VALUE))


def fail(code, message):
    print("omnilane: " + message, file=sys.stderr)
    sys.exit(code)


def headers(value):
    text = value.strip()
    if not (text.startswith("{") and text.endswith("}")):
        fail(2, "OMNILANE_CODEX_HTTP_HEADERS must be one TOML inline table of string headers")
    body = text[1:-1]
    pairs = []
    position = 0
    while body[position:].strip():
        match = PAIR.match(body, position)
        if not match or (match.group(4) == "," and not body[match.end():].strip()):
            fail(2, "OMNILANE_CODEX_HTTP_HEADERS accepts only \"Header-Name\"=\"value\" pairs "
                    "(token names, printable values without quotes or backslashes)")
        pairs.append((match.group(1) or match.group(2), match.group(3)))
        position = match.end()
    if not pairs:
        fail(2, "OMNILANE_CODEX_HTTP_HEADERS has no headers")
    names = [name.lower() for name, _ in pairs]
    if len(set(names)) != len(names):
        fail(2, "OMNILANE_CODEX_HTTP_HEADERS repeats a header name")
    print("{" + ", ".join('"%s"="%s"' % pair for pair in pairs) + "}")


def add_exclude(workdir):
    try:
        # Not --git-path info/exclude: that answer already has links resolved,
        # so a no-follow open would be checking the link's target.
        common = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return  # not a git work tree
    path = os.path.join(common, "info", "exclude")
    # The ignore rule is a convenience: any failure here (a link, a full disk)
    # is a warning and the worker still starts.
    try:
        _append_exclude(common)
    except OSError as error:
        print("omnilane: warning: left %s unchanged: %s" % (path, error), file=sys.stderr)


def _append_exclude(common):
    directory = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    # Each step opens relative to a descriptor already held, so swapping
    # `info` for a link after any check cannot redirect the write.
    common_fd = os.open(common, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            os.mkdir("info", 0o755, dir_fd=common_fd)
        except FileExistsError:
            pass
        info_fd = os.open("info", directory, dir_fd=common_fd)
    finally:
        os.close(common_fd)
    try:
        fd = os.open("exclude", os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=info_fd)
    finally:
        os.close(info_fd)
    with os.fdopen(fd, "r+", encoding="utf-8", errors="replace") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        handle.seek(0)
        content = handle.read()
        if EXCLUDE_LINE in content.splitlines():
            return
        handle.write(("" if content == "" or content.endswith("\n") else "\n") + EXCLUDE_LINE + "\n")


def tmpdir(workdir):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    try:
        parent = os.open(workdir, flags)
    except OSError as error:
        fail(125, "Codex workdir cannot be opened: %s" % error)
    try:
        fd = None
        try:
            try:
                os.mkdir(TMP_NAME, 0o700, dir_fd=parent)
            except FileExistsError:
                pass  # Validate the existing entry with a no-follow open.
            fd = os.open(TMP_NAME, flags, dir_fd=parent)
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                fail(125, "unsafe Codex work temp path")
            os.fchmod(fd, 0o700)
            job = "job-" + uuid.uuid4().hex
            os.mkdir(job, 0o700, dir_fd=fd)
        except OSError as error:
            fail(125, "unsafe Codex work temp path: %s" % error)
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        os.close(parent)
    add_exclude(workdir)
    print(os.path.join(workdir, TMP_NAME, job))


def _require_identity(expected, actual):
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        raise OSError("Codex cleanup entry identity changed")


def _remove_entry(parent_fd, name, expected, is_directory=False):
    _require_identity(expected, os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
    # Python's portable unlink/rmdir APIs have no atomic expected-inode
    # condition. A name can still change between this check and removal;
    # dir_fd pins the parent and neither operation follows a replaced symlink.
    if is_directory:
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


def _remove_directory(parent_fd, name, flags, expected=None):
    # Every name is a single component relative to a pinned directory. Never
    # chmod a file or symlink: unlink only needs write access to its parent.
    if expected is None:
        expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    # Refuse an unreadable directory rather than chmod by name: that could
    # change a replacement before we have a descriptor to verify its identity.
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        _require_identity(expected, os.fstat(fd))
        os.fchmod(fd, 0o700)
        for entry in os.listdir(fd):
            try:
                info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    _remove_directory(fd, entry, flags, info)
                else:
                    _remove_entry(fd, entry, info)
            except FileNotFoundError:
                pass
        _remove_entry(parent_fd, name, expected, is_directory=True)
    finally:
        os.close(fd)


def cleanup(path):
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        fail(125, "unsafe Codex cleanup path")
    base, job = os.path.split(path)
    if (os.path.basename(base) != TMP_NAME or
            not re.fullmatch(r"job-[0-9a-f]{32}", job) or
            os.path.realpath(base) != base):
        fail(125, "unsafe Codex cleanup path")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    try:
        parent = os.open(os.path.dirname(base), flags)
    except FileNotFoundError:
        return
    try:
        try:
            fd = os.open(TMP_NAME, flags, dir_fd=parent)
        except FileNotFoundError:
            return
        try:
            try:
                _remove_directory(fd, job, flags)
            except FileNotFoundError:
                pass
            except OSError as error:
                fail(125, "unsafe Codex cleanup path: %s" % error)
        finally:
            os.close(fd)
        # Keep the shared base: another creator may already hold it open.
    finally:
        os.close(parent)


def main(argv):
    commands = {"headers": headers, "tmpdir": tmpdir, "cleanup": cleanup}
    if len(argv) != 3 or argv[1] not in commands:
        fail(2, "usage: codex_worker_setup.py headers VALUE | tmpdir WORKDIR | cleanup PATH")
    commands[argv[1]](argv[2])


if __name__ == "__main__":
    main(sys.argv)
