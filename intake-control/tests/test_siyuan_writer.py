from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.write_siyuan import resolve_target_folder


class SiYuanWriterTests(unittest.TestCase):
    def test_resolve_target_folder_uses_taxonomy_decision_for_formal_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "taxonomy_decision.json").write_text(json.dumps({
                "schema_name": "TaxonomyDecisionV1",
                "recommended_path": ["GSAP"],
                "confidence": "high",
            }, ensure_ascii=False), encoding="utf-8")

            folder, decision = resolve_target_folder({
                "siyuan_use_taxonomy_path": True,
                "siyuan_taxonomy_root_path": "知识卡",
                "inbox_path": "00_Inbox/临时收集箱",
            }, job_dir)

            self.assertEqual(folder, "知识卡/GSAP")
            self.assertEqual(decision["recommended_path"], ["GSAP"])

    def test_resolve_target_folder_falls_back_to_inbox_without_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder, decision = resolve_target_folder({
                "inbox_path": "00_Inbox/临时收集箱",
            }, Path(tmp))

            self.assertEqual(folder, "00_Inbox/临时收集箱")
            self.assertEqual(decision, {})

    def test_resolve_target_folder_ignores_stale_taxonomy_when_gate_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "quality_gate.json").write_text(json.dumps({
                "quality_gate_passed": False,
                "final_card_type": "temporary_review_card",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "taxonomy_decision.json").write_text(json.dumps({
                "schema_name": "TaxonomyDecisionV1",
                "recommended_path": ["GSAP"],
                "confidence": "high",
            }, ensure_ascii=False), encoding="utf-8")

            folder, decision = resolve_target_folder({
                "inbox_path": "00_Inbox/临时收集箱",
                "siyuan_use_taxonomy_path": True,
            }, job_dir)

            self.assertEqual(folder, "00_Inbox/临时收集箱")
            self.assertEqual(decision, {})


if __name__ == "__main__":
    unittest.main()
