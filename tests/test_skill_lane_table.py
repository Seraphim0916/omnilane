"""The skill's lane table names every routing.yaml lane and no model; READMEs match routing.yaml."""
import offline_env  # Activate suite isolation for direct file execution.
from itertools import zip_longest
import re
import unittest
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "omnilane" / "SKILL.md"
ROUTING = REPO / "routing.yaml"
READMES = {
    "README.md": (("Lane", "First choice", "Backup"), "opt-in"),
    "README.zh-TW.md": (("通道", "首選模型", "備選模型"), "選配"),
    "README.zh-CN.md": (("通道", "首选模型", "备选模型"), "opt-in"),
    "README.ja.md": (("レーン", "第一候補", "バックアップ"), "opt-in"),
    "README.ko.md": (("레인", "1순위 모델", "백업"), "opt-in"),
}
LANE = r"[a-z][a-z0-9-]*"
NO_BACKUPS = {"", "-", "—", "–"}


def routing_chains(text: Optional[str] = None) -> dict:
    chains = {}
    if text is None:
        text = ROUTING.read_text(encoding="utf-8")
    for line in text.splitlines():
        match = re.match(r"^([a-z][a-z0-9-]*):\s*(.+)$", line.split("#")[0].rstrip())
        if match:
            chains[match.group(1)] = [segment.split() for segment in match.group(2).split("|")]
    return chains


def key(text: str) -> str:
    """One spelling for 'claude claude-opus-5-5 medium' and 'Claude Opus 5.5 (medium)'."""
    text = text.lower().replace("claude", "")
    text = re.sub(r"^(codex|grok|gemini|kimi|qwen|opencode)\s+(?=(gpt|grok|gemini|kimi|qwen|-))", "", text.strip())
    text = re.sub(r"[^a-z0-9]", "", text)
    return text or "opencode"


def table_chains(name: str, text: str) -> dict:
    """Read only the shipped-chain table; ignore historical snapshots and prose."""
    header, _ = READMES[name]
    lines = text.splitlines()
    headers = [
        index for index, line in enumerate(lines)
        if line.lstrip().startswith("|")
        and tuple(cell.strip() for cell in line.strip().strip("|").split("|")[:3]) == header
    ]
    if len(headers) != 1:
        raise AssertionError(f"{name}: expected one current lane table, found {len(headers)}")
    chains = {}
    for line in lines[headers[0] + 1:]:
        if not line.lstrip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        lane = re.sub(r"^[^a-z]+", "", cells[0])
        if len(cells) < 4 or not re.fullmatch(LANE, lane):
            raise AssertionError(f"{name}: malformed current lane row {line!r}")
        if lane in chains:
            raise AssertionError(f"{name}: duplicate lane {lane}")
        backups = [] if cells[2] in NO_BACKUPS else [item.strip() for item in cells[2].split("→")]
        chains[lane] = [cells[1], *backups]
    return chains


def is_off(segments: list) -> bool:
    return bool(segments) and all(segment in (["off"], ["off", "-", "-"]) for segment in segments)


def skill_lane_rows(text: Optional[str] = None) -> dict:
    """Rows of the skill's `| Lane | Use for |` table, keyed by lane."""
    rows, inside = {}, False
    if text is None:
        text = SKILL.read_text(encoding="utf-8")
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells == ["Lane", "Use for"]:
            inside = True
        elif inside and not line.startswith("|"):
            break
        elif inside and not set(cells[0]) <= set("-"):
            rows[cells[0]] = line
    return rows


def flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class SkillLaneTableTests(unittest.TestCase):
    def assert_skill_lanes_match(self, routing: dict, rows: dict):
        expected, actual = set(routing), set(rows)
        self.assertEqual(
            expected, actual,
            "SKILL.md lane mismatch: missing lanes: {}; extra lanes: {}".format(
                sorted(expected - actual), sorted(actual - expected)
            ),
        )

    def test_skill_table_names_every_routing_lane(self):
        rows = skill_lane_rows()
        self.assertGreaterEqual(len(rows), 10, "SKILL.md: lane table not found")
        self.assert_skill_lanes_match(routing_chains(), rows)

    def test_detects_digit_lane_missing_from_skill_table(self):
        routing = routing_chains("review-lane: off - -\nreview-lane-2: off - -\n")
        rows = skill_lane_rows("| Lane | Use for |\n|---|---|\n| review-lane | Review |\n")
        with self.assertRaisesRegex(AssertionError, r"missing lanes: \['review-lane-2'\]"):
            self.assert_skill_lanes_match(routing, rows)

    def test_detects_extra_lane_in_skill_table(self):
        routing = routing_chains("review-lane: off - -\n")
        rows = skill_lane_rows(
            "| Lane | Use for |\n|---|---|\n"
            "| review-lane | Review |\n| obsolete-lane | Obsolete |\n"
        )
        with self.assertRaisesRegex(AssertionError, r"extra lanes: \['obsolete-lane'\]"):
            self.assert_skill_lanes_match(routing, rows)

    def test_skill_table_carries_no_model_names(self):
        # Chains live in routing.yaml and `omnilane route --explain`; a copy in the skill goes stale.
        models = set()
        for segments in routing_chains().values():
            for segment in segments:
                if len(segment) > 1 and segment[1] != "-":
                    models.add(flat(segment[1]))
                    models.add(flat(re.sub(r"-(low|medium|high)$", "", segment[1])))
        self.assertIn("claudeopus55", models)
        for lane, row in skill_lane_rows().items():
            for model in models:
                self.assertNotIn(model, flat(row), f"SKILL.md: lane {lane} names model {model}")

    def assert_table_matches(self, name: str, text: str, routing: dict):
        table = table_chains(name, text)
        self.assertEqual(set(routing), set(table), (
            f"{name}: lane mismatch: missing lanes: {sorted(set(routing) - set(table))}; "
            f"extra lanes: {sorted(set(table) - set(routing))}"
        ))
        for lane, segments in routing.items():
            first, *backups = table[lane]
            location = f"{name}: lane {lane}"
            if is_off(segments):
                self.assertRegex(first, r"^`?off`?(?:\s|[（(]|$)", f"{location}: expected off")
                self.assertIn(READMES[name][1], first, f"{location}: missing opt-in wording")
                self.assertFalse(backups, f"{location}: disabled lane must not have backups")
                continue
            for index, (got, want) in enumerate(zip_longest([first, *backups], segments)):
                position = "first choice" if index == 0 else f"backup #{index}"
                goal = " ".join(want) if want is not None else None
                self.assertTrue(
                    bool(got) and goal is not None and key(got) == key(goal),
                    f"{location}: {position}: README {got!r}, routing.yaml {goal!r}",
                )

    def test_readme_tables_match_routing(self):
        routing = routing_chains()
        for name in READMES:
            with self.subTest(readme=name):
                self.assert_table_matches(name, (REPO / name).read_text(encoding="utf-8"), routing)

    def assert_disabled_skill_lanes_marked(self, routing: dict, rows: dict):
        for lane, segments in routing.items():
            if is_off(segments):
                self.assertIn("Disabled by default", rows.get(lane, ""),
                              f"SKILL.md: lane {lane} must say Disabled by default")

    def test_skill_marks_disabled_routing_lanes(self):
        self.assert_disabled_skill_lanes_marked(routing_chains(), skill_lane_rows())
        with self.assertRaisesRegex(AssertionError, "lane panel must say Disabled by default"):
            self.assert_disabled_skill_lanes_marked(
                routing_chains("panel: off - -"), {"panel": "| panel | Opinion panel |"})

    def test_readme_mutations_report_the_first_differing_position(self):
        routing = routing_chains("search: grok grok-4.7 high | gemini gemini-4-argon-high - | off")
        header = "| Lane | First choice | Backup | When |\n|---|---|---|---|\n"
        for first, backups, position in (
            ("Grok 4.7", "Gemini 4 Argon (High) → off", "first choice"),
            ("Grok 4.7 (high)", "Gemini 4 Argon (Low) → off", "backup #1"),
            ("Grok 4.7 (high)", "off → Gemini 4 Argon (High)", "backup #1"),
            ("Grok 4.7 (high)", "Gemini 4 Argon (High)", "backup #2"),
            ("Grok 4.7 (high)", "Gemini 4 Argon (High) → off → off", "backup #3"),
        ):
            with self.subTest(first=first, backups=backups):
                with self.assertRaisesRegex(AssertionError, f"README.md: lane search: {position}"):
                    self.assert_table_matches(
                        "README.md", header + f"| search | {first} | {backups} | Search |\n", routing)

    def test_readme_mutations_check_table_boundaries_lane_set_and_opt_in(self):
        routing = routing_chains("panel: off - - | off")
        header = "| 通道 | 首選模型 | 備選模型 | 用途 |\n|---|---|---|---|\n"
        row = "| 🗳 panel | `off`（選配模型評審團） | — | Note |\n"
        historical = "| old-lane | A | B → C | This is outside the table |\n"
        self.assert_table_matches("README.zh-TW.md", historical + "\n" + header + row, routing)
        for text, error in (
            (historical, "expected one current lane table"),
            (header, "missing lanes"),
            (header + row + row, "duplicate lane panel"),
            (header + row + historical, "extra lanes"),
            (header + row.replace("選配", ""), "missing opt-in wording"),
            (header + row.replace("| — |", "| extra |"), "must not have backups"),
            (header + row.replace("`off`", "active"), "expected off"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(AssertionError, error):
                    self.assert_table_matches("README.zh-TW.md", text, routing)


if __name__ == "__main__":
    unittest.main()
