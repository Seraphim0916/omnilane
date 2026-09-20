#!/usr/bin/env python3
"""Who signed a vendor CLI, and whether a changed one may be re-signed unattended.

The transport overlay pins each vendor's executable by hash. Vendor CLIs update
themselves, so that pin goes stale as routine traffic. Re-pinning on sight would
turn the pin into a rubber stamp; this module is what stands in between. A
changed executable may be re-probed without an operator only when it still
carries the signer recorded when the overlay was last signed and still lives in
the same install location. Anything else is a notification, not a re-sign.
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


def facts(path: Path | str, runner=subprocess.run) -> dict:
    """codesign's view of one executable: the signing team, or why there is none."""
    target = str(path)
    try:
        verified = runner(["codesign", "--verify", "--strict", target],
                          capture_output=True, text=True, timeout=60)
        described = runner(["codesign", "-dv", "--verbose=2", target],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return {"signer": UNSIGNED, "identifier": None, "valid": False,
                "detail": f"codesign unavailable: {error.__class__.__name__}"}
    fields = {}
    for line in described.stderr.splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in fields:
            fields[key] = value
    valid = verified.returncode == 0
    team = fields.get("TeamIdentifier")
    if not valid:
        signer = UNSIGNED
    elif fields.get("Signature") == "adhoc" or team in (None, "not set"):
        signer = ADHOC
    else:
        signer = team
    return {"signer": signer, "identifier": fields.get("Identifier"), "valid": valid}


def family(path: Path | str) -> str:
    """An install location with its version numbers blanked out."""
    return VERSION_SEGMENT.sub("<version>", str(path))


def verdict(recorded: dict | None, recorded_path: str | None, current: dict,
            current_path: Path | str) -> tuple[bool, str]:
    """May this changed executable be re-probed unattended? And the reason either way."""
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
