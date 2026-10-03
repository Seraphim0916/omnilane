"""The lane table an agent reads in the skill matches routing.yaml, candidate for candidate."""
import offline_env  # Activate suite isolation for direct file execution.
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "omnilane" / "SKILL.md"
ROUTING = REPO / "routing.yaml"
READMES = sorted(REPO.glob("README*.md"))


def routing_chains() -> dict:
    chains = {}
    for line in ROUTING.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-z][a-z-]*):\s*(.+)$", line.split("#")[0].rstrip())
        if match:
            chains[match.group(1)] = [segment.split() for segment in match.group(2).split("|")]
    return chains


def key(text: str) -> str:
    """One spelling for 'claude claude-opus-5-5 medium' and 'Claude Opus 5.5 (medium)'."""
    text = text.lower().replace("claude", "")
    text = re.sub(r"^(codex|grok|gemini|kimi|qwen|opencode)\s+(?=(gpt|grok|gemini|kimi|qwen|-))", "", text.strip())
    text = re.sub(r"[^a-z0-9]", "", text)
    text = re.sub(r"^(grok\d+)high$", r"\1", text)  # the table names a Grok release without its effort
    return text or "opencode"


def table_chains(path: Path) -> dict:
    chains = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        lane = re.sub(r"^[^a-z]+", "", cells[0])
        if re.fullmatch(r"[a-z][a-z-]*", lane) and "→" in cells[2] or lane in ("consult",):
            chains[lane] = [cells[1]] + [item.strip() for item in cells[2].split("→")]
    return chains


class SkillLaneTableTests(unittest.TestCase):
    def assert_table_matches(self, path: Path):
        routing = routing_chains()
        table = table_chains(path)
        compared = 0
        for lane, segments in routing.items():
            if segments[0][0] in ("off", "vote") and len(segments) == 1:
                continue  # a switched-off lane is described in prose, not as a chain
            self.assertIn(lane, table, f"{path.name} has no row for {lane}")
            want = [key(" ".join(segment)) for segment in segments]
            got = [key(item) for item in table[lane]]
            self.assertEqual(got, want, f"{path.name}: lane {lane} differs from routing.yaml")
            compared += 1
        self.assertGreaterEqual(compared, 10, f"{path.name}: lane table not found")

    def test_skill_table_matches_routing(self):
        self.assert_table_matches(SKILL)

    def test_readme_tables_match_routing(self):
        checked = 0
        for path in READMES:
            if len(table_chains(path)) >= 10:
                self.assert_table_matches(path)
                checked += 1
        self.assertGreaterEqual(checked, 1)


if __name__ == "__main__":
    unittest.main()
