"""Every lane table in the READMEs and the skill agrees with routing.yaml, entry by entry.

Spellings read from the documents:
- README.md, README.zh-CN.md, README.ja.md, README.ko.md: `GPT-6 Astra (xhigh)`,
  `Gemini 4 Argon (High)` (a Gemini thinking suffix is written as the effort),
  `Claude Haiku 4.5`, `Kimi Code K3`, `Qwen3.8 Max`, `OpenCode`, `off`.
- README.zh-TW.md: the same names with full-width brackets, `GPT-6 Astra （xhigh）`,
  and the switched-off lane as `` `off`（選配模型評審團） ``.
- skills/omnilane/SKILL.md: lane names only; chains are deliberately absent.
"""
import offline_env  # Activate suite isolation for direct file execution.
import re
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
ROUTING = REPO / "routing.yaml"
SKILL = REPO / "skills" / "omnilane" / "SKILL.md"
READMES = ["README.md", "README.zh-TW.md", "README.zh-CN.md", "README.ja.md", "README.ko.md"]
# How each document says a lane is switched off until the operator enables it.
OPT_IN_MARKERS = {"README.zh-TW.md": "選配", "SKILL.md": "Disabled by default"}
DEFAULT_OPT_IN_MARKER = "opt-in"


def routing_chains(text: str) -> Dict[str, List[str]]:
    """Lane -> candidates as written in routing.yaml, e.g. 'claude claude-opus-5-5 medium'."""
    chains = {}
    for line in text.splitlines():
        match = re.match(r"^([a-z][a-z0-9-]*):\s*(.+)$", line.split("#")[0].rstrip())
        if match:
            chains[match.group(1)] = [" ".join(seg.split()) for seg in match.group(2).split("|")]
    return chains


def is_switched_off(chain: List[str]) -> bool:
    return all(segment.split()[0] == "off" for segment in chain)


def canon(text: str) -> str:
    return re.sub(r"[\s./]+", "-", text.strip().lower())


def routing_key(segment: str) -> str:
    vendor, model, effort = (segment.split() + ["-", "-"])[:3]
    if vendor == "off":
        return "off"
    name = vendor if model == "-" else model
    return canon(name if effort == "-" else f"{name} {effort}")


def doc_key(entry: str) -> str:
    entry = entry.replace("`", "").replace("（", "(").replace("）", ")").strip()
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", entry)
    return canon(f"{match.group(1)} {match.group(2)}" if match else entry)


def lane_table_rows(text: str, lanes) -> List[Tuple[str, List[str], str]]:
    """(lane, cells, line) for the body rows of every 4-column table that names a routing lane."""
    tables, current = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    found = []
    for table in tables:
        body = []
        for line in table[2:]:  # skip the header and the |---| separator
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 4:
                body.append((re.sub(r"^[^a-z]+", "", cells[0]), cells, line))
        if any(lane in lanes for lane, _, _ in body):
            found.extend(body)
    return found


def readme_problems(name: str, text: str, routing: Dict[str, List[str]]) -> List[str]:
    marker = OPT_IN_MARKERS.get(name, DEFAULT_OPT_IN_MARKER)
    rows = {}
    problems = []
    for lane, cells, line in lane_table_rows(text, routing):
        if lane not in routing:
            problems.append(f"{name}: lane {lane}: row has no lane in routing.yaml")
            continue
        backup = [] if cells[2] in ("—", "-", "") else [item.strip() for item in cells[2].split("→")]
        rows[lane] = (cells[1], backup, line)
    for lane, chain in routing.items():
        if lane not in rows:
            problems.append(f"{name}: lane {lane}: no row in the lane table")
            continue
        first, backup, row = rows[lane]
        if is_switched_off(chain):
            if not first.replace("`", "").startswith("off"):
                problems.append(f"{name}: lane {lane}: first choice {first!r}, routing.yaml says off")
            if marker not in row:
                problems.append(f"{name}: lane {lane}: row does not call the lane {marker!r}")
            if backup:
                problems.append(f"{name}: lane {lane}: backup {backup!r}, routing.yaml has none")
            continue
        if doc_key(first) != routing_key(chain[0]):
            problems.append(
                f"{name}: lane {lane}: first choice {first!r}, routing.yaml {chain[0]!r}"
            )
        want, got = chain[1:], backup
        for index in range(max(len(want), len(got))):
            doc_entry = got[index] if index < len(got) else None
            yaml_entry = want[index] if index < len(want) else None
            if doc_entry is None or yaml_entry is None or doc_key(doc_entry) != routing_key(yaml_entry):
                problems.append(
                    f"{name}: lane {lane}: backup #{index + 1} is {doc_entry!r}, routing.yaml {yaml_entry!r}"
                )
                break
    return problems


def skill_problems(text: str, routing: Dict[str, List[str]]) -> List[str]:
    rows, inside = {}, False
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells == ["Lane", "Use for"]:
            inside = True
        elif inside and not line.startswith("|"):
            break
        elif inside and not set(cells[0]) <= set("-"):
            rows[cells[0]] = line
    problems = []
    for lane in sorted(set(rows) - set(routing)):
        problems.append(f"SKILL.md: lane {lane}: row has no lane in routing.yaml")
    marker = OPT_IN_MARKERS["SKILL.md"]
    for lane, chain in routing.items():
        if lane not in rows:
            problems.append(f"SKILL.md: lane {lane}: no row in the lane table")
        elif is_switched_off(chain) and marker not in rows[lane]:
            problems.append(f"SKILL.md: lane {lane}: row does not say {marker!r}")
    return problems


class DocLaneSyncTests(unittest.TestCase):
    def setUp(self):
        self.routing = routing_chains(ROUTING.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(self.routing), 10, "routing.yaml: lanes not found")

    def test_readme_tables_match_routing(self):
        for name in READMES:
            with self.subTest(document=name):
                problems = readme_problems(name, (REPO / name).read_text(encoding="utf-8"), self.routing)
                self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_skill_table_matches_routing(self):
        problems = skill_problems(SKILL.read_text(encoding="utf-8"), self.routing)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_every_document_spelling_maps_to_its_routing_entry(self):
        pairs = {
            "GPT-6 Astra (xhigh)": "codex gpt-6-astra xhigh",
            "GPT-6.1 Sol （medium）": "codex gpt-6.1-sol medium",
            "Claude Opus 5.5 (low)": "claude claude-opus-5-5 low",
            "Claude Haiku 4.5": "claude claude-haiku-4-5 -",
            "Gemini 4 Argon （High）": "gemini gemini-4-argon-high -",
            "Grok 4.7 (high)": "grok grok-4.7 high",
            "Kimi Code K3": "kimi kimi-code/k3 -",
            "Qwen3.8 Max": "qwen qwen3.8-max -",
            "OpenCode": "opencode - -",
            "off": "off",
        }
        for doc_entry, segment in pairs.items():
            self.assertEqual(doc_key(doc_entry), routing_key(segment), doc_entry)
        self.assertNotEqual(doc_key("Grok 4.7"), routing_key("grok grok-4.7 high"))
        self.assertNotEqual(doc_key("Claude Opus 5 (low)"), routing_key("claude claude-opus-5-5 low"))

    def test_reports_document_lane_and_entry(self):
        routing = routing_chains(
            "fast: codex gpt-6.1-sol medium | claude claude-opus-5-5 low | off\n"
            "panel: off - -\n"
        )
        table = (
            "| Lane | First choice | Backup | When |\n|---|---|---|---|\n"
            "| fast | GPT-6.1 Sol (medium) | Claude Opus 5 (low) → off | x |\n"
            "| panel | off | — | x |\n"
            "| gone | GPT-6 Sol (low) | off | x |\n"
        )
        problems = readme_problems("README.ko.md", table, routing)
        self.assertIn(
            "README.ko.md: lane fast: backup #1 is 'Claude Opus 5 (low)', "
            "routing.yaml 'claude claude-opus-5-5 low'",
            problems,
        )
        self.assertIn("README.ko.md: lane panel: row does not call the lane 'opt-in'", problems)
        self.assertIn("README.ko.md: lane gone: row has no lane in routing.yaml", problems)

    def test_reports_missing_and_extra_backup_entries(self):
        routing = routing_chains("fast: codex gpt-6.1-sol medium | codex gpt-6-sol low | off\n")
        short = (
            "| Lane | First choice | Backup | When |\n|---|---|---|---|\n"
            "| fast | GPT-6.1 Sol (medium) | GPT-6 Sol (low) | x |\n"
        )
        long = (
            "| Lane | First choice | Backup | When |\n|---|---|---|---|\n"
            "| fast | GPT-6.1 Sol (medium) | GPT-6 Sol (low) → off → OpenCode | x |\n"
        )
        self.assertEqual(
            readme_problems("README.md", short, routing),
            ["README.md: lane fast: backup #2 is None, routing.yaml 'off'"],
        )
        self.assertEqual(
            readme_problems("README.md", long, routing),
            ["README.md: lane fast: backup #3 is 'OpenCode', routing.yaml None"],
        )

    def test_skill_reports_switched_off_lane_without_marker(self):
        routing = routing_chains("panel: off - -\n")
        table = "| Lane | Use for |\n|---|---|\n| panel | Big calls |\n"
        self.assertEqual(
            skill_problems(table, routing),
            ["SKILL.md: lane panel: row does not say 'Disabled by default'"],
        )


if __name__ == "__main__":
    unittest.main()
