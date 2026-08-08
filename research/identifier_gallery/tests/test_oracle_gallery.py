from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backgammon_research import oracle_gallery as og


class FakeSurface:
    def __init__(self, name: str): self.name = name
    def xgid_to_gnuid(self, value: str) -> str: return "PAAAAAAAAAAAAA:cAkAAAAAAAAE"
    def gnuid_to_xgid(self, value: str) -> str: return "XGID=--------------------------:0:0:1:00:0:0:0:0:10"


class FakeCalculator:
    provenance = {"release_commit": "a385a963ed01a6eac083dae7a1b246b1c150b3eb", "package_version": "0.2.0"}
    def xgid_to_gnuid(self, value: str): return {"gnuid": "PAAAAAAAAAAAAA:cAkAAAAAAAAE"}
    def gnuid_to_xgid(self, value: str): return {"xgid": "XGID=--------------------------:0:0:1:00:0:0:0:0:10"}


class FakeBglab:
    provenance = {"remote_sha": "bglab-sha"}
    def convert(self, value: str): return {"xgid": "XGID=--------------------------:0:0:1:00:0:0:0:0:10"}


class FakeGnu:
    provenance = {"executable": "gnubg-cli"}
    def load(self, identifier: str, scratch: Path, name: str):
        return {"complete_gnuid": "PAAAAAAAAAAAAA:cAkAAAAAAAAE", "exported_text": "GNU board evidence", "rawboard": "board:fake"}


class FakeRenderer:
    provenance = {"remote_sha": "a4ab56f712c9ecb8e8ad83782cc82d5b32d94883"}
    def render(self, xgid: str, output_dir: Path, name: str):
        return {"output": "<svg><text>current board</text></svg>", "remote_sha": self.provenance["remote_sha"]}


def fake_canonical(identifier: str):
    return {"board": {"player_0": {"points": [], "bar": 0, "off": 15}, "player_1": {"points": [], "bar": 0, "off": 15}}, "state": {"on_roll": "player_1", "dice": None}, "cube": {"value": 1, "owner": None}, "score": {"player_0": 0, "player_1": 0, "match_length": 0}, "rules": {"maximum_cube": 1024}}


class FullGalleryTests(unittest.TestCase):
    def test_case_matrix_has_twelve_edge_cases(self):
        cases = og.load_cases(Path(__file__).parents[1] / "fixtures" / "cases.csv")
        self.assertEqual(len(cases), 12)
        ids = {x.case_id for x in cases}
        self.assertIn("known-top-roller", ids)
        self.assertIn("both-bars-off", ids)
        self.assertIn("crawford", ids)

    def test_full_gallery_restores_discussed_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = root / "cases.csv"
            with cases.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["case_id", "label", "xgid", "gnuid"])
                writer.writeheader()
                writer.writerow({"case_id": "demo", "label": "Demo", "xgid": "XGID=--------------------------:0:0:1:00:0:0:0:0:10", "gnuid": "PAAAAAAAAAAAAA:cAkAAAAAAAAE"})
            with patch.object(og, "NativeSurface", lambda: FakeSurface("native_python")), patch.object(og, "BridgeSurface", lambda: FakeSurface("engine_kit")), patch.object(og, "AnkiSurface", lambda: FakeSurface("ankigammon_direct")), patch.object(og, "_canonical", fake_canonical), patch.object(og, "_bek", lambda: type("B", (), {"__file__": __file__})()):
                report = og.build_gallery(cases_path=cases, output_dir=root / "out", r_library=root, calculator=FakeCalculator(), bglab=FakeBglab(), gnu=FakeGnu(), renderer=FakeRenderer())
            page = (root / "out" / "oracle-gallery.html").read_text(encoding="utf-8")
            for marker in ["Reference: backgammoncalculator 0.2.0", "XGID → GNUID → XGID", "GNUID → XGID → GNUID", "Native Python", "Engine Kit public API", "Direct AnkiGammon", "Diagnostic: R bglab", "current board"]:
                self.assertIn(marker, page)
            self.assertEqual(report["schema"], "stable-player-oracle-first-gallery-v3")

    def test_renderer_provenance_is_exact_current_source_sha(self):
        from backgammon_research.renderer import EXPECTED_COMMIT
        self.assertEqual(EXPECTED_COMMIT, "a4ab56f712c9ecb8e8ad83782cc82d5b32d94883")


if __name__ == "__main__":
    unittest.main()
