"""Local daily-verification outcomes. Informational only, never dispatch authority."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import sys

VENDORS = ("claude", "codex", "gemini", "grok")
REASONS = {
    0: "verified", 2: "not-configured-or-invalid-arguments", 10: "verification-needed",
    20: "operator-or-login-needed", 30: "verification-failed-settings-kept",
    40: "host-configuration-unavailable", 50: "verification-busy",
    60: "partially-re-signed",
}


def transport_guidance(vendor, config_id=None, candidates=(), environment_failures=(), conclusive_failures=()):
    safe_vendor = vendor if vendor in VENDORS else None
    command = "omnilane resign" + (" --vendor " + safe_vendor if safe_vendor else " --check")
    rows = config_id or ", ".join(candidates) or "requested model/effort"
    if any(cid in conclusive_failures for cid in ([config_id] if config_id else candidates)):
        from probe_failure import public_reason
        details = ("; ".join(public_reason(conclusive_failures[cid])
                             for cid in ([config_id] if config_id else candidates) if cid in conclusive_failures)
                   if isinstance(conclusive_failures, dict) else "model/effort mismatch")
        return dict(next_command="omnilane doctor", requires_gui_session=False,
                    message=f"{vendor}: {rows} has a deterministic probe finding: {details}. "
                            "It will not retry automatically. Run `omnilane doctor`, inspect the "
                            "recorded request and CLI/provider model routing, and correct the cause "
                            "before explicitly arranging a fresh verification.",
                    message_zh_tw=f"{vendor} 的 {rows} 已確認有探測問題，不會自動重試。"
                                  f"紀錄原因：{details}。"
                                  "請執行 `omnilane doctor`，核對原始請求、工具與供應端的模型對應；"
                                  "查明並修正原因後，再明確安排重新驗證。")
    failed = any(cid in environment_failures for cid in ([config_id] if config_id else candidates))
    if failed and safe_vendor:
        command = f"omnilane resign --retry-failed --vendor {safe_vendor}"
    gui = safe_vendor in ("claude", "gemini", "grok") and sys.platform == "darwin"
    session = (" On this Mac, use a desktop terminal signed in to this CLI; "
               "SSH alone may not have its login." if gui else " Use a terminal with this CLI signed in.")
    if failed:
        return dict(next_command=command, requires_gui_session=gui,
                    message=f"{vendor}: {rows} last probe failed for an environment reason. "
                            f"Run `{command}` to retry.{session} This does not approve a signer change.",
                    message_zh_tw=f"{vendor} 的 {rows} 上次探測因環境原因失敗，請執行 `{command}` 重試。"
                                  + ("請在這台 Mac 的圖形桌面終端機確認工具已登入。" if gui else "")
                                  + "這個旗標不會放寬簽署者檢查。")
    return dict(next_command=command, requires_gui_session=gui,
                message=f"{vendor}: {rows} is not verified for the current executable and request "
                        f"parameters. Run `{command}`.{session} No approval flag is needed for login errors.",
                message_zh_tw=f"{vendor} 的 {rows} 尚未驗證目前的執行檔與呼叫參數。請執行 `{command}`。"
                              + ("請在這台 Mac 的圖形桌面終端機確認工具已登入後重試。" if gui else
                                 "請在已登入該工具的終端機重試。")
                              + "登入失敗不需要加 --approve。")


def team_wide_notice(vendor, team):
    """An approval with no product identifier passes any program from that team and location."""
    return (f"{vendor}: An approval for team {team} with no product identifier covers every program "
            "that team signs at this location, not only this CLI. To bind it to the product, run "
            f"omnilane resign --trust-origin {vendor} without --team while a verified factory sample "
            f"is installed. / {vendor}：團隊 {team} 的核准沒有綁定產品識別名稱，會涵蓋該團隊在這個位置簽署的"
            "所有程式，不只這個 CLI。要綁定到產品，請在安裝有效原廠樣本時，不加 --team 再執行一次 "
            f"omnilane resign --trust-origin {vendor}。")


def team_wide_notices(vendor, channels):
    return [team_wide_notice(vendor, channel["team_id"]) for channel in channels
            if channel.get("kind") == "developer-id" and channel.get("identifier_family") == ""]


def origin_guidance(vendor, reason, current=None):
    """Source failures never suggest login or treat approval as signature repair."""
    vendor = vendor if vendor in VENDORS else "VENDOR"
    current = current or {}
    kind = current.get("kind")
    descriptions = {
        "origin-unapproved": ("This executable kind has no approved source channel", "這種執行檔尚未核准來源通道"),
        "team-changed": ("The signing team changed", "簽署團隊變了"),
        "identifier-changed": ("The signed product identifier changed", "簽章識別名稱變了"),
        "location-changed": ("The resolved installation location changed", "解析後的安裝位置變了"),
        "invalid": ("The signature or Apple Developer ID requirement failed", "簽章或 Apple 發行者要求查驗失敗"),
        "unsigned": ("This macOS native executable has no signature", "這個 macOS 原生執行檔完全沒有簽章"),
        "unverifiable": ("The executable signature could not be classified or checked", "無法分類或查驗執行檔簽章"),
        "policy-invalid": ("The local origin policy is damaged, unsafe, or belongs to another host", "本機通道檔損壞、不安全或屬於別台主機"),
    }
    en, zh = descriptions.get(reason, descriptions["unverifiable"])
    # Diagnostic detail never participates in origin-policy trust decisions.
    if reason == "invalid":
        if current.get("invalid_reason") == "integrity":
            en, zh = "The signature integrity check failed", "簽章完整性查驗失敗"
        elif current.get("invalid_reason") == "requirement":
            en, zh = "The Apple Developer ID certificate requirement failed", "Apple 發行者憑證鏈要求查驗失敗"
    command = None
    if reason not in ("invalid", "unsigned", "unverifiable", "policy-invalid"):
        if kind == "developer-id":
            command = f"omnilane resign --trust-origin {vendor}"
        elif reason == "location-changed" or kind in ("script", "no-platform-signature"):
            command = f"omnilane resign --approve {vendor}"
        elif kind == "adhoc":
            command = f"omnilane resign --trust-adhoc {vendor}"
    observed = {key: current.get(key) for key in ("signer", "identifier")
                if isinstance(current.get(key), str)}
    if isinstance(current.get("observed_team"), str):
        observed["signer"] = current["observed_team"]
    detail = json.dumps(observed, ensure_ascii=True)
    message = f"{vendor}: {en}. This attempt did not execute the CLI. Observed: {detail}. "
    message_zh = f"{vendor}：{zh}。這次沒有執行該執行檔。觀察值：{detail}。"
    if reason == "identifier-changed" and isinstance(current.get("identifier"), str):
        import cli_provenance
        if cli_provenance.identifier_kept_whole(current["identifier"]):
            message += "Its version annotation could also name a different program, so each new identifier needs its own approval. "
            message_zh += "它的版本附註也可能是另一支程式的名稱，所以每個新的識別名稱都要各自核准。"
    if command:
        message += f"After reviewing the source, run: {command}. Then run omnilane resign --vendor {vendor}."
        message_zh += f"查明來源後執行：{command}；再執行 omnilane resign --vendor {vendor}。"
        if kind in ("script", "no-platform-signature"):
            message += f" For future updates at that pinned location: omnilane resign --trust-location {vendor}."
            message_zh += f"若要同位置後續更新自動補測：omnilane resign --trust-location {vendor}。"
    else:
        message += "No approval command repairs this. Restore a valid installation or this host's policy backup, or repair signature checking, then retry omnilane resign."
        message_zh += "核准指令無法修好這個問題。請還原有效安裝、本機通道備份，或修復簽章查驗能力後，再執行 omnilane resign。"
    return dict(next_command=command, message=message, message_zh_tw=message_zh)


def status_path():
    return Path(os.environ.get("OMNILANE_HOME") or Path.home() / ".omnilane") / "verification-status.json"


def load():
    import aa_policy
    path = status_path()
    if not path.exists() and not path.is_symlink():
        return {"schema_version": 1, "pending": {}}
    value, _ = aa_policy._read_json(path)
    aa_policy._check(isinstance(value, dict), "invalid local verification status")
    aa_policy._check(type(value.get("schema_version")) is int
                     and value.get("schema_version") == 1 and isinstance(value.get("pending"), dict),
                     "invalid local verification status")
    aa_policy._check(all(isinstance(entry, dict) for entry in value["pending"].values()),
                     "invalid local verification status")
    return value


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _selector_count(value, field="pending_count"):
    from selector_recheck import summary_ids
    if isinstance(value, Mapping) and field in value:
        value = value[field]
    elif field == "verified_count":
        return 0
    elif isinstance(value, Mapping) and "config_ids" in value:
        value = value["config_ids"]
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return len(summary_ids(value))
    return None if value else 0


def reason_class(code, summary, vendor):
    """The same outcome precedence for daily status and end-of-run guidance."""
    deterministic = summary.get("deterministic_failures", {}).get(vendor, {})
    gap = summary.get("still_unprobed", {}).get(vendor, [])
    reason = REASONS.get(code, "verification-interrupted")
    report_value = summary.get("vendors", {}).get(vendor, {})
    report = _mapping(report_value)
    sweep = _mapping(summary.get("sweeps", {}).get(vendor, {}))
    partial = summary.get("partially_re_signed", {}).get(vendor)
    retained = summary.get("selector_pending", {}).get(vendor) or partial
    pending = summary.get("pending_recheck", {}).get(vendor)
    deferred = summary.get("deferred_retries", {}).get(vendor)
    retried = summary.get("failed_retries", {}).get(vendor)
    missing = gap if "still_unprobed" in summary else report.get("unprobed", [])
    failed = (summary["still_failed_environment"].get(vendor, {}) if "still_failed_environment" in summary
              else report.get("failed_environment", {}))
    if gap:
        reason = "verification-needed"
    elif code == 0 and deterministic:
        reason = "deterministic-probe-failure"
    elif code in (20, 60) and partial:
        reason = "partially-re-signed"
    elif code == 60:
        if deterministic:
            reason = "deterministic-probe-failure"
        elif not retained:
            if missing or failed or deferred or retried or pending:
                reason = "verification-needed"
            elif report and not report.get("drifted"):
                return None
            elif not report_value:
                # Another vendor's partial result proves nothing about this
                # vendor's prior gap. Preserve its names and actual reason.
                return ""
            else:
                reason = "verification-needed"
    if sweep.get("outcome") == "unprobeable":
        reason = "needs-login-session"
    elif _mapping(report.get("gate")).get("allowed") is False:
        reason = "review-executable-source" if report.get("cli") else "cli-not-installed"
    elif sweep.get("outcome") not in (None, "done"):
        reason = "provider-verification-incomplete"
    if code == 20 and summary.get("held") and vendor not in summary["held"] \
            and vendor not in summary.get("deferred_retries", {}) \
            and vendor not in summary.get("failed_retries", {}) \
            and vendor not in summary.get("still_failed_environment", {}) and not retained and not gap and not deterministic and not pending:
        return None
    return reason


def attention(code, summary):
    """Actionable public hints, never approvals or additional retry authority."""
    result = []
    for vendor in VENDORS:
        report = _mapping(summary.get("vendors", {}).get(vendor, {}))
        unresolved = any(summary.get(key, {}).get(vendor) for key in (
            "still_unprobed", "still_failed_environment", "deferred_retries", "failed_retries",
            "selector_pending", "partially_re_signed", "pending_recheck", "deterministic_failures"))
        if vendor not in summary.get("held", []) and not unresolved and not (
                code not in (0, 60) and report.get("drifted")):
            continue
        reason = reason_class(code, summary, vendor)
        if not reason:
            continue
        command = f"omnilane resign --vendor {vendor}"
        sweep = _mapping(summary.get("sweeps", {}).get(vendor, {}))
        if reason == "review-executable-source":
            command = origin_guidance(vendor, report.get("origin_reason"), report.get("codesign"))["next_command"] or "omnilane doctor"
        elif reason in ("cli-not-installed", "deterministic-probe-failure") or summary.get("evidence_missing"):
            command = "omnilane doctor"
        elif sweep.get("regressed"):
            if "evidence-tier-regression" in sweep.get("regression_reasons", {}).values():
                command = "omnilane doctor"
            else:
                command += " --allow-shrink"
        elif any(summary.get(key, {}).get(vendor) for key in (
                "still_failed_environment", "deferred_retries", "failed_retries")):
            command = f"omnilane resign --retry-failed --vendor {vendor}"
        if summary.get("initializing") and not summary.get("installed"):
            # Approval/retry actions require an existing overlay. Failed bootstrap
            # must point at setup, not send the operator back to exit 2.
            command = ("omnilane doctor" if reason in ("review-executable-source", "cli-not-installed")
                       else "omnilane resign --init")
        operator_only = any(flag in command for flag in (
            "--approve", "--record-signers", "--trust-adhoc", "--allow-shrink", "--trust-origin"))
        if operator_only:
            command += " (for the human operator to run in a terminal after reviewing the source or loss)"
        elif reason == "needs-login-session":
            command += " (in a desktop terminal signed in to this CLI)"
        elif summary.get("deferred_retries", {}).get(vendor):
            command += " (in an interactive terminal, or wait for the cooldown/reset time)"
        result.append(dict(vendor=vendor, reason=reason, next_command=command))
    return result


def record(code, vendors=None, summary=None):
    """Persist only allowlisted metadata, not CLI output, prompts, or credentials."""
    import aa_policy
    from selector_recheck import summary_ids
    summary = summary or {}
    state = load()
    state.pop("running", None)
    now = datetime.now(timezone.utc).isoformat()
    gaps = summary.get("still_unprobed", {})
    requested = [v for v in VENDORS if v in (vendors or VENDORS) or gaps.get(v)]
    for vendor in requested:
        deterministic = summary.get("deterministic_failures", {}).get(vendor, {})
        gap = gaps.get(vendor, [])
        if code == 0 and not gap and not deterministic:
            state["pending"].pop(vendor, None)
            continue
        reason = REASONS.get(code, "verification-interrupted")
        report_value = summary.get("vendors", {}).get(vendor, {})
        report = _mapping(report_value)
        sweep = _mapping(summary.get("sweeps", {}).get(vendor, {}))
        partial = summary.get("partially_re_signed", {}).get(vendor)
        retained = summary.get("selector_pending", {}).get(vendor) or partial
        pending = summary.get("pending_recheck", {}).get(vendor)
        deferred = summary.get("deferred_retries", {}).get(vendor)
        retried = summary.get("failed_retries", {}).get(vendor)
        missing = gap if "still_unprobed" in summary else report.get("unprobed", [])
        failed = (summary["still_failed_environment"].get(vendor, {}) if "still_failed_environment" in summary
                  else report.get("failed_environment", {}))
        reason = reason_class(code, summary, vendor)
        if reason is None:
            state["pending"].pop(vendor, None)
            continue
        if reason == "":
            continue
        prior = state["pending"].get(vendor, {})
        same = prior.get("reason") == reason and prior.get("exit_code") == code
        # Row IDs come only from the package registry, never free-form logs.
        import build_overlay
        rows = sorted({cid for value in (missing, failed, gap, deterministic, retained, pending, deferred, retried)
                       for cid in summary_ids(value) if cid in build_overlay.ROWS})
        state["pending"][vendor] = dict(vendor=vendor, reason=reason, config_ids=rows,
                                         exit_code=code, first_seen=prior.get("first_seen", now) if same else now,
                                         last_seen=now, attempts=prior.get("attempts", 0) + 1 if same else 1)
        if retained:
            pending_ids = sorted(cid for cid in summary_ids(retained) if cid in build_overlay.ROWS)
            state["pending"][vendor]["pending_config_ids"] = pending_ids
            for field in ("pending_count", "verified_count"):
                count = _selector_count(retained, field)
                if count is not None:
                    state["pending"][vendor][field] = count
        if isinstance(deterministic, Mapping) and deterministic:
            from probe_failure import public_reason
            state["pending"][vendor]["deterministic_failures"] = {
                cid: public_reason(reason) for cid, reason in deterministic.items() if cid in build_overlay.ROWS}
        if reason == "review-executable-source":
            # Allowlist codes only; signer details are re-observed by --check.
            source_reason = report.get("origin_reason")
            if source_reason in {"origin-unapproved", "team-changed", "identifier-changed", "location-changed",
                                 "invalid", "unsigned", "unverifiable", "policy-invalid"}:
                state["pending"][vendor]["origin_reason"] = source_reason
    state["checked_at"] = now
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    aa_policy.atomic_json(path, state)


def pending_summary():
    state = load()
    messages = []
    if state.get("running"):
        messages.append("Automatic verification started but has no completed result; it may still be "
                        "running or was interrupted. Check `omnilane resign --check` before retrying.")
    for vendor, entry in sorted(state["pending"].items()):
        if vendor not in VENDORS or not isinstance(entry, dict):
            raise ValueError("invalid local verification status")
        reason = entry.get("reason")
        allowed = set(REASONS.values()) | {"verification-interrupted", "needs-login-session",
                    "review-executable-source", "cli-not-installed", "provider-verification-incomplete",
                    "deterministic-probe-failure"}
        if reason not in allowed:
            raise ValueError("invalid local verification status reason")
        if reason == "deterministic-probe-failure":
            action = "inspect the recorded finding with `omnilane doctor`; no automatic retry"
        elif reason == "verification-busy":
            action = "wait for the running verification, then retry"
        elif reason == "review-executable-source":
            action = ("Source review required; this attempt did not execute the CLI. "
                      "Run `omnilane resign --check` to inspect the executable: observed kind, team, identifier and source-specific next step. "
                      "來源待查，這次沒有執行該執行檔；請執行 `omnilane resign --check` 查看種類、團隊與後續指令。")
        elif reason == "cli-not-installed":
            action = "install this vendor's CLI, then run `omnilane resign --vendor " + vendor + "`"
        elif reason in ("host-configuration-unavailable", "not-configured-or-invalid-arguments"):
            action = "check local.sh and the overlay path with `omnilane doctor`"
        else:
            action = transport_guidance(vendor)["message"]
        selectors = (f"; {entry.get('pending_count', len(entry.get('config_ids', [])))} selector(s) pending: "
                     + ", ".join(entry.get("pending_config_ids", entry.get("config_ids", [])))) if "pending_count" in entry else ""
        from probe_failure import public_reason
        findings = "; ".join(f"{cid}: {public_reason(value)}"
                             for cid, value in entry.get("deterministic_failures", {}).items())
        messages.append(f"{vendor}: {reason}{selectors}" + (f"; {findings}" if findings else "") + f"; next: {action}")
    return "; ".join(messages)


def run_daily(action, vendors=None, summary=None, log=None):
    log = log or (lambda message: print(message, file=sys.stderr))
    # Leave evidence before starting, so even SIGKILL cannot turn an unfinished
    # job into a silent success. This record cannot authorize a model dispatch.
    try:
        import aa_policy
        state = load()
        state["running"] = {"started_at": datetime.now(timezone.utc).isoformat()}
        path = status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        aa_policy.atomic_json(path, state)
    except (OSError, ValueError, TypeError, KeyError):
        log("omnilane: automatic verification status could not be saved; check OMNILANE_HOME permissions.")
        return 40
    try:
        code = action()
    except KeyboardInterrupt:
        code = 130
    except (OSError, ValueError, RuntimeError):
        code = 40
    try:
        result_summary = summary() if callable(summary) else summary
        record(code, vendors, result_summary)
    except (OSError, ValueError, TypeError, KeyError):
        log("omnilane: automatic verification status could not be saved; check OMNILANE_HOME permissions. "
            "Verification is not reported as successful.")
        return code or 40
    if code == 60:
        partial = (result_summary or {}).get("partially_re_signed", {})
        def total(field):
            counts = [_selector_count(value, field) for value in partial.values()]
            return "unknown" if None in counts else sum(counts)
        verified, pending = total("verified_count"), total("pending_count")
        log(f"omnilane: automatic verification partially completed: {verified} verified, {pending} pending. "
            "Run `omnilane resign` in an interactive terminal to retry, or `omnilane doctor` for details.")
    elif code:
        log("omnilane: automatic verification did not complete; the unresolved status is saved locally. "
            "Run `omnilane doctor` for the vendor and next step.")
    return code


if __name__ == "__main__":
    try:
        print(pending_summary())
    except (OSError, ValueError, TypeError, KeyError):
        print("Local verification status cannot be read; check OMNILANE_HOME and its permissions.")
        raise SystemExit(1)
