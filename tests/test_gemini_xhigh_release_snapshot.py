"""Keep the Gemini XHigh correction aligned across the approved 0.63.5 snapshot and docs."""
import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID = "aa-v5.0.1-2026-11-19-v2"
APPROVAL_BASIS = "Vincent 2026-11-19：AA基準有更新就更新／然後我核准直接做到上線"


class GeminiXHighReleaseSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / "config/aa-model-policy.json").read_text(encoding="utf-8"))

    def test_approved_snapshot_records_operator_basis(self):
        snapshot = self.policy["snapshot"]
        self.assertEqual(snapshot["id"], SNAPSHOT_ID)
        self.assertEqual(snapshot["approval"]["status"], "approved")
        self.assertEqual(snapshot["approval"]["basis"], APPROVAL_BASIS)
        report = (ROOT / "docs/reports/aa-gemini-evidence-2026-11-19-v2.md").read_text(encoding="utf-8")
        self.assertIn(APPROVAL_BASIS, report)

    def test_final_snapshot_counts_and_comparison_only_xhigh(self):
        scored = self.policy["scored_configs"]
        reference = self.policy["reference_configs"]
        aliases = self.policy["aliases"]
        self.assertEqual(sum(not row.get("estimated", False) for row in scored), 88)
        self.assertEqual(sum(bool(row.get("estimated", False)) for row in scored), 22)
        self.assertEqual(len(reference), 3)
        self.assertEqual(len(aliases), 68)
        for model in ("gemini-4-hydrogen", "gemini-4-neon"):
            self.assertFalse(any(row.get("vendor") == "gemini" and row.get("model") == model
                                 and row.get("effort") == "xhigh" for row in scored))
            self.assertTrue(any(row.get("vendor") == "gemini" and row.get("model") == model
                                and row.get("effort") == "xhigh" for row in reference))
        serialized_aliases = json.dumps(aliases, ensure_ascii=False)
        self.assertNotIn("gemini-4-hydrogen-xhigh", serialized_aliases)
        self.assertNotIn("gemini-4-neon-xhigh", serialized_aliases)

    def test_xhigh_correction_belongs_to_0635_and_links_match(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", 1)[1].split("## [0.63.5]", 1)[0]
        release = changelog.split("## [0.63.5]", 1)[1].split("## [0.63.4]", 1)[0]
        self.assertNotRegex(unreleased, re.compile(r"Hydrogen.*XHigh|Neon.*XHigh", re.IGNORECASE))
        self.assertIn("88 列實測", release)
        self.assertIn("22 列推導", release)
        self.assertIn("68 個別名", release)
        self.assertIn("3 列比較資料", release)
        self.assertRegex(release, re.compile(r"Hydrogen.*Neon.*XHigh|Neon.*Hydrogen.*XHigh", re.IGNORECASE))
        self.assertIn("[Unreleased]: https://github.com/Seraphim0916/omnilane/compare/v0.63.5...HEAD", changelog)
        self.assertIn("[0.63.5]: https://github.com/Seraphim0916/omnilane/compare/v0.63.4...v0.63.5", changelog)

    def test_current_snapshot_documentation_points_to_v2(self):
        skill = (ROOT / "skills/omnilane/SKILL.md").read_text(encoding="utf-8")
        capability = (ROOT / "docs/model-capabilities-2026-09.md").read_text(encoding="utf-8")
        coverage = json.loads((ROOT / "docs/aa-model-coverage-2026-09-05.json").read_text(encoding="utf-8"))
        self.assertTrue(SNAPSHOT_ID in skill, "skill current snapshot must point to v2")
        skill_section = skill.split("## Notes for your main model", 1)[1].split("\n## ", 1)[0]
        ceiling_source = next((part.strip().replace("\n", " ") for part in re.split(r"\n\s*\n", skill_section)
                               if "Ceilings below are the measured AA v5.0.1 scores" in part), "")
        self.assertTrue(ceiling_source, "skill caller score-ceiling source paragraph must exist")
        self.assertIn(SNAPSHOT_ID, ceiling_source, "skill caller score-ceiling source must point to v2")
        self.assertIn("88 列實測、22 列推導分數、68 個別名、3 列比較", skill)
        self.assertIn("Gemini XHigh transport correction (approved", capability)
        self.assertIn(SNAPSHOT_ID, capability)
        self.assertIn("上一份快照", capability)
        snapshot_sections = re.split(r"(?m)^## ", capability)
        capability_section = next((part for part in snapshot_sections
                                   if part.startswith("Snapshot — 2026-11-13 ")), "")
        self.assertTrue(capability_section, "2026-11-13 Hydrogen snapshot section must remain present")
        capability_lines = capability_section.splitlines()
        lane_heading = next(index for index, line in enumerate(capability_lines)
                            if line.strip() == "### Current lane measurements")
        lane_source = next((line.strip() for line in capability_lines[lane_heading + 1:]
                            if line.strip() and not line.lstrip().startswith("|")), "")
        self.assertTrue(lane_source, "current lane-measurement source paragraph must exist")
        self.assertIn(SNAPSHOT_ID, lane_source, "current lane-measurement source must point to v2")
        reasons = [row.get("mappingReason", "") for row in coverage["catalogMappings"]]
        current = [reason for reason in reasons if "aa-v5.0.1-2026-11-19-" in reason]
        self.assertTrue(current)
        self.assertTrue(all(SNAPSHOT_ID in reason for reason in current))
        self.assertFalse(any("aa-v5.0.1-2026-11-19-v1" in reason for reason in current))

    def test_five_readmes_scope_historical_xhigh_and_current_v2_lanes(self):
        headings = {
            "README.md": "## What's new in v0.63.5",
            "README.zh-TW.md": "## v0.63.5 新功能",
            "README.zh-CN.md": "## v0.63.5 新功能",
            "README.ja.md": "## v0.63.5 の新機能",
            "README.ko.md": "## v0.63.5 새 기능",
        }
        comparison_words = {
            "README.md": "comparison",
            "README.zh-TW.md": "比較",
            "README.zh-CN.md": "比较",
            "README.ja.md": "比較",
            "README.ko.md": "비교",
        }
        table_headers = {
            "README.md": "| Lane | First choice | Backup | When |",
            "README.zh-TW.md": "| 通道 | 首選模型 | 備選模型 | 用途 |",
            "README.zh-CN.md": "| 通道 | 首选模型 | 备选模型 | 用途 |",
            "README.ja.md": "| レーン | 第一候補 | バックアップ | 用途 |",
            "README.ko.md": "| 레인 | 1순위 모델 | 백업 | 용도 |",
        }
        historical_markers = {
            "README.md": ("comparison-only", "not dispatchable", "historical scores are retained"),
            "README.zh-TW.md": ("僅供比較", "不可派工", "歷史分數保留"),
            "README.zh-CN.md": ("仅供比较", "不可派工", "历史分数保留"),
            "README.ja.md": ("比較専用", "派遣対象外", "過去のスコアは保持"),
            "README.ko.md": ("비교 전용", "디스패치할 수 없습니다", "과거 점수는 유지"),
        }
        current_markers = {
            "README.md": ("Hydrogen XHigh", "Neon XHigh", "comparison-only", "not dispatchable"),
            "README.zh-TW.md": ("Hydrogen XHigh", "Neon XHigh", "僅供比較", "不可派工"),
            "README.zh-CN.md": ("Hydrogen XHigh", "Neon XHigh", "仅供比较", "不可派工"),
            "README.ja.md": ("Hydrogen XHigh", "Neon XHigh", "比較専用", "派遣対象外"),
            "README.ko.md": ("Hydrogen XHigh", "Neon XHigh", "비교 전용", "디스패치할 수 없습니다"),
        }
        historical_snapshot = "aa-v5.0.1-2026-11-13-v1"
        for relative, heading in headings.items():
            with self.subTest(readme=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertTrue(heading in text, "0.63.5 README heading must remain present")
                section = text.split(heading, 1)[1].split("\n## ", 1)[0]
                self.assertIn("Hydrogen", section)
                self.assertIn("Neon", section)
                self.assertIn("XHigh", section)
                self.assertIn(comparison_words[relative], section)
                paragraphs = [part.strip().replace("\n", " ") for part in re.split(r"\n\s*\n", text) if part.strip()]
                historical = next((part for part in paragraphs
                                   if historical_snapshot in part and "gemini-4-hydrogen-xhigh" in part), "")
                self.assertTrue(historical, "the 2026-11-13 snapshot must remain a distinct historical paragraph")
                self.assertIn("2026-11-13", historical)
                self.assertIn("gemini-4-hydrogen-xhigh", historical)
                lines = text.splitlines()
                table_index = next(index for index, line in enumerate(lines) if table_headers[relative] in line)
                paragraph_lines = []
                for line in reversed(lines[:table_index]):
                    if not line.strip():
                        if paragraph_lines:
                            break
                        continue
                    paragraph_lines.append(line.strip())
                current = " ".join(reversed(paragraph_lines))
                self.assertIn(SNAPSHOT_ID, current)
                self.assertNotIn("aa-v5.0.1-2026-11-19-v1", current)
                self.assertNotIn("gemini-4-hydrogen-xhigh", current)
                self.assertNotIn("gemini-4-neon-xhigh", current)
                for marker in current_markers[relative]:
                    self.assertIn(marker, current, "current lane preamble must make both XHigh restrictions explicit")
                for marker in historical_markers[relative]:
                    self.assertIn(marker, historical)

                table_lines = []
                for line in lines[table_index:]:
                    if line.startswith("|"):
                        table_lines.append(line)
                    elif table_lines and line.strip():
                        break
                self.assertTrue(table_lines, "the current lane table must follow the v2 preamble")
                current_table = "\n".join(table_lines)
                self.assertNotIn("gemini-4-hydrogen-xhigh", current_table)
                self.assertNotIn("gemini-4-neon-xhigh", current_table)
                self.assertNotRegex(current_table, re.compile(r"(?:Hydrogen|Neon)\s*[（(]\s*xhigh\s*[）)]", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
