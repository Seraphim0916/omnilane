"""Local daily-verification outcomes. Informational only, never dispatch authority."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

VENDORS = ("claude", "codex", "gemini", "grok")
REASONS = {
    0: "verified", 2: "not-configured-or-invalid-arguments", 10: "verification-needed",
    20: "operator-or-login-needed", 30: "verification-failed-settings-kept",
    40: "host-configuration-unavailable", 50: "verification-busy",
}


def transport_guidance(vendor, config_id=None, candidates=(), environment_failures=(), conclusive_failures=()):
    safe_vendor = vendor if vendor in VENDORS else None
    command = "omnilane resign" + (" --vendor " + safe_vendor if safe_vendor else " --check")
    rows = config_id or ", ".join(candidates) or "requested model/effort"
    if any(cid in conclusive_failures for cid in ([config_id] if config_id else candidates)):
        return dict(next_command="omnilane doctor", requires_gui_session=False,
                    message=f"{vendor}: {rows} reached a different model or effort than declared. "
                            "It will not retry automatically. Run `omnilane doctor`, inspect the "
                            "recorded request and CLI/provider model routing, and correct the cause "
                            "before explicitly arranging a fresh verification.",
                    message_zh_tw=f"{vendor} 的 {rows} 實測打到的不是宣稱的模型／強度，不會自動重試。"
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
    aa_policy._check(value.get("schema_version") == 1 and isinstance(value.get("pending"), dict),
                     "invalid local verification status")
    return value


def record(code, vendors=None, summary=None):
    """Persist only allowlisted metadata, not CLI output, prompts, or credentials."""
    import aa_policy
    summary = summary or {}
    state = load()
    state.pop("running", None)
    now = datetime.now(timezone.utc).isoformat()
    requested = [v for v in (vendors or VENDORS) if v in VENDORS]
    for vendor in requested:
        if code == 0:
            state["pending"].pop(vendor, None)
            continue
        reason = REASONS.get(code, "verification-interrupted")
        report = summary.get("vendors", {}).get(vendor, {})
        sweep = summary.get("sweeps", {}).get(vendor, {})
        if sweep.get("outcome") == "unprobeable":
            reason = "needs-login-session"
        elif report.get("gate", {}).get("allowed") is False:
            reason = "review-executable-source" if report.get("cli") else "cli-not-installed"
        elif sweep.get("outcome") not in (None, "done"):
            reason = "provider-verification-incomplete"
        if code == 20 and summary.get("held") and vendor not in summary["held"] \
                and vendor not in summary.get("deferred_retries", {}) \
                and vendor not in summary.get("failed_retries", {}) \
                and vendor not in summary.get("still_failed_environment", {}):
            state["pending"].pop(vendor, None)
            continue
        prior = state["pending"].get(vendor, {})
        same = prior.get("reason") == reason and prior.get("exit_code") == code
        # Row IDs come only from the package registry, never free-form logs.
        import build_overlay
        rows = sorted({cid for cid in list(report.get("unprobed", [])) +
                       list(report.get("failed_environment", {})) if cid in build_overlay.ROWS})
        state["pending"][vendor] = dict(vendor=vendor, reason=reason, config_ids=rows,
                                         exit_code=code, first_seen=prior.get("first_seen", now) if same else now,
                                         last_seen=now, attempts=prior.get("attempts", 0) + 1 if same else 1)
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
                    "review-executable-source", "cli-not-installed", "provider-verification-incomplete"}
        if reason not in allowed:
            raise ValueError("invalid local verification status reason")
        if reason == "verification-busy":
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
        messages.append(f"{vendor}: {reason}; next: {action}")
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
        record(code, vendors, summary() if callable(summary) else summary)
    except (OSError, ValueError, TypeError, KeyError):
        log("omnilane: automatic verification status could not be saved; check OMNILANE_HOME permissions. "
            "Verification is not reported as successful.")
        return code or 40
    if code:
        log("omnilane: automatic verification did not complete; the unresolved status is saved locally. "
            "Run `omnilane doctor` for the vendor and next step.")
    return code


if __name__ == "__main__":
    try:
        print(pending_summary())
    except (OSError, ValueError, TypeError, KeyError):
        print("Local verification status cannot be read; check OMNILANE_HOME and its permissions.")
        raise SystemExit(1)
