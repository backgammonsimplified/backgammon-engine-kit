from __future__ import annotations

import csv
import inspect
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backgammon_research import oracle_gallery as og
from backgammon_research.calculator_reference import (
    RELEASE_COMMIT as CALCULATOR_COMMIT,
    REQUESTED_RELEASE_REF as CALCULATOR_REF,
    release_provenance_matches as calculator_provenance_matches,
)
from backgammon_research.models import RESULT_CLASSIFICATIONS
from backgammon_research.renderer import (
    EXPECTED_COMMIT as BOARD_COMMIT,
    REQUESTED_RELEASE_REF as BOARD_REF,
    BackgammonBoardRenderer,
    release_provenance_matches as board_provenance_matches,
)


CURRENT_BOARD_SHA = "0bc70d30e458642f41d4976948e49492c2c6117c"
CURRENT_CALCULATOR_SHA = "a385a963ed01a6eac083dae7a1b246b1c150b3eb"
EMPTY_XGID = "XGID=--------------------------:0:0:1:00:0:0:0:0:10"
EMPTY_GNUID = "PAAAAAAAAAAAAA:cAkAAAAAAAAE"


def factual_state(maximum_cube: int = 1024):
    empty_points = [0] * 24
    return {
        "stable_player_identity": {"player_0": "player_0", "player_1": "player_1"},
        "checker_points": {"player_0": empty_points, "player_1": empty_points},
        "bars": {"player_0": 0, "player_1": 0},
        "borne_off": {"player_0": 15, "player_1": 15},
        "state": {
            "on_roll": "player_1",
            "decision_player": "player_1",
            "action": "roll",
            "dice": None,
        },
        "cube": {"value": 1, "owner": "center"},
        "score": {"player_0": 0, "player_1": 0, "match_length": 0},
        "rules": {
            "crawford": False,
            "jacoby": False,
            "beavers": False,
            "maximum_cube": maximum_cube,
        },
    }


class FakeSurface:
    def __init__(self, name: str):
        self.name = name

    def xgid_to_gnuid(self, value: str) -> str:
        return EMPTY_GNUID

    def gnuid_to_xgid(self, value: str) -> str:
        return EMPTY_XGID


class FakeCalculator:
    provenance = {
        "requested_release_ref": CALCULATOR_REF,
        "resolved_release_commit": CALCULATOR_COMMIT,
        "release_commit": CALCULATOR_COMMIT,
        "package_version": "0.2.0",
    }

    def xgid_to_gnuid(self, value: str):
        return {"input": value, "gnuid": EMPTY_GNUID, "entry_point": "xgid_to_gnuid"}

    def gnuid_to_xgid(self, value: str):
        return {"input": value, "xgid": EMPTY_XGID, "entry_point": "gnuid_to_xgid"}

    def canonical_position(self, value: str):
        return factual_state()


class FakeBglab:
    provenance = {"remote_sha": "bglab-sha"}

    def convert(self, value: str):
        return {"input": value, "xgid": EMPTY_XGID, "diagnostic": True}


class FakeGnu:
    provenance = {"executable": "gnubg-cli", "command_contract": "real CLI contract"}

    def __init__(self):
        self.calls: list[str] = []

    def load(self, identifier: str, scratch: Path, name: str):
        self.calls.append(identifier)
        return {
            "input": identifier,
            "complete_gnuid": EMPTY_GNUID,
            "exported_text": "GNU board evidence",
            "rawboard": "board:fake",
            "commands": ["show board"],
            "argv": ["gnubg-cli", "-c", "commands"],
            "stdout": "GNU board evidence",
            "stderr": "",
            "exit_code": 0,
        }


class FakeRenderer:
    provenance = {
        "requested_release_ref": BOARD_REF,
        "resolved_release_commit": BOARD_COMMIT,
        "remote_sha": CURRENT_BOARD_SHA,
        "resolved_commit": CURRENT_BOARD_SHA,
        "package_version": "0.1.1",
        "color_preset": "bs",
        "style_preset": "bs",
    }

    def __init__(self):
        self.xgid_calls: list[str] = []
        self.gnuid_calls: list[str] = []

    def render(self, xgid: str, output_dir: Path, name: str):
        self.xgid_calls.append(xgid)
        return {
            "input": xgid,
            "type": "svg",
            "output": "<svg><text>released BS board</text></svg>",
            "factual_state": factual_state(),
            **self.provenance,
        }

    def render_gnuid(self, gnuid: str, output_dir: Path, name: str):
        self.gnuid_calls.append(gnuid)
        return {
            "input": gnuid,
            "type": "svg",
            "output": "<svg><text>direct GNUID board</text></svg>",
            "factual_state": factual_state(),
            **self.provenance,
        }


class GalleryContractTests(unittest.TestCase):
    def _build(self, root: Path, *, same=None):
        cases = root / "cases.csv"
        with cases.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["case_id", "label", "xgid", "gnuid"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "case_id": "checker-4-2",
                    "label": "Checker decision with 4-2",
                    "xgid": EMPTY_XGID,
                    "gnuid": EMPTY_GNUID,
                }
            )
        gnu = FakeGnu()
        renderer = FakeRenderer()
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(og, "NativeSurface", lambda: FakeSurface("native_python"))
            )
            stack.enter_context(
                patch.object(og, "BridgeSurface", lambda: FakeSurface("engine_kit"))
            )
            stack.enter_context(
                patch.object(
                    og, "AnkiSurface", lambda: FakeSurface("ankigammon_direct")
                )
            )
            stack.enter_context(patch.object(og, "_engine_factual", lambda value: factual_state()))
            stack.enter_context(
                patch.object(og, "_bek", lambda: type("B", (), {"__file__": __file__})())
            )
            if same is not None:
                stack.enter_context(patch.object(og, "_same", same))
            report = og.build_gallery(
                cases_path=cases,
                case_id="checker-4-2",
                output_dir=root / "out",
                r_library=root,
                calculator=FakeCalculator(),
                bglab=FakeBglab(),
                gnu=gnu,
                renderer=renderer,
            )
        page = (root / "out" / "oracle-gallery.html").read_text(encoding="utf-8")
        return report, page, gnu, renderer

    def test_fixture_catalog_retains_checker_and_top_roller(self):
        cases = og.load_cases(Path(__file__).parents[1] / "fixtures" / "cases.csv")
        ids = {case.case_id for case in cases}
        self.assertIn("checker-4-2", ids)
        self.assertIn("known-top-roller", ids)
        selected = og.load_cases(
            Path(__file__).parents[1] / "fixtures" / "cases.csv", "checker-4-2"
        )
        self.assertEqual([case.case_id for case in selected], ["checker-4-2"])

    def test_released_dependency_refs_and_commits(self):
        self.assertEqual(CALCULATOR_REF, "v0.2.0")
        self.assertEqual(CALCULATOR_COMMIT, CURRENT_CALCULATOR_SHA)
        self.assertEqual(BOARD_REF, "v0.1.1")
        self.assertEqual(BOARD_COMMIT, CURRENT_BOARD_SHA)

    def test_provenance_can_be_proved_by_release_ref_without_remote_sha(self):
        self.assertTrue(calculator_provenance_matches({"remote_ref": "v0.2.0"}))
        self.assertTrue(board_provenance_matches({"remote_ref": "v0.1.1"}))
        self.assertFalse(calculator_provenance_matches({"remote_ref": "main"}))
        self.assertFalse(board_provenance_matches({"remote_ref": "main"}))

    def test_board_renderer_uses_real_bs_public_presets(self):
        source = inspect.getsource(BackgammonBoardRenderer._render_identifier)
        self.assertIn('backgammonboard::board_colors("bs")', source)
        self.assertIn('backgammonboard::board_style("bs")', source)
        self.assertIn("backgammonboard::ggboard", source)

    def test_three_columns_exist_in_required_order(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            report, page, _, _ = self._build(Path(temporary))
        marker = 'data-column-order="native_python,engine_kit,ankigammon_direct"'
        self.assertEqual(page.count(marker), 2)
        for direction in (og.XGID_DIRECTION, og.GNUID_DIRECTION):
            case = next(item for item in report["cases"] if item["direction"] == direction)
            self.assertEqual([item["surface"] for item in case["methods"]], list(og.SURFACE_ORDER))

    def test_each_method_orders_gnu_then_board_then_canonical(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            _, page, _, _ = self._build(Path(temporary))
        cards = page.split('<article class="method-card">')[1:]
        self.assertEqual(len(cards), 6)
        for card in cards:
            card = card.split("</article>", 1)[0]
            self.assertLess(card.index("GNU CLI"), card.index("backgammonboard"))
            self.assertLess(card.index("backgammonboard"), card.index("Canonical representation"))

    def test_complete_gnuids_use_gnu_cli_and_xgids_use_board(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            _, _, gnu, renderer = self._build(Path(temporary))
        self.assertIn(EMPTY_GNUID, gnu.calls)
        self.assertIn(EMPTY_XGID, gnu.calls)  # retained GNU post-import diagnostic
        self.assertIn(EMPTY_XGID, renderer.xgid_calls)
        self.assertTrue(all(value.startswith("XGID=") for value in renderer.xgid_calls))

    def test_board_direct_complete_gnuid_consumer_parity_is_recorded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            report, page, _, renderer = self._build(Path(temporary))
        self.assertIn(EMPTY_GNUID, renderer.gnuid_calls)
        for case in report["cases"]:
            parity = case["board_direct_gnuid_consumer_parity"]
            self.assertEqual(parity["classification"], "exact agreement")
            self.assertIn("Calculator XGID", parity["path_a"]["description"])
            self.assertIn("Board directly", parity["path_b"]["description"])
        self.assertIn("Board direct-complete-GNUID consumer parity", page)

    def test_bglab_is_explicitly_secondary_diagnostic(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            report, page, _, _ = self._build(Path(temporary))
        self.assertIn("secondary diagnostic only", report["provenance"]["bglab"]["role"])
        self.assertIn("Diagnostic only: R bglab (not canonical)", page)

    def test_five_category_vocabulary_is_exact(self):
        self.assertEqual(
            RESULT_CLASSIFICATIONS,
            (
                "exact agreement",
                "representational/default/normalization difference",
                "factual state mismatch",
                "unsupported/unavailable",
                "error",
            ),
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            report, _, _, _ = self._build(Path(temporary))
        self.assertEqual(report["classifications"], list(RESULT_CLASSIFICATIONS))

    def test_reference_exercises_both_calculator_directions_and_canonical_apis(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            report, page, _, _ = self._build(Path(temporary))
        entry_points = {
            case["calculator_conversion"]["entry_point"] for case in report["cases"]
        } | {
            case["calculator_return_conversion"]["entry_point"]
            for case in report["cases"]
        }
        self.assertEqual(entry_points, {"xgid_to_gnuid", "gnuid_to_xgid"})
        self.assertIn("Calculator canonical source / converted / round-trip factual comparison", page)

    def test_semantic_failure_still_writes_all_diagnostic_outputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1]) as temporary:
            root = Path(temporary)
            report, _, _, _ = self._build(root, same=lambda left, right: False)
            self.assertEqual(og.semantic_exit_code(report), 1)
            for relative in (
                "oracle-gallery.html",
                "oracle-comparison-results.json",
                "method-comparisons.csv",
                "roundtrips.csv",
            ):
                self.assertTrue((root / "out" / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
