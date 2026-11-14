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

            # In v1 policy, argon has no medium row (NO_ROW); in v2 (bda3c2e), argon/medium is a derived
            # upper-bound row (estimated=True, authority_eligible=False, score_source="upper-bound").
            # Either way it must be cleanly refused as a caller identity.
            argon_med_cid = "22222222-2222-3333-4444-555555555555"
            _write_synthetic_db(agy_dir, argon_med_cid, [(0, _build_executor_metadata(b"gemini-4-argon-medium"))])
            _, selector, _ = self._run_read(agy_dir, conv_id=argon_med_cid)
            row, reason = caller_identity.resolve(self.registry, *selector)
            if row is not None:
                self.assertFalse(caller_identity.is_authority_eligible_row(row))
            else:
                self.assertTrue(
                    caller_identity.NO_ROW in reason or "derived or authority-ineligible" in reason,
                    reason,
                )

            # With a synthetic policy table that replaces any existing argon/medium row with a measured
            # authority-eligible one, it resolves cleanly in both v1 and v2 schemas.
            synthetic_registry = copy.deepcopy(self.registry)
            synthetic_registry["scored_configs"] = [
                r for r in synthetic_registry["scored_configs"]
                if not (r["vendor"] == "gemini" and r["model"] == "gemini-4-argon" and r["effort"] == "medium")
            ]
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
            self.assertTrue(caller_identity.is_authority_eligible_row(row2))
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
            workdir = Path(tmp) / "proj"
            workdir.mkdir()
            out_file = Path(tmp) / "native-ctx.json"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(b"gemini-4-carbon-medium"))])
            test_env = {
                "OMNILANE_HOME": str(home_dir),
                "ANTIGRAVITY_CONVERSATION_ID": CONV_UUID,
            }
            for override in (
                {"estimated": True},
                {"authority_eligible": False},
                {"score_source": "upper-bound"},
            ):
                with self.subTest(override=override):
                    bad_reg = copy.deepcopy(self.registry)
                    for row in bad_reg["scored_configs"]:
                        if row["id"] == "gemini/gemini-4-carbon-medium":
                            row.update(override)
                            self.assertFalse(caller_identity.is_authority_eligible_row(row))
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
                    self.assertIn(
                        "derived or authority-ineligible rows cannot supply caller authority",
                        stderr_buf.getvalue(),
                    )
                    self.assertNotIn("restart the CLI with explicit", stderr_buf.getvalue())
                    self.assertNotIn("restart the vendor CLI", stderr_buf.getvalue())

                    stdout_nc, stderr_nc = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, test_env, clear=False), \
                         patch.object(caller_identity, "load_registry", return_value=(bad_reg, "synthetic")), \
                         redirect_stdout(stdout_nc), redirect_stderr(stderr_nc):
                        rc_nc = native_context.main(
                            ["--workdir", str(workdir), "--no-host-rows", "--out", str(out_file)],
                            read_caller=lambda pid: self._run_read(agy_dir),
                        )
                    self.assertEqual(rc_nc, 3)
                    self.assertIn(
                        "derived or authority-ineligible rows cannot supply caller authority",
                        stderr_nc.getvalue(),
                    )

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
            "current process ANTIGRAVITY_CONVERSATION_ID is missing or not a UUID",
            "Antigravity direct child ANTIGRAVITY_CONVERSATION_ID is missing or not a UUID (read from pid 90, zsh); run the omnilane command as the only command of the tool call, with no `;`, `&&`, `|`, subshell or VAR=value shell prefix around it (use env VAR=value if needed)",
            "ANTIGRAVITY_CONVERSATION_ID mismatch between current process and Antigravity direct child",
            "Antigravity conversation database is missing",
            "Antigravity conversation database owner mismatch",
            "Antigravity executor_metadata is empty",
            "Antigravity executor_metadata contains a null or non-integer idx",
            "Antigravity executor_metadata has duplicate maximum idx",
            "Antigravity executor_metadata exceeds 262144 bytes",
            "Antigravity executor_metadata protobuf is malformed (protobuf varint exceeds 64 bits)",
            "Antigravity executor_metadata has no model field at protobuf path 10.1.28",
            "Antigravity model selector at 10.1.28 must be <model>-<effort>",
            "Antigravity model selector at 10.1.28 is malformed",
            "Antigravity model selector at 10.1.28 names an unrecognized model",
            "derived or authority-ineligible rows cannot supply caller authority",
            "Antigravity 的非 Gemini 模型尚未支援",
        ]
        for diag in diagnostics:
            line = refusal_evidence.human_line(
                dict(failed_gate="caller-identity", **refusal_evidence.caller_guidance(diag))
            )
            self.assertNotIn("[redacted]", line, diag)
            self.assertNotIn(CSRF_SECRET, line, diag)
            self.assertNotIn(LS_ADDR_SECRET, line, diag)

    def test_p1_a_lowercase_unknown_gemini_selector_never_echoed_and_native_context_falls_back(self) -> None:
        token = "synthetic-private-token-7788"
        contaminated = f"gemini-{token}-high".encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            home_dir = Path(tmp) / "omnilane_home"
            workdir = Path(tmp) / "proj"
            workdir.mkdir()
            out_file = Path(tmp) / "native-ctx.json"
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(contaminated))])
            test_env = {
                "OMNILANE_HOME": str(home_dir),
                "ANTIGRAVITY_CONVERSATION_ID": CONV_UUID,
            }

            # 0. Direct read_caller() refuses with fixed message and never echoes token
            with self.assertRaises(ValueError) as ctx_direct:
                self._run_read(agy_dir)
            self.assertNotIn(token, str(ctx_direct.exception))
            self.assertNotIn("7788", str(ctx_direct.exception))

            # 1. omnilane whoami (caller_identity.main) refuses (rc=3) without echoing token anywhere
            stdout_who, stderr_who = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 patch.object(caller_identity, "_default_antigravity_dir", return_value=agy_dir), \
                 redirect_stdout(stdout_who), redirect_stderr(stderr_who):
                rc_who = caller_identity.main(
                    ["--registry", str(REGISTRY_PATH)],
                    environment=test_env,
                    lookup=_fake_chain(root_pid=os.getpid()),
                    process_environment=lambda p: {"ANTIGRAVITY_CONVERSATION_ID": CONV_UUID} if p == 90 else {},
                )
            self.assertEqual(rc_who, 3)
            combined_who = stdout_who.getvalue() + stderr_who.getvalue()
            self.assertNotIn(token, combined_who)
            self.assertNotIn("7788", combined_who)

            # 2. native-context without --vendor refuses (rc=3) without echoing token
            stdout_nc1, stderr_nc1 = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 redirect_stdout(stdout_nc1), redirect_stderr(stderr_nc1):
                rc_nc1 = native_context.main(
                    ["--workdir", str(workdir), "--no-host-rows", "--out", str(out_file)],
                    read_caller=lambda pid: self._run_read(agy_dir),
                )
            self.assertEqual(rc_nc1, 3)
            combined_nc1 = stdout_nc1.getvalue() + stderr_nc1.getvalue()
            self.assertNotIn(token, combined_nc1)
            self.assertNotIn("7788", combined_nc1)

            # 3. native-context WITH --vendor gemini --model gemini-4-carbon --inherits-caller-runtime
            # must fall back to host statement (rc=0, caller_identity_verified=False), NOT exit 2!
            stdout_nc2, stderr_nc2 = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 redirect_stdout(stdout_nc2), redirect_stderr(stderr_nc2):
                rc_nc2 = native_context.main(
                    [
                        "--workdir", str(workdir),
                        "--vendor", "gemini",
                        "--model", "gemini-4-carbon",
                        "--inherits-caller-runtime",
                        "--no-host-rows",
                        "--out", str(out_file),
                    ],
                    read_caller=lambda pid: self._run_read(agy_dir),
                )
            self.assertEqual(rc_nc2, 0, stderr_nc2.getvalue())
            ctx_json_text = out_file.read_text(encoding="utf-8")
            ctx_json = json.loads(ctx_json_text)
            self.assertIs(ctx_json["caller_identity_verified"], False)
            self.assertEqual(ctx_json["vendor"], "gemini")
            self.assertEqual(ctx_json["current_model"], "gemini-4-carbon")
            combined_nc2 = stdout_nc2.getvalue() + stderr_nc2.getvalue() + ctx_json_text
            self.assertNotIn(token, combined_nc2)
            self.assertNotIn("7788", combined_nc2)
            self.assertNotIn("this harness reads as", combined_nc2)

            # 4. Defense-in-depth: even if an injected read_caller stub returns an unrecognized
            # antigravity-desktop model tuple directly, native_context.main still refuses to echo
            # it and falls back to host statement (rc=0, caller_identity_verified=False).
            stdout_nc3, stderr_nc3 = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, test_env, clear=False), \
                 redirect_stdout(stdout_nc3), redirect_stderr(stderr_nc3):
                rc_nc3 = native_context.main(
                    [
                        "--workdir", str(workdir),
                        "--vendor", "gemini",
                        "--model", "gemini-4-carbon",
                        "--inherits-caller-runtime",
                        "--no-host-rows",
                        "--out", str(out_file),
                    ],
                    read_caller=lambda pid: (
                        80,
                        ("gemini", f"gemini-{token}", "high"),
                        f"antigravity-desktop conversation {CONV_UUID}",
                    ),
                )
            self.assertEqual(rc_nc3, 0, stderr_nc3.getvalue())
            ctx_json3 = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertIs(ctx_json3["caller_identity_verified"], False)
            combined_nc3 = stdout_nc3.getvalue() + stderr_nc3.getvalue() + out_file.read_text(encoding="utf-8")
            self.assertNotIn(token, combined_nc3)
            self.assertNotIn("7788", combined_nc3)
            self.assertNotIn("this harness reads as", combined_nc3)

    def test_p1_1_overflowing_protobuf_varint_and_invalid_field_number_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            valid_10_1_28 = _pb_bytes(10, _pb_bytes(1, _pb_bytes(28, b"gemini-4-carbon-high")))
            overflow_9ff_7f = b"\xff" * 9 + b"\x7f"
            overflow_9ff_02 = b"\xff" * 9 + b"\x02"
            overflow_11_bytes = b"\x80" * 10 + b"\x01"

            # Overflowing varint in an unknown field at top level, inside field 10, and inside field 10.1.
            malformed_blobs = [
                _varint((99 << 3) | 0) + overflow_9ff_7f + valid_10_1_28,
                _varint((99 << 3) | 0) + overflow_9ff_02 + valid_10_1_28,
                _varint((99 << 3) | 0) + overflow_11_bytes + valid_10_1_28,
                _pb_bytes(
                    10,
                    _varint((99 << 3) | 0) + overflow_9ff_7f + _pb_bytes(1, _pb_bytes(28, b"gemini-4-carbon-high")),
                ),
                _pb_bytes(
                    10,
                    _pb_bytes(
                        1,
                        _varint((99 << 3) | 0) + overflow_9ff_7f + _pb_bytes(28, b"gemini-4-carbon-high"),
                    ),
                ),
                # Field number exceeding the 29-bit protobuf maximum (0x1FFFFFFF).
                _varint(((0x1FFFFFFF + 1) << 3) | 0) + _varint(1) + valid_10_1_28,
            ]
            for idx, bad_blob in enumerate(malformed_blobs):
                with self.subTest(case=idx):
                    _write_synthetic_db(agy_dir, CONV_UUID, [(0, bad_blob)])
                    with self.assertRaises(ValueError):
                        self._run_read(agy_dir)

            # Standard 64-bit max varint (9 * 0xff + 0x01, used by protobuf for int64 -1) remains valid.
            max_uint64_varint = b"\xff" * 9 + b"\x01"
            valid_with_neg1 = _varint((99 << 3) | 0) + max_uint64_varint + valid_10_1_28
            _write_synthetic_db(agy_dir, CONV_UUID, [(0, valid_with_neg1)])
            _, selector, _ = self._run_read(agy_dir)
            self.assertEqual(selector, ("gemini", "gemini-4-carbon", "high"))

    def test_p1_2_duplicate_max_idx_and_null_or_non_integer_idx_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            conv_dir = agy_dir / "conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)
            db_path = conv_dir / f"{CONV_UUID}.db"
            high_blob = _build_executor_metadata(b"gemini-4-carbon-high")
            low_blob = _build_executor_metadata(b"gemini-4-carbon-low")

            def write_unconstrained_rows(rows: list[tuple[object, object]]) -> None:
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute("DROP TABLE IF EXISTS executor_metadata")
                    conn.execute("CREATE TABLE executor_metadata (idx integer, data blob)")
                    for r_idx, r_blob in rows:
                        conn.execute("INSERT INTO executor_metadata (idx, data) VALUES (?, ?)", (r_idx, r_blob))
                    conn.commit()
                finally:
                    conn.close()

            # Duplicate maximum idx in both insertion orders must fail closed (never pick high or low).
            for rows in (
                [(5, high_blob), (5, low_blob)],
                [(5, low_blob), (5, high_blob)],
                [(1, low_blob), (7, high_blob), (7, low_blob)],
            ):
                write_unconstrained_rows(rows)
                with self.assertRaises(ValueError) as ctx:
                    self._run_read(agy_dir)
                self.assertIn("duplicate maximum idx", str(ctx.exception))

            # NULL idx (alone or alongside valid integer rows) and non-integer idx must fail closed.
            for bad_rows in (
                [(None, high_blob)],
                [(1, low_blob), (None, high_blob)],
                [(2, low_blob), (1, low_blob), (None, high_blob)],
                [(5, low_blob), ("not-an-int", high_blob)],
                [(5, low_blob), (1.5, high_blob)],
            ):
                write_unconstrained_rows(bad_rows)
                with self.assertRaises(ValueError) as ctx:
                    self._run_read(agy_dir)
                self.assertIn("idx", str(ctx.exception))

            # Distinct integer idx values pick the unique maximum row cleanly.
            write_unconstrained_rows([(1, high_blob), (2, low_blob)])
            _, selector, _ = self._run_read(agy_dir)
            self.assertEqual(selector, ("gemini", "gemini-4-carbon", "low"))

    def test_p1_3_private_text_in_10_1_28_never_echoed_in_exceptions_or_cli(self) -> None:
        secret = "SYNTHETIC_PRIVATE_TEXT"
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            home_dir = Path(tmp) / "omnilane_home"
            workdir = Path(tmp) / "proj"
            workdir.mkdir()
            out_file = Path(tmp) / "native-ctx.json"
            test_env = {
                "OMNILANE_HOME": str(home_dir),
                "ANTIGRAVITY_CONVERSATION_ID": CONV_UUID,
            }

            for contaminated in (
                secret.encode("utf-8"),
                f"{secret}-high".encode("utf-8"),
                f"claude-{secret}-high".encode("utf-8"),
                f"gemini-{secret}-high".encode("utf-8"),
            ):
                with self.subTest(payload=contaminated):
                    _write_synthetic_db(agy_dir, CONV_UUID, [(0, _build_executor_metadata(contaminated))])

                    # 1. Direct read_caller() exception text
                    with self.assertRaises(ValueError) as ctx:
                        self._run_read(agy_dir)
                    self.assertNotIn(secret, str(ctx.exception))
                    self.assertNotIn("SYNTHETIC", str(ctx.exception))
                    self.assertNotIn("PRIVATE", str(ctx.exception))

                    # 2. caller_identity.main() CLI refusal output
                    stdout_who, stderr_who = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, test_env, clear=False), \
                         patch.object(caller_identity, "_default_antigravity_dir", return_value=agy_dir), \
                         redirect_stdout(stdout_who), redirect_stderr(stderr_who):
                        rc_who = caller_identity.main(
                            ["--registry", str(REGISTRY_PATH)],
                            environment=test_env,
                            lookup=_fake_chain(root_pid=os.getpid()),
                            process_environment=lambda p: {"ANTIGRAVITY_CONVERSATION_ID": CONV_UUID} if p == 90 else {},
                        )
                    self.assertEqual(rc_who, 3)
                    combined_who = stdout_who.getvalue() + stderr_who.getvalue()
                    self.assertNotIn(secret, combined_who)
                    self.assertNotIn("SYNTHETIC", combined_who)

                    # 3. native_context.main() CLI refusal and host-statement fallback output
                    def fake_read_caller(pid: int):
                        return self._run_read(agy_dir)

                    stdout_nc1, stderr_nc1 = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, test_env, clear=False), \
                         redirect_stdout(stdout_nc1), redirect_stderr(stderr_nc1):
                        rc_nc1 = native_context.main(
                            ["--workdir", str(workdir), "--no-host-rows", "--out", str(out_file)],
                            read_caller=fake_read_caller,
                        )
                    self.assertEqual(rc_nc1, 3)
                    combined_nc1 = stdout_nc1.getvalue() + stderr_nc1.getvalue()
                    self.assertNotIn(secret, combined_nc1)
                    self.assertNotIn("SYNTHETIC", combined_nc1)

                    stdout_nc2, stderr_nc2 = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, test_env, clear=False), \
                         redirect_stdout(stdout_nc2), redirect_stderr(stderr_nc2):
                        rc_nc2 = native_context.main(
                            [
                                "--workdir", str(workdir),
                                "--vendor", "gemini",
                                "--model", "gemini-4-carbon",
                                "--inherits-caller-runtime",
                                "--no-host-rows",
                                "--out", str(out_file),
                            ],
                            read_caller=fake_read_caller,
                        )
                    self.assertEqual(rc_nc2, 0)
                    combined_nc2 = stdout_nc2.getvalue() + stderr_nc2.getvalue() + out_file.read_text(encoding="utf-8")
                    self.assertNotIn(secret, combined_nc2)
                    self.assertNotIn("SYNTHETIC", combined_nc2)

    def test_p1_4_oversized_executor_metadata_blob_rejected_in_sql_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agy_dir = Path(tmp) / "agy"
            max_bytes = getattr(caller_identity, "_ANTIGRAVITY_METADATA_MAX_BYTES", 262_144)
            base_blob = _build_executor_metadata(b"gemini-4-carbon-high", include_aux=False)
            # Construct a valid protobuf padding field so total length == max_bytes + 1.
            pad_header_probe = _varint((12 << 3) | 2)
            # Solve payload_len such that len(base_blob) + len(tag) + len(varint(payload_len)) + payload_len == max_bytes + 1
            target_total = max_bytes + 1
            payload_len = target_total - len(base_blob) - len(pad_header_probe) - 3
            while len(base_blob) + len(_pb_bytes(12, b"x" * payload_len)) < target_total:
                payload_len += 1
            while len(base_blob) + len(_pb_bytes(12, b"x" * payload_len)) > target_total:
                payload_len -= 1
            oversized_blob = base_blob + _pb_bytes(12, b"x" * payload_len)
            self.assertEqual(len(oversized_blob), max_bytes + 1)

            small_old_blob = _build_executor_metadata(b"gemini-4-carbon-low", include_aux=False)
            db_path = _write_synthetic_db(agy_dir, CONV_UUID, [(0, small_old_blob), (1, oversized_blob)])

            # Must fail closed on oversized latest row and never fall back to idx=0.
            with self.assertRaises(ValueError) as ctx:
                self._run_read(agy_dir)
            self.assertIn("exceeds", str(ctx.exception))

            # Verify SQL query itself does not materialize the oversized BLOB into Python bytes.
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    caller_identity._ANTIGRAVITY_METADATA_QUERY,
                    (max_bytes,),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows[0][0], 1)
            self.assertEqual(rows[0][3], max_bytes + 1)
            self.assertIsNone(rows[0][4])

            # Exact boundary length == max_bytes remains accepted.
            exact_blob = oversized_blob[:-1]
            # Adjust field 12 length by 1 byte so protobuf stays valid at exact max_bytes.
            exact_pad_len = payload_len - 1
            exact_blob = base_blob + _pb_bytes(12, b"x" * exact_pad_len)
            if len(exact_blob) <= max_bytes:
                _write_synthetic_db(agy_dir, CONV_UUID, [(0, exact_blob)])
                _, selector, _ = self._run_read(agy_dir)
                self.assertEqual(selector, ("gemini", "gemini-4-carbon", "high"))

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
