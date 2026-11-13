"""Synthetic unit tests for Antigravity desktop caller identity."""
from __future__ import annotations

import copy
import io
import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import offline_env

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import caller_identity  # noqa: E402
import native_context  # noqa: E402
import refusal_evidence  # noqa: E402

REGISTRY_PATH = ROOT / "config/aa-model-policy.json"
AGY_APP = "/Applications/Antigravity.app/Contents/MacOS/Antigravity"
AGY_LS = "/Applications/Antigravity.app/Contents/Resources/bin/language_server"
CONV_UUID = "6b4380e1-1656-40ad-8671-272990f3552f"
OTHER_UUID = "09f3fe96-3a15-48a0-996d-61e5c3e7ec38"
CSRF_SECRET = "csrf-secret-never-leak-778899"
LS_ADDR_SECRET = "127.0.0.1:59999"


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _pb_varint(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(value)


def _pb_fixed64(field: int, value: bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08") -> bytes:
    assert len(value) == 8
    return _varint((field << 3) | 1) + value


def _pb_bytes(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _pb_fixed32(field: int, value: bytes = b"\x00\x00\x80\x3f") -> bytes:
    assert len(value) == 4
    return _varint((field << 3) | 5) + value


def _build_executor_metadata(
    selector: bytes | None = b"gemini-4-carbon-medium",
    *,
    include_aux: bool = True,
    extra_tag28: bytes | None = None,
    extra_f1_with_tag28: bytes | None = None,
    extra_f10_with_tag28: bytes | None = None,
    omit_f10: bool = False,
    omit_f1: bool = False,
) -> bytes:
    parts = [_pb_varint(1, 4), _pb_varint(2, 8), _pb_varint(3, 569)]
    if not omit_f10:
        f1_inner = bytearray()
        if include_aux:
            aux_32 = _pb_bytes(32, b"gemini-3.8-flash-low")
            aux_25 = _pb_bytes(25, aux_32)
            f1_inner.extend(_pb_bytes(13, aux_25))
            f1_inner.extend(_pb_fixed32(14))
            f1_inner.extend(_pb_fixed64(15))
        if selector is not None:
            f1_inner.extend(_pb_bytes(28, selector))
        if extra_tag28 is not None:
            f1_inner.extend(_pb_bytes(28, extra_tag28))

        f10_inner = bytearray()
        if not omit_f1:
            f10_inner.extend(_pb_bytes(1, bytes(f1_inner)))
        if extra_f1_with_tag28 is not None:
            f10_inner.extend(_pb_bytes(1, _pb_bytes(28, extra_f1_with_tag28)))
        if include_aux:
            f10_inner.extend(_pb_bytes(15, _pb_bytes(1, b"gemini-3.5-flash-lite")))
            f10_inner.extend(_pb_fixed32(16))
        parts.append(_pb_bytes(10, bytes(f10_inner)))

    if extra_f10_with_tag28 is not None:
        parts.append(_pb_bytes(10, _pb_bytes(1, _pb_bytes(28, extra_f10_with_tag28))))
    parts.append(_pb_bytes(11, b"tail"))
    return b"".join(parts)


def _write_synthetic_db(agy_dir: Path, cid: str, blobs: list[tuple[int, bytes]]) -> Path:
    conv_dir = agy_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    db_path = conv_dir / f"{cid}.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS executor_metadata")
        conn.execute(
            "CREATE TABLE executor_metadata "
            "(idx integer, data blob, size integer NOT NULL DEFAULT 0, PRIMARY KEY (idx))"
        )
        for idx, blob in blobs:
            conn.execute(
                "INSERT INTO executor_metadata (idx, data, size) VALUES (?, ?, ?)",
                (idx, blob, len(blob)),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _fake_chain(ls_path: str = AGY_LS, app_path: str = AGY_APP, root_pid: int = 100):
    table = {
        root_pid: (90, ["/usr/bin/python3", "caller_identity.py"]),
        90: (80, ["/bin/zsh", "-c", "omnilane whoami"]),
        80: (70, [ls_path]),
        70: (1, [app_path]),
        1: (0, ["/sbin/launchd"]),
    }

    def lookup(pid: int):
        return table.get(pid)

    return lookup


def _pack_procargs2(argv: list[str], env_entries: list[bytes]) -> bytes:
    header = struct.pack("=i", len(argv)) + b"/bin/zsh\x00\x00"
    argv_bytes = b"".join(item.encode() + b"\x00" for item in argv)
    env_bytes = b"".join(entry + b"\x00" for entry in env_entries) + b"\x00"
    return header + argv_bytes + env_bytes


class AntigravityCallerIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry, _ = caller_identity.load_registry(REGISTRY_PATH)

    def _run_read(self, agy_dir: Path, *, conv_id: str = CONV_UUID,
                  child_conv_id: str | None = None,
                  ls_path: str = AGY_LS, app_path: str = AGY_APP,
                  extra_env: dict[str, str] | None = None):
        child_id = conv_id if child_conv_id is None else child_conv_id
        current_env = {
            "ANTIGRAVITY_CONVERSATION_ID": conv_id,
            "ANTIGRAVITY_CSRF_TOKEN": CSRF_SECRET,
            "ANTIGRAVITY_LS_ADDRESS": LS_ADDR_SECRET,
        }
        if extra_env:
            current_env.update(extra_env)

        def env_lookup(pid: int) -> dict[str, str]:
            if pid == 90:
                return {"ANTIGRAVITY_CONVERSATION_ID": child_id} if child_id else {}
            if pid == 100:
                return {"ANTIGRAVITY_CONVERSATION_ID": conv_id}
            return {}

        return caller_identity.read_caller(
            100,
            lookup=_fake_chain(ls_path=ls_path, app_path=app_path),
            environment=env_lookup,
            current_environment=current_env,
            antigravity_dir=agy_dir,
        )

    def test_valid_carbon_hydrogen_argon_and_argon_medium_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            for cid, raw, expected_model, expected_effort in (
                (CONV_UUID, b"gemini-4-carbon-medium", "gemini-4-carbon", "medium"),
                (OTHER_UUID, b"gemini-4-hydrogen-medium", "gemini-4-hydrogen", "medium"),
                ("11111111-2222-3333-4444-555555555555", b"gemini-4-argon-high", "gemini-4-argon", "high"),
            ):
                _write_synthetic_db(agy_dir, cid, [(0, _build_executor_metadata(raw))])
                launcher, selector, source = self._run_read(agy_dir, conv_id=cid)
                self.assertEqual(launcher, 80)
                self.assertEqual(selector, ("gemini", expected_model, expected_effort))
                self.assertIn("antigravity-desktop", source)
                row, reason = caller_identity.resolve(self.registry, *selector)
                self.assertIsNotNone(row, reason)
                self.assertEqual(row["vendor"], "gemini")

            # In 0.62.0 policy table, argon only has high; argon-medium must be cleanly refused.
            argon_med_cid = "22222222-2222-3333-4444-555555555555"
            _write_synthetic_db(agy_dir, argon_med_cid, [(0, _build_executor_metadata(b"gemini-4-argon-medium"))])
            _, selector, _ = self._run_read(agy_dir, conv_id=argon_med_cid)
            row, reason = caller_identity.resolve(self.registry, *selector)
            self.assertIsNone(row)
            self.assertIn(caller_identity.NO_ROW, reason)

            # With a synthetic policy table that includes a measured argon/medium row, it resolves cleanly.
            synthetic_registry = copy.deepcopy(self.registry)
            synthetic_registry["scored_configs"].append({
                "id": "gemini/gemini-4-argon-medium",
                "vendor": "gemini",
                "model": "gemini-4-argon",
                "effort": "medium",
                "score": 47,
                "estimated": False,
                "authority_eligible": True,
            })
            row2, reason2 = caller_identity.resolve(synthetic_registry, *selector)
            self.assertIsNotNone(row2, reason2)
            self.assertEqual(row2["id"], "gemini/gemini-4-argon-medium")

    def test_auxiliary_field_interference_does_not_misfire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            # (a) Only 10.1.13.25.32 and 10.15.*, no 10.1.28 -> must refuse.
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(None, include_aux=True))])
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir)
            self.assertIn("10.1.28", str(ctx.exception))

            # (b) Aux fields + 10.1.28 -> exact match on 10.1.28.
            _write_synthetic_db(
                agy_dir,
                OTHER_UUID,
                [(0, _build_executor_metadata(b"gemini-4-carbon-medium", include_aux=True))],
            )
            _, selector, _ = self._run_read(agy_dir, conv_id=OTHER_UUID)
            self.assertEqual(selector, ("gemini", "gemini-4-carbon", "medium"))

    def test_missing_fields_empty_table_and_missing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            # Missing DB
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir)
            self.assertIn("database", str(ctx.exception).lower())

            # Empty table
            _write_synthetic_db(agy_dir, CONV_UUID, [])
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir)
            self.assertIn("executor_metadata", str(ctx.exception))

            # Missing field 10
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(None, omit_f10=True))])
            with self.assertRaises(ValueError):
                self._run_read(agy_dir)

            # Missing field 10.1
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(None, omit_f1=True))])
            with self.assertRaises(ValueError):
                self._run_read(agy_dir)

            # Invalid selector format (no effort suffix)
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-carbon"))])
            with self.assertRaises(ValueError):
                self._run_read(agy_dir)

    def test_duplicate_10_1_28_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            # Duplicate tag 28 inside same 10.1
            _write_synthetic_db(
                agy_dir,
                CONV_UUID,
                [(0, _build_executor_metadata(b"gemini-4-carbon-medium", extra_tag28=b"gemini-4-hydrogen-medium"))],
            )
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir)
            self.assertIn("10.1.28", str(ctx.exception))

            # Duplicate 10.1 with tag 28
            _write_synthetic_db(
                agy_dir,
                CONV_UUID,
                [(0, _build_executor_metadata(b"gemini-4-carbon-medium", extra_f1_with_tag28=b"gemini-4-hydrogen-medium"))],
            )
            with self.assertRaises(ValueError):
                self._run_read(agy_dir)

            # Duplicate 10 with 10.1.28
            _write_synthetic_db(
                agy_dir,
                CONV_UUID,
                [(0, _build_executor_metadata(b"gemini-4-carbon-medium", extra_f10_with_tag28=b"gemini-4-hydrogen-medium"))],
            )
            with self.assertRaises(ValueError):
                self._run_read(agy_dir)

    def test_invalid_or_duplicate_conversation_id_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-carbon-medium"))])

            for bad_id in ("", "../etc/passwd", "not-a-uuid"):
                with self.assertRaises(ValueError) as ctx:
                    self._run_read(agy_dir, conv_id=bad_id)
                self.assertIn("ANTIGRAVITY_CONVERSATION_ID", str(ctx.exception))

            # Mismatch between current env and direct child
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir, conv_id=CONV_UUID, child_conv_id=OTHER_UUID)
            self.assertIn("mismatch", str(ctx.exception))

            # Duplicate ANTIGRAVITY_CONVERSATION_ID in KERN_PROCARGS2 environment block
            raw = _pack_procargs2(
                ["/bin/zsh", "-c", "omnilane whoami"],
                [
                    f"ANTIGRAVITY_CSRF_TOKEN={CSRF_SECRET}".encode(),
                    f"ANTIGRAVITY_CONVERSATION_ID={CONV_UUID}".encode(),
                    f"ANTIGRAVITY_CONVERSATION_ID={OTHER_UUID}".encode(),
                ],
            )
            with self.assertRaises(ValueError) as ctx:
                caller_identity._parse_procargs2(raw)
            self.assertIn("duplicate ANTIGRAVITY_CONVERSATION_ID", str(ctx.exception))
            self.assertNotIn(CSRF_SECRET, str(ctx.exception))

    def test_non_gemini_models_rejected_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            for bad_model in (b"claude-opus-5-5-high", b"claude-sonnet-5-medium", b"gpt-6-luna-medium"):
                _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(bad_model))])
                with self.assertRaises(ValueError) as ctx:
                    self._run_read(agy_dir)
                self.assertIn("Antigravity 的非 Gemini 模型尚未支援", str(ctx.exception))

    def test_wrong_language_server_path_and_wrong_parent_not_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-carbon-medium"))])

            with self.assertRaises(ValueError) as ctx1:
                self._run_read(agy_dir, ls_path="/tmp/fake/language_server", app_path=AGY_APP)
            self.assertIn("no vendor CLI among this process's ancestors", str(ctx1.exception))

            with self.assertRaises(ValueError) as ctx2:
                self._run_read(agy_dir, ls_path=AGY_LS, app_path="/tmp/fake/Antigravity")
            self.assertIn("no vendor CLI among this process's ancestors", str(ctx2.exception))

    def test_csrf_and_ls_address_never_leak_and_whoami_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            home_dir = Path(tmp) / "omnilane_home"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-hydrogen-medium"))])

            # Verify _parse_procargs2 ignores CSRF/LS_ADDRESS completely
            raw = _pack_procargs2(
                ["/bin/zsh", "-c", "omnilane whoami"],
                [
                    f"ANTIGRAVITY_CSRF_TOKEN={CSRF_SECRET}".encode(),
                    f"ANTIGRAVITY_LS_ADDRESS={LS_ADDR_SECRET}".encode(),
                    f"ANTIGRAVITY_CONVERSATION_ID={CONV_UUID}".encode(),
                ],
            )
            _, parsed_env = caller_identity._parse_procargs2(raw)
            self.assertEqual(parsed_env, {"ANTIGRAVITY_CONVERSATION_ID": CONV_UUID})
            self.assertNotIn(CSRF_SECRET, repr(parsed_env))
            self.assertNotIn(LS_ADDR_SECRET, repr(parsed_env))

            # End-to-end main() success path
            def env_lookup(pid: int) -> dict[str, str]:
                if pid == 90:
                    return caller_identity._parse_procargs2(raw)[1]
                return {}

            test_env = {
                "OMNILANE_HOME": str(home_dir),
                "ANTIGRAVITY_CONVERSATION_ID": CONV_UUID,
                "ANTIGRAVITY_CSRF_TOKEN": CSRF_SECRET,
                "ANTIGRAVITY_LS_ADDRESS": LS_ADDR_SECRET,
            }
            stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 patch.object(caller_identity, "_default_antigravity_dir", return_value=agy_dir), \
                 redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                rc = caller_identity.main(
                    ["--registry", str(REGISTRY_PATH)],
                    environment=test_env,
                    lookup=_fake_chain(root_pid=os.getpid()),
                    process_environment=env_lookup,
                )
            self.assertEqual(rc, 0, stderr_buf.getvalue())
            ctx_path = Path(stdout_buf.getvalue().strip())
            self.assertTrue(ctx_path.is_file())
            ctx_text = ctx_path.read_text(encoding="utf-8")
            ctx_data = json.loads(ctx_text)
            self.assertEqual(ctx_data["caller"]["vendor"], "gemini")
            self.assertEqual(ctx_data["caller"]["model"], "gemini-4-hydrogen")
            self.assertEqual(ctx_data["caller"]["effort"], "medium")
            self.assertEqual(ctx_data["inherited_ceiling"], 41)
            for text in (stdout_buf.getvalue(), stderr_buf.getvalue(), ctx_text):
                self.assertNotIn(CSRF_SECRET, text)
                self.assertNotIn(LS_ADDR_SECRET, text)

            # End-to-end main() failure path (Claude model) also must not leak secrets
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"claude-opus-5-5-high"))])
            stdout_err, stderr_err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 patch.object(caller_identity, "_default_antigravity_dir", return_value=agy_dir), \
                 redirect_stdout(stdout_err), redirect_stderr(stderr_err):
                rc_fail = caller_identity.main(
                    ["--registry", str(REGISTRY_PATH)],
                    environment=test_env,
                    lookup=_fake_chain(root_pid=os.getpid()),
                    process_environment=env_lookup,
                )
            self.assertEqual(rc_fail, 3)
            self.assertIn("Antigravity 的非 Gemini 模型尚未支援", stderr_err.getvalue())
            self.assertNotIn(CSRF_SECRET, stderr_err.getvalue())
            self.assertNotIn(LS_ADDR_SECRET, stderr_err.getvalue())

    def test_estimated_or_ineligible_row_rejected_for_063_compat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            home_dir = Path(tmp) / "omnilane_home"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-carbon-medium"))])
            bad_reg = copy.deepcopy(self.registry)
            for row in bad_reg["scored_configs"]:
                if row["id"] == "gemini/gemini-4-carbon-medium":
                    row["estimated"] = True
            test_env = {
                "OMNILANE_HOME": str(home_dir),
                "ANTIGRAVITY_CONVERSATION_ID": CONV_UUID,
            }
            stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 patch.object(caller_identity, "_default_antigravity_dir", return_value=agy_dir), \
                 patch.object(caller_identity, "load_registry", return_value=(bad_reg, "synthetic")), \
                 redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                rc = caller_identity.main(
                    ["--registry", str(REGISTRY_PATH)],
                    environment=test_env,
                    lookup=_fake_chain(root_pid=os.getpid()),
                    process_environment=lambda pid: {"ANTIGRAVITY_CONVERSATION_ID": CONV_UUID} if pid == 90 else {},
                )
            self.assertEqual(rc, 3)
            self.assertIn("not a measured authority-eligible", stderr_buf.getvalue())

    def test_shell_compound_command_hint_and_no_redacted_in_human_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-carbon-medium"))])
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir, child_conv_id="")
            msg = str(ctx.exception)
            self.assertIn(", zsh); run the omnilane command as the only command", msg)
            line = refusal_evidence.human_line(
                dict(failed_gate="caller-identity", **refusal_evidence.caller_guidance(msg))
            )
            self.assertNotIn("[redacted]", line)
            self.assertNotIn(CSRF_SECRET, line)

    def test_all_step_refusal_human_lines_never_redact_or_leak(self) -> None:
        diagnostics = [
            f"current process ANTIGRAVITY_CONVERSATION_ID is missing or not a UUID",
            f"Antigravity direct child ANTIGRAVITY_CONVERSATION_ID is missing or not a UUID (read from pid 90, zsh); run the omnilane command as the only command of the tool call, with no `;`, `&&`, `|`, subshell or VAR=value shell prefix around it (use env VAR=value if needed)",
            "ANTIGRAVITY_CONVERSATION_ID mismatch between current process and Antigravity direct child",
            "Antigravity conversation database is missing",
            "Antigravity conversation database owner mismatch",
            "Antigravity executor_metadata is empty",
            "Antigravity executor_metadata has no model field at protobuf path 10.1.28",
            "Antigravity model selector at 10.1.28 must be <model>-<effort>, got 'gemini-4-carbon'",
            "Antigravity 的非 Gemini 模型尚未支援: claude-opus-5-5-high",
        ]
        for diag in diagnostics:
            line = refusal_evidence.human_line(
                dict(failed_gate="caller-identity", **refusal_evidence.caller_guidance(diag))
            )
            self.assertNotIn("[redacted]", line, diag)
            self.assertNotIn(CSRF_SECRET, line, diag)
            self.assertNotIn(LS_ADDR_SECRET, line, diag)

    def test_native_context_uses_antigravity_desktop_harness_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            home_dir = Path(tmp) / "omnilane_home"
            workdir = Path(tmp) / "proj"
            workdir.mkdir()
            out_file = Path(tmp) / "native-ctx.json"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-hydrogen-medium"))])
            test_env = {
                "OMNILANE_HOME": str(home_dir),
                "ANTIGRAVITY_CONVERSATION_ID": CONV_UUID,
                "ANTIGRAVITY_CSRF_TOKEN": CSRF_SECRET,
            }

            def fake_read_caller(pid: int):
                return caller_identity.read_caller(
                    100,
                    lookup=_fake_chain(),
                    environment=lambda p: {"ANTIGRAVITY_CONVERSATION_ID": CONV_UUID} if p == 90 else {},
                    current_environment=test_env,
                    antigravity_dir=agy_dir,
                )

            stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                rc = native_context.main(
                    [
                        "--workdir", str(workdir),
                        "--inherits-caller-runtime",
                        "--no-host-rows",
                        "--out", str(out_file),
                    ],
                    read_caller=fake_read_caller,
                )
            self.assertEqual(rc, 0, stderr_buf.getvalue())
            data = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(data["harness"], "antigravity-desktop")
            self.assertEqual(data["vendor"], "gemini")
            self.assertEqual(data["current_model"], "gemini-4-hydrogen")
            self.assertEqual(data["current_effort"], "medium")
            self.assertNotIn("caller_identity_verified", data)
            self.assertNotIn(CSRF_SECRET, out_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
