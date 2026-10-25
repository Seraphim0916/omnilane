"""Codex work-mode setup the runner cannot do safely in shell.

  headers VALUE   print VALUE re-serialized as a TOML inline table of string
                  header names to string values; exit 2 if it is anything else
  tmpdir WORKDIR  create WORKDIR/.omnilane-tmp without following links, print
                  its path; exit 125 if the name is not a directory we own
"""

import os
import re
import stat
import subprocess
import sys

TMP_NAME = ".omnilane-tmp"
EXCLUDE_LINE = "/" + TMP_NAME + "/"
# RFC 9110 token for the name; the value is printable ASCII without the two
# characters a TOML basic string would have to escape.
HEADER_NAME = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
HEADER_VALUE = r'[ !#-\[\]-~]*'
PAIR = re.compile(r'\s*(?:"(%s)"|(%s))\s*=\s*"(%s)"\s*' % (HEADER_NAME, HEADER_NAME, HEADER_VALUE))


def fail(code, message):
    print("omnilane: " + message, file=sys.stderr)
    sys.exit(code)


def headers(value):
    text = value.strip()
    if not (text.startswith("{") and text.endswith("}")):
        fail(2, "OMNILANE_CODEX_HTTP_HEADERS must be one TOML inline table of string headers")
    body = text[1:-1]
    pairs = []
    if body.strip():
        for part in body.split(","):
            match = PAIR.fullmatch(part)
            if not match:
                fail(2, "OMNILANE_CODEX_HTTP_HEADERS accepts only \"Header-Name\"=\"value\" pairs "
                        "(token names, printable values without quotes or backslashes)")
            pairs.append((match.group(1) or match.group(2), match.group(3)))
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
    parent = os.path.join(common, "info")
    path = os.path.join(parent, "exclude")
    try:
        if not os.path.isdir(parent):
            os.mkdir(parent, 0o755)
        if os.path.islink(parent) or not stat.S_ISDIR(os.lstat(parent).st_mode):
            raise OSError("info is not a plain directory")
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError as error:
        print("omnilane: warning: left %s unchanged: %s" % (path, error), file=sys.stderr)
        return
    with os.fdopen(fd, "r+", encoding="utf-8", errors="replace") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            print("omnilane: warning: left %s unchanged: not a regular file" % path, file=sys.stderr)
            return
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
        try:
            os.mkdir(TMP_NAME, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        try:
            fd = os.open(TMP_NAME, flags, dir_fd=parent)
        except OSError:
            fail(125, "unsafe Codex work temp path")
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                fail(125, "unsafe Codex work temp path")
            # chmod the object we opened, not whatever the name points to now.
            os.fchmod(fd, 0o700)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    add_exclude(workdir)
    print(os.path.join(workdir, TMP_NAME))


def main(argv):
    if len(argv) != 3 or argv[1] not in ("headers", "tmpdir"):
        fail(2, "usage: codex_worker_setup.py headers VALUE | tmpdir WORKDIR")
    (headers if argv[1] == "headers" else tmpdir)(argv[2])


if __name__ == "__main__":
    main(sys.argv)
