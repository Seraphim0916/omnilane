#!/usr/bin/env python3
"""Every refusal the gate and aa_lanes.py print says only what its own fields say.

The table is every lane in routing.yaml, for callers at the lowest, a middle and
the highest registry score, refused either through the lane's chain or through an
explicit --vendor pin. Each refusal is checked for three things: a number quoted
in "reason" equals the field it names, an "above the ceiling" claim is
numerically true, and "required_caller_effort" is an effort of the caller's own
model whose score reaches the target the message refers to. Offline: providers
are spies, and nothing is dispatched.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from offline_env import fixture_environment_with_isolated_tools

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_lanes  # noqa: E402
import aa_policy  # noqa: E402
import caller_identity  # noqa: E402

REGISTRY_PATH = ROOT / "config/aa-model-policy.json"
ROUTING_PATH = ROOT / "routing.yaml"
ABOVE = "target-above-effective-ceiling"
# A bare integer: not part of a model name, a version or a config id.
NUMBER = re.compile(r"(?<![\w.-])\d+(?![\w-]|\.\d)")


def normalized(effort):
    return None if effort in (None, "-") else effort


def segment(fields):
    """vendor, model, effort exactly as dispatch.sh hands them to aa_policy.py."""
    return (fields[0], fields[1] if len(fields) > 1 else "-",
            normalized(fields[2] if len(fields) > 2 else None))


def ceiling_callers(registry):
    """Scored rows at the lowest, a middle and the highest registry score.

    A row that is its model's floor is also listed as a degraded caller (the
    harness recorded no effort), which has its own reason text.
    """
    scores = sorted({row["score"] for row in registry["scored_configs"]})
    callers = []
    for score in (scores[0], scores[len(scores) // 2], scores[-1]):
        for row in sorted((r for r in registry["scored_configs"] if r["score"] == score),
                          key=lambda r: r["id"]):
            callers.append((row, False))
            if caller_identity.floor_row(registry, row["vendor"], row["model"])["id"] == row["id"]:
                callers.append((row, True))
    return callers


def caller_context(registry, row, degraded):
    value = {"schema_version": 1, "snapshot_id": registry["snapshot"]["id"], "kind": "model",
             "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
             "inherited_ceiling": row["score"]}
    if degraded:
        value["effort_unverified"] = True
    return value


def caller_efforts(registry, caller):
    """effort label (as the messages print it) -> highest score of that caller-model effort."""
    efforts = {}
    for row in registry["scored_configs"]:
        if row["vendor"] == caller.get("vendor") and row["model"] == caller.get("model"):
            label = row["effort"] or "none"
            efforts[label] = max(efforts.get(label, row["score"]), row["score"])
    return efforts


def inconsistencies(registry, refusal, lane):
    """What the refusal claims that its own fields contradict; empty when consistent."""
    found = []
    reason = refusal.get("reason") or ""
    requirement = refusal.get("lane_requirement") or {}
    target = refusal.get("target_score")
    ceiling = refusal.get("effective_ceiling")
    named = {value for value in (target, ceiling, requirement.get("score")) if type(value) is int}
    for number in NUMBER.findall(reason):
        if int(number) not in named:
            found.append(f"reason quotes {number}, which is none of target_score, "
                         f"effective_ceiling, lane_requirement.score {sorted(named)}")
    for score, quoted in re.findall(r"scores (\d+), above this caller's ceiling of (\d+)", reason):
        if int(score) != requirement.get("score"):
            found.append(f"reason scores {score}, lane_requirement.score is {requirement.get('score')}")
        if int(quoted) != ceiling:
            found.append(f"reason ceiling {quoted}, effective_ceiling is {ceiling}")
        if not int(score) > int(quoted):
            found.append(f"reason says {score} is above the ceiling {quoted}")
    for quoted in re.findall(r"floor of (\d+)", reason):
        if int(quoted) != ceiling:
            found.append(f"reason floor {quoted}, effective_ceiling is {ceiling}")
    for quoted in re.findall(r"reaches (\d+)", reason):
        if int(quoted) != target:
            found.append(f"reason reaches {quoted}, target_score is {target}")
    for named_lane, named_target, score, quoted in re.findall(
            r"Lane ([\w-]+) has a target within this caller's ceiling: (\S+) "
            r"\(score (\d+), ceiling (\d+)\)", reason):
        if named_lane != lane or named_lane != requirement.get("lane"):
            found.append(f"hint lane {named_lane} disagrees with lane_requirement")
        if named_target != requirement.get("target"):
            found.append(f"hint target {named_target} disagrees with lane_requirement.target")
        if int(score) != requirement.get("score") or int(quoted) != ceiling:
            found.append("hint score or ceiling disagrees with its corresponding field")
        if int(score) > int(quoted):
            found.append("hint target is above the caller's ceiling")
    message = refusal.get("message") or ""
    for score, quoted in re.findall(r"target score (\d+) exceeds effective caller ceiling (\d+)", message):
        if (int(score), int(quoted)) != (target, ceiling) or not int(score) > int(quoted):
            found.append(f"message {message!r} against target_score {target}, ceiling {ceiling}")

    efforts = caller_efforts(registry, refusal.get("caller") or {})
    # The lane explanation replaces the gate's reason; its effort then refers to the lane minimum.
    lane_reason = reason.startswith(f"lane {lane}'s cheapest target")
    refers_to = requirement.get("score") if lane_reason else target
    effort = refusal.get("required_caller_effort")
    said = re.search(r"at effort (\w+) or higher|explicit effort of (\w+) or higher", reason)
    said = (said.group(1) or said.group(2)) if said else None
    if said != effort:
        found.append(f"reason names effort {said}, required_caller_effort is {effort}")
    if effort is not None and efforts.get(effort, -1) < refers_to:
        found.append(f"required_caller_effort {effort} of the caller's model scores "
                     f"{efforts.get(effort)}, not reaching {refers_to}")
    lane_effort = requirement.get("required_caller_effort")
    if lane_effort is not None and efforts.get(lane_effort, -1) < requirement.get("score"):
        found.append(f"lane_requirement.required_caller_effort {lane_effort} does not reach "
                     f"{requirement.get('score')}")
    if refusal.get("code") == ABOVE and type(target) is int and not target > ceiling:
        found.append(f"refused as above the ceiling with target_score {target} <= {ceiling}")
    return found


class RefusalTableTests(unittest.TestCase):
    """aa_policy.decide's refusal, annotated by aa_lanes.py, for every lane x ceiling x mode."""

    @classmethod
    def setUpClass(cls):
        cls.registry, cls.digest = aa_policy.load_registry(REGISTRY_PATH)
        cls.table = aa_lanes.lanes([ROUTING_PATH])
        # Every routing target is treated as transport-verified, so the only gate left is the
        # ceiling; this host's real overlay would otherwise decide which targets are reached.
        cls.verified = copy.deepcopy(cls.registry)
        for segments in cls.table.values():
            for fields in segments:
                vendor, model, effort = segment(fields)
                if vendor in ("off", "vote"):
                    continue
                row, _ = caller_identity.resolve(cls.verified, vendor, model, effort)
                if row is None:
                    continue
                row["transport_mapping"].update(status="verified", runtime_verified=True,
                                                runtime_model=model, runtime_effort=effort)
                if vendor == "grok":
                    row["transport_mapping"].update(selector_type="cli_reasoning_effort",
                                                    cli_flag="--reasoning-effort")

    def decide(self, fields, caller):
        vendor, model, effort = segment(fields)
        return aa_policy.decide(self.verified, self.digest, vendor=vendor, model=model,
                                effort=effort, caller=caller, caller_sha256="1" * 64)

    def lane_refusal(self, segments, caller):
        # dispatch.sh resolve_chain: the first allowed candidate wins; a refusal is the last one tried.
        decision = None
        for fields in segments:
            decision = self.decide(fields, caller)
            if decision["allowed"]:
                return None
        return decision

    def vendor_refusal(self, segments, vendor, caller):
        # --vendor: only the lane's first segment of that vendor is considered.
        decision = self.decide(next(f for f in segments if f[0] == vendor), caller)
        return None if decision["allowed"] else decision

    def annotate(self, decision, lane):
        out = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(decision))), \
                contextlib.redirect_stdout(out):
            aa_lanes.main(["--registry", str(REGISTRY_PATH), "--routing", str(ROUTING_PATH),
                           "--lane", lane])
        return json.loads(out.getvalue())

    def cases(self):
        for lane, segments in self.table.items():
            for row, degraded in ceiling_callers(self.registry):
                caller = caller_context(self.registry, row, degraded)
                modes = [("lane", self.lane_refusal(segments, caller))]
                modes += [(f"--vendor {vendor}", self.vendor_refusal(segments, vendor, caller))
                          for vendor in dict.fromkeys(f[0] for f in segments)
                          if vendor not in ("off", "vote")]
                for mode, decision in modes:
                    yield lane, row, degraded, mode, decision

    def test_every_refusal_matches_its_own_fields(self):
        checked = 0
        seen = set()
        for lane, row, degraded, mode, decision in self.cases():
            if decision is None:
                continue
            refusal = self.annotate(decision, lane)
            checked += 1
            reason = refusal.get("reason") or ""
            seen.update(label for label, hit in (
                ("lane-explanation", reason.startswith(f"lane {lane}'s")),
                ("gate-relaunch", reason.startswith("relaunch the calling session")),
                ("gate-degraded", "floor of" in reason),
                ("no-effort-reaches", "o effort of" in reason or "no scored effort" in reason),
                ("explicit-vendor", mode != "lane" and refusal.get("code") == ABOVE),
            ) if hit)
            with self.subTest(lane=lane, caller=row["id"], degraded=degraded, mode=mode):
                self.assertEqual(inconsistencies(self.registry, refusal, lane), [], reason)
        self.assertGreater(checked, 0)
        # The table must reach every message shape, or a passing run proves nothing about it.
        self.assertEqual(seen, {"lane-explanation", "gate-relaunch", "gate-degraded",
                                "no-effort-reaches", "explicit-vendor"})

    def test_the_table_covers_every_routing_lane_and_three_ceilings(self):
        lanes = {lane for lane, *_ in self.cases()}
        self.assertEqual(lanes, set(self.table))
        self.assertIn("consult", lanes)
        scores = {row["score"] for row, _ in ceiling_callers(self.registry)}
        every = [row["score"] for row in self.registry["scored_configs"]]
        self.assertEqual(len(scores), 3)
        self.assertEqual((min(scores), max(scores)), (min(every), max(every)))

    def test_vendor_refusal_points_to_reachable_lane_target(self):
        checked = 0
        for lane, row, degraded, mode, decision in self.cases():
            if decision is None or mode == "lane" or decision.get("code") != ABOVE:
                continue
            refusal = self.annotate(decision, lane)
            need = refusal.get("lane_requirement")
            if not need or need["score"] > refusal["effective_ceiling"]:
                continue
            with self.subTest(lane=lane, caller=row["id"], degraded=degraded, mode=mode):
                checked += 1
                hint = (f"Lane {lane} has a target within this caller's ceiling: "
                        f"{need['target']} (score {need['score']}, "
                        f"ceiling {refusal['effective_ceiling']}). "
                        "If you specified --vendor, retry the lane without --vendor.")
                separator = " " if decision["reason"].endswith(".") else ". "
                self.assertEqual(refusal["reason"], decision["reason"] + separator + hint)
                self.assertEqual(inconsistencies(self.registry, refusal, lane), [])
                # Adding guidance must preserve every original gate field except reason.
                for key, value in decision.items():
                    if key != "reason":
                        self.assertEqual(refusal[key], value, key)
        self.assertGreater(checked, 0)

    def test_unreachable_lane_has_no_vendor_retry_hint(self):
        checked = 0
        for lane, _, _, _, decision in self.cases():
            if decision is None or decision.get("code") != ABOVE:
                continue
            refusal = self.annotate(decision, lane)
            need = refusal.get("lane_requirement")
            if need and need["score"] > refusal["effective_ceiling"]:
                checked += 1
                self.assertNotIn("without --vendor", refusal["reason"])
        self.assertGreater(checked, 0)

    def test_checker_rejects_incorrect_vendor_hint_fields(self):
        good = {"allowed": False, "code": ABOVE, "target_score": 46,
                "effective_ceiling": 33,
                "lane_requirement": {"lane": "example", "target": "vendor/model", "score": 9},
                "reason": ("Lane example has a target within this caller's ceiling: "
                           "vendor/model (score 9, ceiling 33). "
                           "If you specified --vendor, retry the lane without --vendor.")}
        self.assertEqual(inconsistencies(self.registry, good, "example"), [])
        for old, new in (("score 9", "score 33"), ("ceiling 33", "ceiling 9"),
                         ("Lane example", "Lane other"), ("vendor/model", "vendor/other")):
            with self.subTest(replacement=new):
                broken = dict(good, reason=good["reason"].replace(old, new))
                self.assertNotEqual(inconsistencies(self.registry, broken, "example"), [])
        above = copy.deepcopy(good)
        above["lane_requirement"]["score"] = 46
        above["reason"] = above["reason"].replace("score 9", "score 46")
        self.assertNotEqual(inconsistencies(self.registry, above, "example"), [])

    def test_the_checker_flags_each_kind_of_inconsistency(self):
        caller = next(row for row in self.registry["scored_configs"]
                      if row["id"] == "codex/gpt-5-6-sol-low")
        good = {"allowed": False, "code": ABOVE, "target_score": 46, "effective_ceiling": 33,
                "caller": {key: caller[key] for key in aa_policy.IDENTITY_FIELDS},
                "message": "target score 46 exceeds effective caller ceiling 33",
                "lane_requirement": {"lane": "consult", "target": "x", "score": 41,
                                     "required_caller_effort": "high"},
                "required_caller_effort": "high",
                "reason": ("lane consult's cheapest target x scores 41, above this caller's "
                           "ceiling of 33. Relaunch the calling session at effort high or "
                           "higher, or have a human operator dispatch it.")}
        self.assertEqual(inconsistencies(self.registry, good, "consult"), [])
        broken = {
            # PR #52's case: a lane minimum below the ceiling still called "above" it.
            "below called above": dict(good, effective_ceiling=51, reason=good["reason"].replace(
                "ceiling of 33", "ceiling of 51")),
            "stray number": dict(good, reason=good["reason"].replace("scores 41", "scores 40")),
            "effort too low": dict(good, required_caller_effort="low",
                                   reason=good["reason"].replace("effort high", "effort low")),
            "effort of another model": dict(good, required_caller_effort="xmax",
                                            reason=good["reason"].replace("effort high", "effort xmax")),
            "reason and field disagree": dict(good, required_caller_effort="xhigh"),
        }
        for label, refusal in broken.items():
            with self.subTest(label):
                self.assertNotEqual(inconsistencies(self.registry, refusal, "consult"), [])


class DispatchRefusalTests(unittest.TestCase):
    """The refusal JSON dispatch.sh prints on stderr, through the real routing table.

    Only codex is transport-verified (a spy, as in test_aa_lineage), so a lane's other
    vendors are refused at the transport gate and only codex targets meet the ceiling.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="refusal-consistency-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.registry = json.loads(REGISTRY_PATH.read_text())
        bins = base / "bin"
        bins.mkdir()
        spy = bins / "codex"
        spy.write_text("#!/usr/bin/env python3\nraise SystemExit('provider must not run')\n")
        spy.chmod(0o755)
        mappings = [{"config_id": row["id"], "identity": {k: row[k] for k in aa_policy.IDENTITY_FIELDS},
                     "runtime_model": row["model"], "runtime_effort": row["effort"],
                     "verification": "request-selector-contract"}
                    for row in self.registry["scored_configs"] if row["vendor"] == "codex"]
        overlay = base / "runtime-overlay.json"
        overlay.write_text(json.dumps({
            "schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"],
            "host": socket.gethostname(),
            "evidence": [{"path": str(spy), "sha256": hashlib.sha256(spy.read_bytes()).hexdigest()}],
            "mappings": mappings}))
        self.base = base
        self.env = {k: v for k, v in fixture_environment_with_isolated_tools(self).items()
                    if not k.startswith("OMNILANE_") and k not in ("CODEX_BIN", "CLAUDE_BIN")}
        self.env.update(OMNILANE_HOME=str(self.home), CODEX_BIN=str(spy), OMNILANE_INBOX="0",
                        PATH=str(bins) + os.pathsep + self.env["PATH"],
                        OMNILANE_AA_TRANSPORT_OVERLAY=str(overlay))

    def printed_refusal(self, row, lane, *args):
        context = self.base / f"caller-{row['score']}.json"
        context.write_text(json.dumps(caller_context(self.registry, row, False)))
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--dry-run", "--caller-context", str(context),
             "--aa-policy", str(REGISTRY_PATH), "--timeout", "10", *args, lane, "Bounded offline test"],
            env=self.env, cwd=ROOT, text=True, capture_output=True, timeout=60)
        lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
        if result.returncode == 0:
            self.assertEqual(lines, [], result.stderr)
            return None
        self.assertIn(result.returncode, (3, 4), result.stderr)
        self.assertEqual(len(lines), 1, result.stderr)
        return json.loads(lines[0])

    def test_printed_refusals_match_their_own_fields(self):
        registry, _ = aa_policy.load_registry(REGISTRY_PATH)
        table = aa_lanes.lanes([ROUTING_PATH])
        scores = sorted({row["score"] for row in registry["scored_configs"]})
        callers = [min((r for r in registry["scored_configs"] if r["score"] == score),
                       key=lambda r: r["id"])
                   for score in (scores[0], scores[len(scores) // 2], scores[-1])]
        above = 0
        for lane, segments in table.items():
            modes = [()] + ([("--vendor", "codex")] if any(f[0] == "codex" for f in segments) else [])
            for row in callers:
                for mode in modes:
                    with self.subTest(lane=lane, caller=row["id"], mode=" ".join(mode) or "lane"):
                        refusal = self.printed_refusal(row, lane, *mode)
                        if refusal is None:
                            continue
                        self.assertIs(refusal["allowed"], False)
                        if refusal["code"] == "lane-disabled":
                            # Printed by dispatch.sh itself: no gate ran, so no ceiling to quote.
                            self.assertEqual(refusal["lane"], lane)
                            self.assertNotIn("reason", refusal)
                            continue
                        for key in ("code", "message", "reason", "effective_ceiling", "caller"):
                            self.assertIn(key, refusal)
                        self.assertIsInstance(refusal["eligible_lanes"], list)
                        for entry in refusal["eligible_lanes"]:
                            self.assertLessEqual(entry["score"], refusal["effective_ceiling"])
                        if refusal["code"] == ABOVE:
                            above += 1
                            self.assertIn("lane_requirement", refusal)
                            self.assertEqual(refusal["lane_requirement"]["lane"], lane)
                        self.assertEqual(inconsistencies(registry, refusal, lane), [],
                                         refusal.get("reason"))
        self.assertGreater(above, 0)


if __name__ == "__main__":
    unittest.main()
