"""Retry eligibility is informational; only a fresh passing proof can verify a row."""

from __future__ import annotations
import contextlib
import json
import os
import re
import signal
import threading
from types import SimpleNamespace
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import probe

ADVISOR_KILL_SWITCH = "CLAUDE_CODE_DISABLE_ADVISOR_TOOL"
COOLDOWN = timedelta(hours=20)
ENVIRONMENT_REASONS = frozenset(("timeout", "api-error", "quota-exhausted", "not-authenticated",
                               "connection-error", "rate-limited"))
CONCLUSIVE_REASONS = frozenset(("model-mismatch", "client-record-mismatch", "effort-silently-defaulted",
                               "model-account-mismatch", "evidence-tier-regression"))
TIER_RANK = {"selector-only": 0, "client-echo": 1, "billed-model": 2}
RESET_HINT = re.compile(r"\bresets?\s+in\s+((?:\d+\s*[dhms]\s*)+)", re.IGNORECASE)
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
    if "; stderr-review: " in reason:
        # Codex's final event owns the outcome. MCP startup diagnostics are not
        # evidence about the provider's authentication or model availability.
        reason = engine_view("codex", {"verdict": "fail", "verdict_reason": reason})["verdict_reason"]
    base = reason.split(":", 1)[0]
    if base in ENVIRONMENT_REASONS:
        return base
    if base in ("exit-code", "result-error", "unexpected-stderr") and ENVIRONMENT_DETAIL.search(reason):
        return base
    return None


def public_reason(reason) -> str:
    """Bound diagnostic hints before they cross from local proof to a caller."""
    text = " ".join(str(reason or "").split())
    if text in CONCLUSIVE_REASONS:
        return text
    base, separator, detail = text.partition(":")
    prefix = base + ": " if separator and base in CONCLUSIVE_REASONS else ""
    if prefix:
        text = detail.lstrip()
    hint = RESET_HINT.search(text)
    reset = hint.group(0).strip() if hint else ""
    if hint:
        text = text[:hint.start()] + text[hint.end():]
    text = re.sub(r"[\w.+%-]+@[\w.-]+", "[redacted]", text)
    text = re.sub(r"(?i)\b(?:account|organisation|organization|org|tenant|user)[ _-]*(?:id)?"
                  r"[\"']?\s*[=:]\s*[\"']?[^\s,;\"']+", "[redacted]", text)
    text = re.sub(r"(?i)\b(?:account|organisation|organization|org|tenant|user)[ _-]*id\s+"
                  r"[\"']?[^\s,;\"']+", "[redacted]", text)
    text = re.sub(r"(?i)\b(?:bearer\s+|(?:access[ _-]*token|refresh[ _-]*token|token|api[ _-]*key)"
                  r"[\"']?\s*[=:]\s*[\"']?)[^\s,;\"']+", "[redacted]", text)
    text = re.sub(r"(?i)\b(?:sk[-_]|org[-_]|acct[-_]|account[-_]|tenant[-_])[A-Za-z0-9_-]+\b", "[redacted]", text)
    text = re.sub(r"\b[A-Za-z0-9_./+=-]{24,}\b",
                  lambda match: match.group(0) if match.group(0) in CONCLUSIVE_REASONS else "[redacted]", text)
    text = prefix + " ".join(text.split()).strip(" .;")
    suffix = ("; " + reset) if reset else ""
    budget = max(0, 160 - len(suffix))
    if len(text) > budget:
        text = text[:max(0, budget - 3)].rstrip() + "..."
    return (text + suffix)[:160]


def pending_cooldown(item, *, now=None):
    """Two pending runs may be consecutive; reset hints always bind unattended runs."""
    mark = item["pending_recheck"]
    stamp = item.get("last_failed_at") or mark.get("at")
    now = now or datetime.now(timezone.utc)
    hint = RESET_HINT.search(str(item.get("verdict_reason", "")))
    if hint:
        try:
            at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError("timezone required")
            seconds = sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit.lower()]
                          for n, unit in re.findall(r"(\d+)\s*([dhms])", hint.group(1), re.IGNORECASE))
            until = at + timedelta(seconds=seconds)
            if now < until:
                return True, until.isoformat()
        except (AttributeError, TypeError, ValueError, OverflowError):
            return True, "invalid failure timestamp"
    runs = mark.get("consecutive_runs")
    if type(runs) is not int or runs < 1:
        return True, "invalid pending run count"
    return cooldown(stamp, now=now) if runs >= 2 else (False, None)


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
        for stream in (stderr, stdout):
            reason = f"result-error: {stream[:300]}"
            if environment_reason(reason):
                return verdict[0], reason, verdict[2], verdict[3]
    return verdict


def classified_verdict(verdict, descriptor, stdout, stderr, vendor, extra):
    """Only tighten the pinned engine's result; model evidence outranks stderr."""
    requested = probe._requested_model(descriptor.get("command", []))
    if vendor in ("gemini", "agy", "codex"):
        record = probe._client_record(descriptor, extra.get(
            "rollout" if vendor == "codex" else "cli_log", ""), "model-record")
        if record[0] == "fail":
            return record
    elif vendor == "grok":
        try:
            response = json.loads(stdout)
        except (ValueError, TypeError):
            response = None
        usage = response.get("modelUsage") if isinstance(response, dict) else None
        models = sorted(usage) if isinstance(usage, dict) else []
        if models and models not in ([requested], [f"{requested}-build"]):
            return "fail", "model-mismatch", ", ".join(models), probe.TIER_SELECTOR
    if vendor == "codex" and verdict[0] == "fail":
        event = probe.codex_failure(stdout)
        if re.search(r"model.*(?:not supported|unsupported|not available|does not exist)|"
                     r"(?:account|plan).*not.*(?:support|access).*model", event, re.IGNORECASE):
            return "fail", "model-account-mismatch: " + verdict[1], verdict[2], verdict[3]
    minimum = descriptor.get("required_evidence_tier")
    if verdict[0] == "pass" and minimum in TIER_RANK and TIER_RANK[verdict[3]] < TIER_RANK[minimum]:
        return "fail", "evidence-tier-regression", verdict[2], verdict[3]
    return contextual_failure(verdict, descriptor, stdout, stderr)


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
    verdict = classified_verdict(verdict, descriptor, streams["stdout"], streams["stderr"], vendor, extra)
    if descriptor.get("verdict") != verdict[0] and not (legacy and "verdict" not in descriptor):
        raise ValueError("failed probe verdict disagrees with raw evidence")
    if "verdict" not in descriptor:
        descriptor = dict(descriptor, **dict(zip(
            ("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict)))
    return descriptor, verdict


def pinned_failures(overlay, *, invalidated=None):
    """Validate negative evidence with the same manifest anchor as passing proof.

    None denotes a genuinely old manifest with no failure-metadata contract.
    The version and nested file digests seal timestamp presence as well as value.
    """
    import aa_policy
    from evidence_integrity import damaged_files
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
        for cid, files in protected.items():
            damaged = damaged_files(files)
            if damaged:
                aa_policy._check(invalidated is not None,
                                 "failed probe evidence changed; timestamp integrity cannot be verified: "
                                 + ", ".join(damaged))
                invalidated[cid] = damaged
    return protected


def failed_probes(overlay: dict, vendor: str, *, invalidated=None) -> dict[str, dict]:
    """Cross-check retry hints against the original failed request and response."""
    import probe_sweep
    from evidence_integrity import FAILED_INVALIDATED_REASON
    entries = {entry["config_id"]: entry for entry in probe_sweep.plan(vendor)}
    mapped = {mapping.get("config_id") for mapping in overlay.get("mappings", [])}
    seen, failures = set(), {}
    manifests = [Path(e["path"]) for e in overlay.get("evidence", [])
                 if "vendor" not in e and Path(e["path"]).name == "probe-manifest.json"]
    damaged = {} if invalidated is not None else None
    protected = pinned_failures(overlay, invalidated=damaged)
    if damaged and not set(damaged) <= set(invalidated):
        raise ValueError("failed probe evidence changed after validation")
    for item in overlay.get("unproven", []):
        cid = item.get("config_id")
        if cid not in entries:
            continue
        if cid in seen or cid in mapped:
            raise ValueError("duplicate or contradictory failed probe row")
        seen.add(cid)
        if (cid in (invalidated or ()) and cid in (protected or {})) \
                or item.get("verdict_reason") == FAILED_INVALIDATED_REASON:
            # Lost timestamps cannot qualify for unattended retry, including
            # after another vendor's repair has retired the raw failure files.
            failures[cid] = dict(reason=FAILED_INVALIDATED_REASON,
                                 last_failed_at="invalid failure timestamp", evidence_invalidated=True)
            continue
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


@contextlib.contextmanager
def advisor_disabled(vendor: str):
    """Keep claude's server-side advisor out of a probe.

    A user setting can enable it; the probed model then sometimes consults it, the
    advisor model is billed as well, and modelUsage no longer names the requested
    model alone (claude 2.1.304, 2026-10-20). The CLI checks this switch before the
    user's opt-in and the server-side flag. It is set here rather than in probe.py
    because probe.py and probe_sweep.py are hashed into every proof's engine
    contract: editing them would invalidate every verified mapping on every host.
    """
    if vendor != "claude":
        yield
        return
    previous = os.environ.get(ADVISOR_KILL_SWITCH)
    os.environ[ADVISOR_KILL_SWITCH] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(ADVISOR_KILL_SWITCH, None)
        else:
            os.environ[ADVISOR_KILL_SWITCH] = previous


# Official Grok settings reference: these disable only compatibility imports,
# not Grok-native/plugin hooks or MCP. Do not claim complete isolation.
# https://docs.x.ai/build/settings/reference#cursor--claude-compatibility-scanners
GROK_COMPAT_SWITCHES = (
    "GROK_CURSOR_MCPS_ENABLED", "GROK_CURSOR_HOOKS_ENABLED",
    "GROK_CLAUDE_MCPS_ENABLED", "GROK_CLAUDE_HOOKS_ENABLED",
)


# Deliberately no credential, endpoint, proxy, loader or config-override variables.
# USER/LOGNAME let CLIs locate keychain login by account name; they are not credentials.
# HOME retains the existing on-disk identity; this is not a filesystem sandbox.
PROBE_ENV_KEYS = ("HOME", "PATH", "TMPDIR", "USER", "LOGNAME")
PREPARATION_TIMEOUT = 30
_ENVIRONMENT_LOCK = threading.RLock()


def child_environment(vendor=None):
    environment = {key: os.environ[key] for key in PROBE_ENV_KEYS if key in os.environ}
    # Fixed values, never inherited user locale overrides.
    environment.update(LANG="C", LC_ALL="C")
    if vendor == "claude":
        environment[ADVISOR_KILL_SWITCH] = "1"
    if vendor == "grok":
        environment.update({key: "0" for key in GROK_COMPAT_SWITCHES})
    return environment


@contextlib.contextmanager
def probe_environment(vendor: str):
    """Let the pinned probe copy only an explicit allowlist, restoring on exit.

    This CLI wrapper is synchronous. The lock serializes cooperating wrapper
    calls; unrelated threads must not spawn children while this scope is active.
    """
    with _ENVIRONMENT_LOCK:
        previous = dict(os.environ)
        environment = child_environment(vendor)
        try:
            os.environ.clear()
            os.environ.update(environment)
            yield
        finally:
            os.environ.clear()
            os.environ.update(previous)


def prepare_agy(argv, **_):
    """Bound Python preparation, with no terminal input or inherited Python knobs."""
    command = list(argv)
    if not command or command[0] != sys.executable:
        raise ValueError("preparation must use this Python interpreter")
    command.insert(1, "-I")
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, env=child_environment(),
                          start_new_session=(os.name == "posix")) as process:
        try:
            stdout, stderr = process.communicate(timeout=PREPARATION_TIMEOUT)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            # Do not wait for pipes held by a descendant that escaped the group.
            process.stdout.close()
            process.stderr.close()
            process.wait()
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@contextlib.contextmanager
def controlled_preparation(vendor):
    """Adapt only the pinned sweep's preparation dependency, not its code/argv.

    The engine has no preparation callback. Its subprocess.run is used solely
    for prepare-agy-mode. A local facade keeps the standard subprocess module
    unchanged and restores the dependency even when preparation raises.
    """
    import probe_sweep
    if vendor != "gemini":
        yield
        return
    with _ENVIRONMENT_LOCK:
        original = probe_sweep.subprocess
        probe_sweep.subprocess = SimpleNamespace(run=prepare_agy)
        try:
            yield
        finally:
            probe_sweep.subprocess = original


SAFE_PREFLIGHT_REASONS = frozenset((
    "timeout", "model-mismatch", "client-record-mismatch", "effort-silently-defaulted",
    "model-account-mismatch", "evidence-tier-regression", "rate-limited",
    "quota-exhausted", "not-authenticated", "connection-error", "api-error",
    "missing-expected-token", "invalid-json", "invalid-json-result",
))


def engine_view(vendor: str, record: dict) -> dict:
    """What the pinned engine may read of a failed codex probe: codex's final error, if any.

    A codex run also connects every MCP server its plugins declare, and those log
    their own 401s to stderr. The engine reads stderr, and verdict_reason's
    stderr-review repeats it, so an unrelated failure would read as "not logged in".
    This lives here because probe.py and probe_sweep.py are hashed into every proof.
    """
    if vendor != "codex" or record.get("verdict") == "pass":
        return record
    reason, _, review = str(record.get("verdict_reason") or "").partition("; stderr-review: ")
    if "; codex-event:" not in reason:
        # With no final event stderr is all there is; codex can print a login error there and exit.
        kept = [line for line in review.split(" | ") if line and "mcp" not in line.lower()]
        if kept:
            reason += "; " + " | ".join(kept)
    view = {key: value for key, value in record.items() if key not in ("stdout", "stderr")}
    view["verdict_reason"] = reason
    return view


def not_authenticated(vendor: str, record: dict) -> bool:
    import probe_sweep
    return bool(probe_sweep.NOT_AUTHENTICATED.search(probe_sweep._text(engine_view(vendor, record))))


@contextlib.contextmanager
def any_session_allowed():
    """Bypass only the session heuristic, after a separate passing preflight."""
    key = "OMNILANE_PROBE_ANY_SESSION"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def session_preflight(vendor: str, root: Path, entry: dict) -> str | None:
    """One unchanged probe request, separate from installable sweep evidence.

    No retries here: every failure, including preparation/launch errors, leaves
    the non-Aqua session unprobeable. A pass says nothing about any other row.
    """
    import probe_sweep
    preflight = root / "session-preflight"
    work = preflight / "work"
    try:
        work.mkdir(parents=True, exist_ok=True)
        app_root = app_data = None
        if vendor == "gemini":
            home = Path.home()
            app_root = home / ".omnilane" / "agy-app" / f"preflight-{root.name}-{entry['name']}"
            prepared = prepare_agy(
                [sys.executable, str(probe_sweep.build_overlay.REPO / "scripts/lib/prepare-agy-mode.py"),
                 "--mode", "advise", "--workdir", str(work), "--app-root", str(app_root),
                 "--gemini-dir", str(home / ".gemini")], capture_output=True, text=True)
            if prepared.returncode != 0:
                return "preparation-failed"
            app_data = prepared.stdout.strip()
        argv = probe_sweep.command(vendor, entry["model"], entry["effort"], app_data)
        with probe_environment(vendor):
            record = probe_sweep.probe_module.probe(
                entry["name"], argv, root=preflight,
                vendor="agy" if vendor == "gemini" else vendor,
                expected_token=probe_sweep.TOKENS[vendor], app_root=app_root)
        if record.get("verdict") == "pass":
            return None
        reason = str(record.get("verdict_reason") or "").split(":", 1)[0]
        return reason if reason in SAFE_PREFLIGHT_REASONS else "probe-failed"
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError:
        return "launch-failed"
    except Exception:
        # Do not expose exception text: it may include provider/user data.
        return "probe-failed"


def run_sweep(vendor: str, root: Path, *, log=print, only_missing=False,
              retry_failed=(), refresh_expired=(), skip_configs=(), retry_now=False,
              per_selector=False, refresh_invalidated=()) -> dict:
    """Test actual reachability before rejecting a non-Aqua session heuristic."""
    import probe_sweep
    options = dict(log=log, only_missing=only_missing, retry_failed=retry_failed,
                   refresh_expired=refresh_expired, skip_configs=skip_configs, retry_now=retry_now,
                   per_selector=per_selector, refresh_invalidated=refresh_invalidated)
    if (vendor in probe_sweep.KEYCHAIN_VENDORS and sys.platform == "darwin"
            and os.environ.get("OMNILANE_PROBE_ANY_SESSION") != "1"):
        manager = probe_sweep.session_manager()
        if manager and manager != "Aqua":
            entries = [entry for entry in probe_sweep.plan(vendor)
                       if entry["config_id"] not in skip_configs and
                       (not only_missing or entry["config_id"] in (*retry_failed, *refresh_expired, *refresh_invalidated)
                        or not (root / "evidence" / f"{entry['name']}.json").is_file())]
            if entries:
                # Same cheapest-row convention as resign.smoke: registry score,
                # then stable ID. The registry does not contain monetary prices.
                entry = min(entries, key=lambda e: (
                    probe_sweep.build_overlay.ROWS[e["config_id"]]["score"], e["config_id"]))
                failure = session_preflight(vendor, root, entry)
                if failure is not None:
                    return dict(vendor=vendor, outcome="unprobeable", passed=[], failed=[],
                                detail=(f"{vendor} session preflight failed ({failure}); "
                                        "a non-Aqua session or unavailable login keychain is one "
                                        "possible cause. Check CLI login/access, then re-run; "
                                        "an Aqua (GUI) login may help."))
                with any_session_allowed():
                    return _run_sweep(vendor, root, **options)
    return _run_sweep(vendor, root, **options)


def _run_sweep(vendor: str, root: Path, *, log=print, only_missing=False,
              retry_failed=(), refresh_expired=(), skip_configs=(), retry_now=False,
              per_selector=False, refresh_invalidated=()) -> dict:
    """Reuse the pinned sweep engine, exposing only eligible staged gaps to it.

    The original live evidence never changes. The pinned engine's handling of a
    first-time login failure is preserved, with its output moved out of the
    evidence to root/unprobeable; retry login failures keep their new negative
    evidence so the next unattended run can respect the cooldown.
    With per_selector, login failures are independent only after a selector in
    this run authenticated; a wholly unauthenticated full sweep still stops once.
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
    originals, skipped, captured, answered_again = {}, {}, {}, set()
    authenticated = False
    for cid in (*retry_failed, *refresh_expired, *refresh_invalidated):
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

    def real_probe(name, argv, **kwargs):
        cid = by_name[name]["config_id"]
        if cid in captured and not retry_now:
            previous = json.loads(captured[cid]["json"])
            if previous.get("verdict") == "fail" and environment_reason(previous.get("verdict_reason")):
                log(f"{name}: environment probe already failed; unattended cooldown prevents another call")
                return previous
        with probe_environment(vendor):
            record = probe_sweep.probe_module.probe(name, argv, **kwargs)
        # Claude sometimes answers with text injected into the session (a hook's
        # context, an MCP server's instructions) instead of the token, with the
        # right model and exit code. Ask once more; any other failure is a finding.
        if (vendor == "claude" and cid not in answered_again and record.get("verdict") == "fail"
                and record.get("verdict_reason") == "missing-expected-token"):
            answered_again.add(cid)
            kept = root / "retried"
            kept.mkdir(parents=True, exist_ok=True)
            for suffix in suffixes:
                path = root / "evidence" / f"{name}.{suffix}"
                if path.is_file():
                    path.replace(kept / f"{name}.first-attempt.{suffix}")
            log(f"{name}: answered without the token; first answer kept in {kept}, asking once more")
            with probe_environment(vendor):
                record = probe_sweep.probe_module.probe(name, argv, **kwargs)
            log(f"{name}: passed on retry" if record.get("verdict") == "pass"
                else f"{name}: failed twice ({str(record.get('verdict_reason'))[:80]})")
        if record.get("verdict") == "fail":
            streams = [(root / "evidence" / f"{name}.{suffix}").read_text(errors="replace")
                       for suffix in ("stdout", "stderr")]
            extra = {suffix: path.read_text(errors="replace") for suffix in ("rollout", "cli_log")
                     if (path := root / "evidence" / f"{name}.{suffix}").is_file()}
            verdict = classified_verdict((record["verdict"], record.get("verdict_reason"),
                                          record.get("observed_model"), record.get("evidence_tier")),
                                         record, *streams, vendor, extra)
            record.update(zip(("verdict", "verdict_reason", "observed_model", "evidence_tier"), verdict))
            record["last_failed_at"] = datetime.now(timezone.utc).isoformat()
            (root / "evidence" / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
        captured[cid] = snapshot(name)
        return record

    def run_probe(name, argv, **kwargs):
        nonlocal authenticated
        if by_name[name]["config_id"] in skipped:
            return {"verdict": "fail", "verdict_reason": "deferred-by-cooldown"}
        record = real_probe(name, argv, **kwargs)
        if record.get("verdict") == "fail" and str(record.get("verdict_reason", "")).split(":", 1)[0] in CONCLUSIVE_REASONS:
            # The engine also searches raw streams for login/transient phrases.
            # A model mismatch must not acquire those classifications from noise.
            return {"verdict": "fail", "verdict_reason": record["verdict_reason"].split(":", 1)[0]}
        login = not_authenticated(vendor, record)
        if not login and (record.get("verdict") == "pass" or record.get("observed_model")):
            authenticated = True
        if per_selector and authenticated and record.get("verdict") == "fail" \
                and environment_reason(record.get("verdict_reason")) and login:
            # A login failure can belong to one model's backend. Keep its real
            # negative proof and let the pinned engine probe the other selectors.
            return {"verdict": "fail", "verdict_reason": "not-authenticated"}
        if login and record.get("verdict") == "fail":
            # Preserve the engine's login stop without letting a secondary
            # timeout/503 diagnostic trigger its transient retry first.
            return {"verdict": "fail", "verdict_reason": "not logged in"}
        return engine_view(vendor, record)

    try:
        with controlled_preparation(vendor):
            result = probe_sweep.sweep(vendor, root, log=log, only_missing=only_missing, run_probe=run_probe)
        if (per_selector and result.get("detail") == "no selector of this vendor passed"
                and result["failed"] and all(cid in captured and environment_reason(
                    json.loads(captured[cid]["json"]).get("verdict_reason")) for cid in result["failed"])):
            # Resign decides whether complete negative results can be retained;
            # no passing mapping or dispatch authority is synthesized here.
            result["outcome"] = "done"
        # The original engine deletes descriptors for unauthenticated probes.
        # Restore only completed negative retry records; never synthesize a pass.
        retry_login = False
        retained = set(captured) if only_missing else set(retry_failed) & set(captured)
        for cid in retained:
            saved = captured[cid]
            record = json.loads(saved["json"])
            if record.get("verdict") == "fail" and environment_reason(record.get("verdict_reason")):
                restore(entries[cid]["name"], saved)
                if not_authenticated(vendor, record):
                    retry_login = True
                    if cid not in result["failed"]:
                        result["failed"].append(cid)
        if only_missing and retry_login and result["outcome"] == "unprobeable":
            result["outcome"] = "done"
        if result["outcome"] == "unprobeable":
            for cid, saved in captured.items():
                name = entries[cid]["name"]
                if not (root / "evidence" / f"{name}.json").exists():
                    kept = root / "unprobeable"
                    kept.mkdir(parents=True, exist_ok=True)
                    for suffix, content in saved.items():
                        (kept / f"{name}.unprobeable.{suffix}").write_bytes(content)
                    log(f"omnilane: {vendor}: kept this run's output for {cid} in {kept}")
        return result
    finally:
        for cid, saved in originals.items():
            name = entries[cid]["name"]
            if cid not in captured and not (root / "evidence" / f"{name}.json").exists():
                restore(name, saved)
        for cid, saved in skipped.items():
            restore(entries[cid]["name"], saved)
