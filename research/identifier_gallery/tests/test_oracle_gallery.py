from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backgammon_research import oracle_gallery as og


CURRENT_BOARD_SHA = "0bc70d30e458642f41d4976948e49492c2c6117c"


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
    provenance = {
        "remote_sha": CURRENT_BOARD_SHA,
        "resolved_commit": CURRENT_BOARD_SHA,
        "package_version": "0.1.1",
        "color_preset": "bs",
        "style_preset": "bs",
    }
    def render(self, xgid: str, output_dir: Path, name: str):
        return {"output": "<svg><text>current BS board</text></svg>", **self.provenance}


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

    def test_full_gallery_restores_three_column_visual_contract(self):
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
            for marker in [
                "Reference: backgammoncalculator 0.2.0",
                "XGID → GNUID → XGID",
                "GNUID → XGID → GNUID",
                "Native Python",
                "Engine Kit public API",
                "Direct AnkiGammon",
                "Diagnostic: R bglab",
                "current BS board",
                'data-layout="three-method-columns"',
                "GNU CLI render of method GNUID",
                "backgammonboard round-trip XGID",
                "Canonical representation",
                "BS colors/style",
            ]:
                self.assertIn(marker, page)
            self.assertLess(page.index("GNU CLI render of method GNUID"), page.index("backgammonboard round-trip XGID"))
            self.assertLess(page.index("backgammonboard round-trip XGID"), page.index("Canonical representation"))
            self.assertEqual(report["schema"], "stable-player-oracle-first-gallery-v3")

    def test_renderer_provenance_is_current_bs_board_target(self):
        from backgammon_research.renderer import (
            COLOR_PRESET,
            EXPECTED_COMMIT,
            EXPECTED_VERSION,
            PERSPECTIVE,
            REQUIRED_PUBLIC_API,
            STYLE_PRESET,
        )
        self.assertEqual(EXPECTED_COMMIT, CURRENT_BOARD_SHA)
        self.assertEqual(EXPECTED_VERSION, "0.1.1")
        self.assertEqual(COLOR_PRESET, "bs")
        self.assertEqual(STYLE_PRESET, "bs")
        self.assertEqual(PERSPECTIVE, "player_1")
        self.assertIn("board_colors", REQUIRED_PUBLIC_API)
        self.assertIn("board_style", REQUIRED_PUBLIC_API)


if __name__ == "__main__":
    unittest.main()
