#!/usr/bin/env python3
"""Create one private per-session Agy settings root and print its CLI path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

POLICY_MARKER = ".omnilane-policy-root-v1.json"


def policy(mode: str, workdir: str, app_root: str | None = None) -> dict[str, object]:
    read = f"read_file({workdir})"
    write = f"write_file({workdir})"
    if mode == "advise":
        allow = [read, "read_url(*)"]
        deny = ["write_file(*)", "command(*)", "execute_url(*)", "mcp(*)", "unsandboxed(*)"]
        permission = "proceed-in-sandbox"
        terminal_sandbox = True
        outside = False
    elif mode == "work":
        allow = [read, write, "command(*)"]
        # Agy's command(*) denial matching also covers unsandboxed(*), so a
        # blanket unsandboxed deny blocks ordinary sandboxed commands. Native
        # proceed-in-sandbox asks for bypass separately and headless denies it.
        deny = ["read_url(*)", "execute_url(*)", "mcp(*)"]
        roots = [
            "/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp",
            "/var/folders", "/private/var/folders", "/Library/Caches",
            str(Path.home() / "Library/Caches"), str(Path.home() / ".cache"),
            str(Path.home() / ".npm"), str(Path(workdir) / ".agents"),
        ]
        if app_root is not None:
            roots.append(app_root)
        # read_file denial is required for native default cache mounts;
        # write_file denial alone did not remove their shell write access.
        deny += [f"{action}({root})" for root in dict.fromkeys(roots)
                 for action in ("read_file", "write_file")]
        permission = "proceed-in-sandbox"
        terminal_sandbox = True
        outside = False
    elif mode == "sysops":
        allow = [
            "read_file(*)", "write_file(*)", "command(*)", "read_url(*)",
            "execute_url(*)", "mcp(*)", "unsandboxed(*)",
        ]
        deny = []
        permission = "always-proceed"
        terminal_sandbox = False
        outside = True
    else:
        raise ValueError(f"unsupported agy mode: {mode}")
    return {
        "toolPermission": permission,
        "enableTerminalSandbox": terminal_sandbox,
        "allowNonWorkspaceAccess": outside,
        "permissions": {"allow": allow, "deny": deny, "ask": []},
    }


def load_json(path: Path, source: str) -> dict[str, object]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"{source} permissions source is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source} permissions source is unreadable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{source} permissions source has an unknown schema")
    return value


def permission_allow_list(value: object, source: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        raise ValueError(f"{source} permissions have an unknown schema")
    allow = value.get("allow", [])
    deny = value.get("deny", [])
    ask = value.get("ask", [])
    if not all(isinstance(items, list) and all(isinstance(item, str) for item in items)
               for items in (allow, deny, ask)):
        raise ValueError(f"{source} permissions have an unknown schema")
    return allow


def rule_broadens_work(rule: str, workdir: Path) -> str | None:
    match = re.fullmatch(r"([a-z_]+)\((.*)\)", rule)
    if match is None:
        return "unknown allow-rule schema"
    action, target = match.groups()
    if action in {"read_url", "execute_url", "mcp", "unsandboxed"}:
        return f"{action} bypasses work network or sandbox policy"
    if action == "write_file":
        if target == "*" or not target:
            return "write_file grant extends beyond workdir"
        candidate = Path(target).expanduser()
        if not candidate.is_absolute():
            candidate = workdir / candidate
        try:
            candidate.resolve(strict=False).relative_to(workdir)
        except ValueError:
            return "write_file grant extends beyond workdir"
        return None
    if action in {"read_file", "command"}:
        return None
    return "unknown allow-rule action may bypass work policy"


def audit_work_permissions(gemini_dir: Path, app_root: Path, workdir: Path) -> None:
    sources: list[tuple[str, list[str]]] = []

    shared = load_json(gemini_dir / "config" / "config.json", "Shared")
    if shared.get("allowNonWorkspaceAccess") is True:
        raise ValueError("Shared permissions allow non-workspace access")
    sources.append(("Shared", permission_allow_list(shared.get("permissions"), "Shared")))

    existing = load_json(app_root / "settings.json", "private app state")
    sources.append(("private app state", permission_allow_list(existing.get("permissions"), "private app state")))

    project_id_path = app_root / "cache" / "default_project_id.txt"
    if project_id_path.exists():
        if not project_id_path.is_file():
            raise ValueError("Project permission selector has an unknown schema")
        project_id = project_id_path.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", project_id):
            raise ValueError("Project permission selector has an unknown schema")
        project = load_json(gemini_dir / "config" / "projects" / f"{project_id}.json", "Project")
        grants = project.get("permissionGrants", {})
        if grants and not isinstance(grants, dict):
            raise ValueError("Project permissions have an unknown schema")
        nested = grants.get("permissionGrants") if isinstance(grants, dict) else None
        sources.append(("Project", permission_allow_list(nested, "Project")))

    for source, rules in sources:
        for rule in rules:
            reason = rule_broadens_work(rule, workdir)
            if reason is not None:
                raise ValueError(f"{source} permission grant conflicts with work mode: {reason}")


def ensure_private_root(path: Path, mode: str, workdir: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"unsafe symlinked agy app root: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"unsafe non-directory agy app root: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    marker = path / POLICY_MARKER
    expected = {"schema_version": 1, "mode": mode, "workdir": str(workdir)}
    if marker.exists():
        if marker.is_symlink() or load_json(marker, "private app marker") != expected:
            raise ValueError("private app state does not match the requested mode and workdir")
    else:
        if any(path.iterdir()):
            raise ValueError("private app state is not an Omnilane-owned empty root")
        marker.write_text(json.dumps(expected, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(marker, 0o600)


def atomic_write_json(path: Path, value: dict[str, object]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("unsafe agy settings path")
    fd, temporary = tempfile.mkstemp(prefix=".settings.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


WORK_TOOLS = (
    "view_file", "write_to_file", "run_command", "finish",
)


def ensure_work_agent(app_root: Path, name: str = "omnilane-work") -> Path:
    """Own and verify a session policy file; filesystem mode is not immutability."""
    directory = app_root / "policy"
    if directory.is_symlink() or directory.resolve() != app_root.resolve() / "policy":
        raise ValueError("unsafe agy agent policy directory")
    if directory.exists() and not directory.is_dir():
        raise ValueError("unsafe agy agent policy directory")
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "agent.md"
    content = (
        f"---\nname: {name}\ndescription: Omnilane local work mode\n"
        "mainAgent: true\nsubagent: false\ninheritCustomizations: false\n"
        "inheritMcp: false\ncommandExecutionPolicy: sandbox\ntools:\n"
        + "".join(f"  - {tool}\n" for tool in WORK_TOOLS)
        + "---\nComplete the requested local workspace task using the listed tools.\n"
        "Use view_file for reading and write_to_file for creating files. For precise "
        "edits, searches, builds and tests use run_command, preserving unrelated "
        "file content. Wait for terminal results and report nonzero exit status.\n"
    )
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("unsafe agy primary agent path")
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError("existing agy primary agent conflicts with work mode")
        os.chmod(path, 0o600)
        return path.resolve()
    fd, temporary = tempfile.mkstemp(prefix=".agent.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        # link is atomic and fails if another process created the target.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_text(encoding="utf-8") != content:
                raise ValueError("existing agy primary agent conflicts with work mode")
    finally:
        os.unlink(temporary)
    return path.resolve()


def checked_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"unsafe agy workspace directory: {path}")
    path.mkdir(mode=0o700, exist_ok=True)


def stage_work_agent(app_root: Path, workdir: Path) -> None:
    name = "omnilane-work-" + hashlib.sha256(str(app_root.resolve()).encode()).hexdigest()[:16]
    agent = ensure_work_agent(app_root, name)
    cache = workdir / ".omnilane-cache"
    checked_directory(cache)
    cache = cache / name
    checked_directory(cache)
    for part in ("tmp", "cache", "clang", "swift"):
        checked_directory(cache / part)
    for parent in (workdir / ".agents", workdir / ".agents/agents"):
        checked_directory(parent)
    leaf = workdir / ".agents/agents" / name
    # This exclusive owned leaf is also the per-session concurrency guard.
    # Never adopt or overwrite a user profile, an active run or stale state.
    try:
        leaf.mkdir(mode=0o700)
        initial_stat = leaf.lstat()
        expected_identity = (initial_stat.st_dev, initial_stat.st_ino)
    except FileExistsError as exc:
        cleaned = load_json(app_root / "workspace-agent-cleaned.json", "cleaned workspace policy")
        if leaf.is_symlink() or not leaf.is_dir():
            raise ValueError("unsafe existing agy workspace policy leaf") from exc
        st = leaf.lstat()
        if (cleaned.get("leaf") != str(leaf) or cleaned.get("app_root") != str(app_root.resolve())
                or cleaned.get("workdir") != str(workdir) or cleaned.get("agent") != name
                or (st.st_dev, st.st_ino) != (cleaned.get("device"), cleaned.get("inode"))
                or any(leaf.iterdir())):
            raise ValueError("agy workspace policy leaf is active or stale; inspect it before reuse") from exc
        expected_identity = (cleaned["device"], cleaned["inode"])
    marker = {"schema_version": 1, "app_root": str(app_root.resolve()),
              "workdir": str(workdir), "agent": name}
    fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != expected_identity:
            raise ValueError("agy workspace policy leaf changed before staging")
        marker_fd = os.open(".omnilane-owned.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=fd)
        with os.fdopen(marker_fd, "w", encoding="utf-8") as handle:
            json.dump(marker, handle)
            handle.write("\n")
        os.symlink(agent, "agent.md", dir_fd=fd)
        current = leaf.lstat()
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise ValueError("agy workspace policy leaf changed during staging")
    finally:
        os.close(fd)
    atomic_write_json(app_root / "workspace-agent.json", {
        **marker, "leaf": str(leaf), "device": st.st_dev, "inode": st.st_ino,
        "policy": str(agent), "policy_sha256": hashlib.sha256(agent.read_bytes()).hexdigest(),
        "cache": str(cache),
    })


def cleanup_work_agent(app_root: Path, workdir: Path) -> None:
    state_path = app_root / "workspace-agent.json"
    if state_path.is_symlink():
        raise ValueError("unsafe agy workspace policy state")
    state = load_json(state_path, "workspace policy")
    if not state:
        return
    name = state.get("agent", "")
    if not isinstance(name, str) or not re.fullmatch(r"omnilane-work-[a-f0-9]{16}", name):
        raise ValueError("invalid agy workspace policy identity")
    leaf = workdir / ".agents/agents" / name
    expected = {"schema_version": 1, "app_root": str(app_root.resolve()),
                "workdir": str(workdir), "agent": name}
    if any(state.get(k) != v for k, v in expected.items()) or state.get("leaf") != str(leaf):
        raise ValueError("agy workspace policy ownership changed")
    for path in (workdir / ".agents", workdir / ".agents/agents", leaf):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("agy workspace policy directory changed")
    # Pin the owned leaf before unlinking. All removal is relative to this FD.
    fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != (state.get("device"), state.get("inode")):
            raise ValueError("agy workspace policy inode changed")
        if set(os.listdir(fd)) != {"agent.md", ".omnilane-owned.json"}:
            raise ValueError("agy workspace policy leaf contents changed")
        marker = leaf / ".omnilane-owned.json"
        agent = leaf / "agent.md"
        policy_path = app_root / "policy/agent.md"
        if (marker.is_symlink() or load_json(marker, "workspace marker") != expected
                or not agent.is_symlink() or os.readlink("agent.md", dir_fd=fd) != str(policy_path.resolve())
                or state.get("policy") != str(policy_path.resolve())
                or hashlib.sha256(policy_path.read_bytes()).hexdigest() != state.get("policy_sha256")):
            raise ValueError("agy workspace policy content changed")
        os.unlink("agent.md", dir_fd=fd)
        os.unlink(".omnilane-owned.json", dir_fd=fd)
        # No path-based rmdir: another process could replace the name after
        # validation. Keep this empty owned directory and reuse its exact inode.
        current = leaf.lstat()
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise ValueError("agy workspace policy leaf changed during cleanup; replacement preserved")
        atomic_write_json(app_root / "workspace-agent-cleaned.json", state)
        state_path.unlink()
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("advise", "work", "sysops"))
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--gemini-dir", required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve(strict=True)
    app_root = Path(args.app_root).expanduser()
    gemini_dir = Path(args.gemini_dir).expanduser().resolve(strict=True)
    if args.cleanup:
        cleanup_work_agent(app_root, workdir)
        return 0
    ensure_private_root(app_root, args.mode, workdir)
    if args.mode == "work":
        audit_work_permissions(gemini_dir, app_root, workdir)
    atomic_write_json(app_root / "settings.json", policy(args.mode, str(workdir), str(app_root.resolve())))
    if args.mode == "work":
        stage_work_agent(app_root, workdir)
    print(os.path.relpath(app_root.resolve(), gemini_dir))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError) as exc:
        import sys
        print(f"omnilane: {exc}", file=sys.stderr)
        raise SystemExit(2)
