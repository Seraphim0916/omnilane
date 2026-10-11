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
            action = "inspect the executable and signer with `omnilane resign --check` before approving it"
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
