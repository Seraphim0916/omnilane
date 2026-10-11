"""Retry eligibility is informational; only a fresh passing proof can verify a row."""

from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import probe

COOLDOWN = timedelta(hours=20)
ENVIRONMENT_REASONS = frozenset(("timeout", "api-error", "quota-exhausted", "not-authenticated",
                               "connection-error", "rate-limited"))
CONCLUSIVE_REASONS = frozenset(("model-mismatch", "client-record-mismatch", "effort-silently-defaulted"))
# Generic exits/results are retryable only when they identify an environmental
# cause. Never search these phrases inside a model/effort mismatch reason.
ENVIRONMENT_DETAIL = re.compile(
    r"not logged in|not signed in|authentication required|please run /login|grok login"
    r"|failed to authenticate|oauth (session|token) (has )?expired|could not be refreshed"
    r"|invalid api key|unauthorized|\b(401|403|429)\b|quota|rate.?limit|usage limit"
    r"|resource.exhausted|permission.denied"
    r"|api error|connection (refused|reset|failed|closed)|could not connect|failed to connect"
    r"|network (error|unreachable)|fetch failed|enotfound|econnrefused|econnreset|eai_again|getaddrinfo"
    r"|timed? ?out|overloaded|service unavailable|\b(500|502|503|529)\b",
    re.IGNORECASE,
)


def environment_reason(reason) -> str | None:
    if not isinstance(reason, str):
        return None
    base = reason.split(":", 1)[0]
    if base in ENVIRONMENT_REASONS:
        return base
    if base in ("exit-code", "result-error", "unexpected-stderr") and ENVIRONMENT_DETAIL.search(reason):
        return base
    return None


def cooldown(last_failed_at, *, now=None) -> tuple[bool, str | None]:
    """Missing legacy timestamps retry now; malformed/future timestamps fail closed."""
    if last_failed_at is None:
        return False, None
    try:
        stamp = datetime.fromisoformat(last_failed_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
        now = now or datetime.now(timezone.utc)
        until = stamp + COOLDOWN
        return now < until, until.isoformat()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return True, "invalid failure timestamp"


def contextual_failure(verdict, descriptor, stdout, stderr):
    """Identify plain-text login/network failures without changing the proof engine."""
    if verdict[0] == "fail" and verdict[1] in ("invalid-json", "invalid-json-result") \
            and descriptor.get("exit_code") != 0:
        reason = f"result-error: {(stderr or stdout)[:300]}"
        if environment_reason(reason):
            return verdict[0], reason, verdict[2], verdict[3]
    return verdict


def judge(root: Path, entry: dict, vendor: str, command: list[str], token: str, *, legacy=False):
    """Re-judge fixed evidence paths, never paths or retry classes supplied by JSON."""
    evidence = root / "evidence"
    descriptor = json.loads((evidence / f"{entry['name']}.json").read_text())
    if not isinstance(descriptor, dict):
        raise ValueError("invalid failed probe descriptor")
    actual = descriptor.get("command")
    if not isinstance(actual, list) or not all(isinstance(part, str) for part in actual):
        raise ValueError("failed probe has no request command")
    # The executable itself is checked by resign's existing path/hash/signer gate.
    # Gemini's private app directory changes between otherwise identical probes.
    def request(argv):
        return [part for part in argv[1:]
                if not (vendor == "gemini" and part.startswith("--app_data_dir="))]
    if request(actual) != request(command):
        raise ValueError("failed probe request contract mismatch")
    if type(descriptor.get("exit_code")) is not int or type(descriptor.get("timed_out")) is not bool:
        raise ValueError("failed probe has no complete exit status")
    streams = {suffix: (evidence / f"{entry['name']}.{suffix}").read_text(errors="replace")
               for suffix in ("stdout", "stderr")}
    extra = {suffix: path.read_text(errors="replace") for suffix in ("rollout", "cli_log")
             if (path := evidence / f"{entry['name']}.{suffix}").is_file()}
    verdict = probe.verdict(descriptor, streams["stdout"], streams["stderr"],
                            "agy" if vendor == "gemini" else vendor, token, extra)
    verdict = contextual_failure(verdict, descriptor, streams["stdout"], streams["stderr"])
    if descriptor.get("verdict") != verdict[0] and not (legacy and "verdict" not in descriptor):
        raise ValueError("failed probe verdict disagrees with raw evidence")
    if "verdict" not in descriptor:
        descriptor = dict(descriptor, **dict(zip(
            ("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict)))
    return descriptor, verdict


def pinned_failures(overlay):
    """Validate negative evidence with the same manifest anchor as passing proof.

    None denotes a genuinely old manifest with no failure-metadata contract.
    The version and nested file digests seal timestamp presence as well as value.
    """
    import aa_policy
    manifests = [e for e in overlay.get("evidence", [])
                 if "vendor" not in e and Path(e["path"]).name == "probe-manifest.json"]
    protected = None
    for anchor in manifests:
        manifest, actual = aa_policy._read_json(anchor["path"])
        aa_policy._check(actual == anchor["sha256"], f"failed probe manifest changed: {anchor['path']}")
        if "failure_metadata_version" not in manifest and "failed_probe_runs" not in manifest:
            continue
        aa_policy._check(manifest.get("failure_metadata_version") == 1 and
                         isinstance(manifest.get("failed_probe_runs"), dict),
                         f"failed probe integrity metadata missing or unsupported: {anchor['path']}")
        aa_policy._check(len(manifests) == 1, "failed probe has no unique protected manifest")
        protected = manifest["failed_probe_runs"]
        aa_policy._check(not (set(protected) & set(manifest.get("probe_runs", {}))),
                         "contradictory passing and failed probe evidence")
        for files in protected.values():
            aa_policy._check(isinstance(files, dict) and "json" in files,
                             "incomplete failed probe evidence")
            for entry in files.values():
                aa_policy._check(isinstance(entry, dict) and
                                 aa_policy.evidence_digest(entry["path"]) == entry["sha256"],
                                 "failed probe evidence changed; timestamp integrity cannot be verified: "
                                 + str(entry.get("path", "<missing path>")
                                       if isinstance(entry, dict) else "<invalid entry>"))
    return protected


def failed_probes(overlay: dict, vendor: str) -> dict[str, dict]:
    """Cross-check retry hints against the original failed request and response."""
    import probe_sweep
    entries = {entry["config_id"]: entry for entry in probe_sweep.plan(vendor)}
    mapped = {mapping.get("config_id") for mapping in overlay.get("mappings", [])}
    seen, failures = set(), {}
    manifests = [Path(e["path"]) for e in overlay.get("evidence", [])
                 if "vendor" not in e and Path(e["path"]).name == "probe-manifest.json"]
    protected = pinned_failures(overlay)
    for item in overlay.get("unproven", []):
        cid = item.get("config_id")
        if cid not in entries:
            continue
        if cid in seen or cid in mapped:
            raise ValueError("duplicate or contradictory failed probe row")
        seen.add(cid)
        reason = environment_reason(item.get("verdict_reason"))
        if not reason:
            continue
        if len(manifests) != 1:
            raise ValueError("failed probe has no unique evidence root")
        entry = entries[cid]
        if protected is not None:
            files = protected.get(cid, {})
            if files.get("json", {}).get("path") != str(manifests[0].parent / "evidence" / f"{entry['name']}.json"):
                raise ValueError("failed probe has no anchored descriptor")
        descriptor, verdict = judge(manifests[0].parent, entry, vendor,
                                     probe_sweep.command(vendor, entry["model"], entry["effort"]),
                                    probe_sweep.TOKENS[vendor], legacy=True)
        if verdict[0] != "fail" or environment_reason(verdict[1]) != reason:
            raise ValueError("environment failure disagrees with raw probe evidence")
        if environment_reason(descriptor.get("verdict_reason")) != reason:
            raise ValueError("environment failure disagrees with probe descriptor")
        stamp = descriptor.get("last_failed_at")
        if ("last_failed_at" in item) != ("last_failed_at" in descriptor) or item.get("last_failed_at") != stamp:
            raise ValueError("failure timestamp disagrees with probe descriptor")
        # An explicit null is malformed, unlike a missing legacy field.
        if "last_failed_at" in descriptor and stamp is None:
            stamp = "invalid failure timestamp"
        failures[cid] = dict(reason=reason, last_failed_at=stamp)
    return failures


def validate_retry_results(root: Path, vendor: str, selected, result: dict) -> None:
    """A retry cannot install partial, relabelled or wrong-model passing evidence."""
    import probe_sweep
    passed, failed = result["passed"], result["failed"]
    reported = passed + failed
    if len(set(reported)) != len(reported):
        raise ValueError("duplicate retry result")
    entries = {entry["config_id"]: entry for entry in probe_sweep.plan(vendor)}
    if not set(reported) <= set(entries):
        raise ValueError("unknown retry result")
    for cid in selected:
        entry = entries[cid]
        descriptor, verdict = judge(root, entry, vendor,
                                     probe_sweep.command(vendor, entry["model"], entry["effort"]),
                                     probe_sweep.TOKENS[vendor])
        expected = "pass" if cid in passed else "fail"
        if verdict[0] != expected:
            raise ValueError("retry report disagrees with raw probe evidence")
        descriptor.update(verdict_reason=verdict[1], observed_model=verdict[2], evidence_tier=verdict[3])
        if cid not in reported:
            continue
        if expected == "fail":
            descriptor["last_failed_at"] = datetime.now(timezone.utc).isoformat()
        else:
            descriptor.pop("last_failed_at", None)
        (root / "evidence" / f"{entry['name']}.json").write_text(json.dumps(descriptor, indent=2) + "\n")


def run_sweep(vendor: str, root: Path, *, log=print, only_missing=False,
              retry_failed=(), refresh_expired=(), skip_configs=(), retry_now=False) -> dict:
    """Reuse the pinned sweep engine, exposing only eligible staged gaps to it.

    The original live evidence never changes. The pinned engine's handling of a
    first-time login failure is preserved; retry login failures keep their new
    negative evidence so the next unattended run can respect the cooldown.
    """
    import probe_sweep
    entries = {entry["config_id"]: entry for entry in probe_sweep.plan(vendor)}
    by_name = {entry["name"]: entry for entry in entries.values()}
    suffixes = ("json", "stdout", "stderr", "rollout", "cli_log")
    def snapshot(name):
        return {suffix: path.read_bytes() for suffix in suffixes
                if (path := root / "evidence" / f"{name}.{suffix}").is_file()}
    def restore(name, saved):
        for suffix, content in saved.items():
            (root / "evidence" / f"{name}.{suffix}").write_bytes(content)
    originals, skipped, captured = {}, {}, {}
    for cid in (*retry_failed, *refresh_expired):
        entry = entries[cid]
        if cid in retry_failed:
            _, verdict = judge(root, entry, vendor,
                               probe_sweep.command(vendor, entry["model"], entry["effort"]),
                               probe_sweep.TOKENS[vendor], legacy=True)
            if verdict[0] != "fail" or not environment_reason(verdict[1]):
                raise ValueError("only environmental failures may be retried")
        originals[cid] = snapshot(entry["name"])
        before = root / "retry-before"
        before.mkdir(exist_ok=True)
        # A new probe may produce no client record. Never let an old rollout or
        # CLI log raise the evidence tier of that new response.
        for suffix in originals[cid]:
            (root / "evidence" / f"{entry['name']}.{suffix}").rename(before / f"{entry['name']}.{suffix}")
    for cid in skip_configs:
        skipped[cid] = snapshot(entries[cid]["name"])

    def run_probe(name, argv, **kwargs):
        cid = by_name[name]["config_id"]
        if cid in skipped:
            return json.loads(skipped[cid]["json"])
        if cid in captured and not retry_now:
            previous = json.loads(captured[cid]["json"])
            if previous.get("verdict") == "fail" and environment_reason(previous.get("verdict_reason")):
                log(f"{name}: environment probe already failed; unattended cooldown prevents another call")
                return previous
        record = probe_sweep.probe_module.probe(name, argv, **kwargs)
        if record.get("verdict") == "fail":
            streams = [(root / "evidence" / f"{name}.{suffix}").read_text(errors="replace")
                       for suffix in ("stdout", "stderr")]
            verdict = contextual_failure((record["verdict"], record.get("verdict_reason"),
                                          record.get("observed_model"), record.get("evidence_tier")),
                                         record, *streams)
            record["verdict_reason"] = verdict[1]
            record["last_failed_at"] = datetime.now(timezone.utc).isoformat()
            (root / "evidence" / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
        captured[cid] = snapshot(name)
        return record

    try:
        result = probe_sweep.sweep(vendor, root, log=log, only_missing=only_missing, run_probe=run_probe)
        # The original engine deletes descriptors for unauthenticated probes.
        # Restore only completed negative retry records; never synthesize a pass.
        retry_login = False
        retained = set(captured) if only_missing else set(retry_failed) & set(captured)
        for cid in retained:
            saved = captured[cid]
            record = json.loads(saved["json"])
            if record.get("verdict") == "fail" and environment_reason(record.get("verdict_reason")):
                restore(entries[cid]["name"], saved)
                if probe_sweep.NOT_AUTHENTICATED.search(probe_sweep._text(record)):
                    retry_login = True
                    if cid not in result["failed"]:
                        result["failed"].append(cid)
        if only_missing and retry_login and result["outcome"] == "unprobeable":
            result["outcome"] = "done"
        return result
    finally:
        for cid, saved in originals.items():
            name = entries[cid]["name"]
            if cid not in captured and not (root / "evidence" / f"{name}.json").exists():
                restore(name, saved)
        for cid, saved in skipped.items():
            restore(entries[cid]["name"], saved)
