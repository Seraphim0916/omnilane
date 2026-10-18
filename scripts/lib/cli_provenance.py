#!/usr/bin/env python3
"""Classify an executable and verify its native Developer ID requirement.

Facts never grant trust: origin_policy matches them to host-local channels before
re-probing. The legacy verdict helper remains for older callers.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ADHOC = "adhoc"
UNSIGNED = "unsigned"
# The operator's statement that this vendor's executable is expected to be adhoc
# here (a local patch step re-signs it), so an adhoc update in the same install
# location may be re-probed unattended. Nothing else is waived.
TRUST_ADHOC = "adhoc-in-install-location"
# 1.0.25 -> 1.0.30 inside a path is an update; a different directory is not.
VERSION_SEGMENT = re.compile(r"\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.]+)*")


MACH_O = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
          b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
          b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
TEAM_ID = re.compile(r"^[A-Z0-9]{10}$")


def verify_origin_requirement(path, team, runner=subprocess.run):
    """Return Apple's external Developer ID check; never interpolate a shell."""
    if not isinstance(team, str) or not TEAM_ID.fullmatch(team):
        raise ValueError("invalid team identifier")
    requirement = ('anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] '
                   'exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
                   f'and certificate leaf[subject.OU] = "{team}"')
    # codesign(1), SPECIFYING REQUIREMENTS: plain text names a file; an initial
    # '=' introduces literal requirement source text, still passed as one argument.
    return runner(["codesign", "--verify", "--strict", "-R", "=" + requirement, str(path)],
                  capture_output=True, text=True, timeout=15)


def facts(path: Path | str, runner=subprocess.run) -> dict:
    """Classify the resolved entry itself, never its interpreter or its claims."""
    import sys
    result = {"kind": "unverifiable", "signer": UNSIGNED, "identifier": None, "valid": False}
    try:
        target = Path(path).resolve(strict=True)
        with target.open("rb") as stream:
            magic = stream.read(4)
        if magic.startswith(b"#!"):
            return dict(result, kind="script")
        if sys.platform != "darwin":
            if magic in MACH_O or magic == b"\x7fELF" or magic.startswith(b"MZ"):
                return dict(result, kind="no-platform-signature")
            return result
        if magic not in MACH_O:
            return result
        verified = runner(["codesign", "--verify", "--strict", str(target)],
                          capture_output=True, text=True, timeout=15)
        described = runner(["codesign", "-dv", "--verbose=2", str(target)],
                           capture_output=True, text=True, timeout=15)
        if described.returncode:
            if "code object is not signed at all" in described.stderr:
                return dict(result, kind="unsigned")
            return result
        fields = {}
        for line in described.stderr.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in ("TeamIdentifier", "Identifier", "Signature"):
                if key in fields:
                    return result
                fields[key] = value
        identifier = fields.get("Identifier")
        if not identifier or any(ord(c) < 32 for c in identifier):
            return result
        result["identifier"] = identifier
        if verified.returncode:
            return dict(result, kind="invalid", invalid_reason="integrity",
                        observed_team=fields.get("TeamIdentifier"))
        team = fields.get("TeamIdentifier")
        if team and team != "not set":
            if not TEAM_ID.fullmatch(team):
                return dict(result, kind="invalid")
            requirement = verify_origin_requirement(target, team, runner)
            if requirement.returncode == 3:
                return dict(result, kind="invalid", signer=team, invalid_reason="requirement")
            if requirement.returncode != 0:
                return dict(result, signer=team)
            return dict(result, kind="developer-id", signer=team, valid=True)
        if fields.get("Signature") == "adhoc" and team in (None, "not set"):
            return dict(result, kind="adhoc", signer=ADHOC, valid=True)
        return result
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return result
def identifier_family(identifier: str) -> str:
    """Normalize a codesign identifier, separately from filesystem locations."""
    return VERSION_SEGMENT.sub("<version>", identifier)


def family(path: Path | str) -> str:
    """Blank an install suffix's version, preserving all location ancestors.

    Only known vendor install layouts admit a version placeholder.
    """
    value = str(path)
    if "/" not in value or "\\" in value:
        return value
    # Collapse lexical separators only; resolving '..' could cross a symlink.
    parts = [part for part in value.split("/") if part not in ("", ".")]
    prefix = "/" if value.startswith("/") else ""
    index = len(parts) - 1
    if parts[-5:-1] == [".local", "share", "claude", "versions"]:
        pass
    elif parts[-3:-1] == [".grok", "downloads"] and parts[-1].startswith("grok-"):
        # Recognize release metadata explicitly. Unknown suffixes stay intact,
        # so a platform/architecture can never disappear into the version.
        match = re.fullmatch(
            r"grok-v?\d+(?:\.\d+)+"
            r"(?:-(?:alpha|beta|rc|dev|pre|preview|canary|nightly)(?:\.[0-9A-Za-z]+)*)?"
            r"(?:\+[0-9A-Za-z.]+)?(?P<suffix>-[0-9A-Za-z_.-]+)?", parts[-1])
        if match:
            parts[-1] = "grok-<version>" + (match["suffix"] or "")
        return prefix + "/".join(parts)
    elif parts[-2:] == ["bin", "codex"] and parts[-7:-3] == [".codex", "packages", "standalone", "releases"]:
        index -= 2
    else:
        return value
    if VERSION_SEGMENT.fullmatch(parts[index].removeprefix("v")):
        parts[index] = "<version>"
    return prefix + "/".join(parts)


def verdict(recorded: dict | None, recorded_path: str | None, current: dict,
            current_path: Path | str) -> tuple[bool, str]:
    """May this changed executable be re-probed unattended? And the reason either way."""
    if current.get("kind") in ("invalid", "unsigned", "unverifiable") or current.get("valid") is False:
        return False, "the executable signature is invalid or unverifiable; it cannot be approved"
    if current["signer"] == UNSIGNED:
        return False, "the executable is unsigned, so nothing ties it to the vendor; an operator has to approve it"
    if current["signer"] == ADHOC:
        if (recorded or {}).get("operator_trust") == TRUST_ADHOC and recorded_path \
                and family(recorded_path) == family(current_path):
            return True, "adhoc, which the operator trusts in this install location"
        if (recorded or {}).get("operator_trust") == TRUST_ADHOC:
            return False, (f"adhoc and installed at {current_path}, outside the location the operator "
                           f"trusts, {family(recorded_path)}")
        return False, ("the executable is adhoc, so nothing ties it to the vendor; an operator has to "
                       "approve it (or, for a local patch step, trust it here: omnilane resign --trust-adhoc VENDOR)")
    if not recorded or not recorded.get("signer"):
        return False, ("the live overlay recorded no signer for this vendor, so there is nothing "
                       "to compare against; an operator approves the first signer")
    if recorded["signer"] != current["signer"]:
        return False, (f"signed by {current['signer']}, but the overlay was signed against "
                       f"{recorded['signer']}")
    if recorded_path and family(recorded_path) != family(current_path):
        return False, (f"installed at {current_path}, outside the recorded location "
                       f"{family(recorded_path)}")
    return True, f"still signed by {current['signer']} in the recorded install location"
