#!/usr/bin/env python3
"""The capabilities doc's current lane tables list routing.yaml's lanes in routing.yaml's order."""

import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTING = ROOT / "routing.yaml"
CAPABILITIES = ROOT / "docs" / "model-capabilities-2026-09.md"
SPEC = importlib.util.spec_from_file_location("aa_lanes", ROOT / "scripts/aa_rebaseline.py")
aa_rebaseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aa_rebaseline)


def routing_lanes(path: Path = ROUTING) -> dict:
    """Candidates per lane, spelled the way `aa_rebaseline.py lanes` prints them."""
    return {
        lane: [f"{vendor} {model}" + (f" {effort}" if effort else "") for vendor, model, effort in chain]
        for lane, chain in aa_rebaseline.lane_table(path).items()
    }


def doc_lanes(text: str) -> dict:
    """Candidates per lane from the first `### Current lane measurements` section."""
    start = text.index("### Current lane measurements")
    end = text.find("\n## ", start)
    lanes, lane = {}, None
    for line in text[start:end if end >= 0 else None].splitlines():
        heading = re.fullmatch(r"\*\*([a-z][a-z0-9-]*)\*\*", line.strip())
        if heading:
            lane = heading.group(1)
            lanes[lane] = []
            continue
        row = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if lane and row:
            lanes[lane].append((int(row.group(1)), row.group(2)))
    for lane, rows in lanes.items():
        numbers = [number for number, _ in rows]
        if numbers != list(range(1, len(rows) + 1)):
            raise AssertionError(f"{lane}: rows are numbered {numbers}")
    return {lane: [name for _, name in rows] for lane, rows in lanes.items()}


def mismatches(routing: dict, doc: dict) -> list:
    problems = []
    if set(routing) != set(doc):
        problems.append(f"lane names: only in routing.yaml {sorted(set(routing) - set(doc))}; "
                        f"only in the doc {sorted(set(doc) - set(routing))}")
    for lane in sorted(set(routing) & set(doc), key=list(routing).index):
        if routing[lane][:1] != doc[lane][:1]:
            problems.append(f"{lane} first choice: routing.yaml {routing[lane][:1]}, doc {doc[lane][:1]}")
        if routing[lane] != doc[lane]:
            problems.append(f"{lane} order: routing.yaml {routing[lane]}, doc {doc[lane]}")
    return problems


class CapabilitiesDocMatchesRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routing = routing_lanes()
        cls.doc = doc_lanes(CAPABILITIES.read_text(encoding="utf-8"))

    def test_lane_section_is_present(self):
        self.assertGreaterEqual(len(self.doc), 10, "current lane measurements section not found")
        for lane, chain in self.doc.items():
            self.assertTrue(chain, f"{lane}: no candidate rows")

    def test_doc_lanes_and_order_match_routing(self):
        self.assertEqual(mismatches(self.routing, self.doc), [],
                         "rerun `aa_rebaseline.py lanes` into the doc's current lane section")

    def test_reordered_routing_lane_is_reported(self):
        text = ROUTING.read_text(encoding="utf-8")
        lane = next(iter(self.routing))
        line = next(item for item in text.splitlines() if item.startswith(f"{lane}:"))
        body, _, comment = line[len(lane) + 1:].partition("#")
        first, second, *rest = body.split("|")
        swapped = f"{lane}:{second.rstrip()} | {first.strip()} |{'|'.join(rest)}" + (f"#{comment}" if comment else "")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing.yaml"
            path.write_text(text.replace(line, swapped), encoding="utf-8")
            reordered = routing_lanes(path)
        self.assertNotEqual(reordered[lane], self.routing[lane])
        self.assertEqual(
            [problem.split(":")[0] for problem in mismatches(reordered, self.doc)],
            [f"{lane} first choice", f"{lane} order"],
        )


if __name__ == "__main__":
    unittest.main()
