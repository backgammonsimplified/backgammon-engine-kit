from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backgammon_research import external_batch as batch
from backgammon_research.engine import BridgePreparationUnavailable


FACT_A = {
    "stable_player_identity": {"player_0": "player_0", "player_1": "player_1"},
    "checker_points": {"player_0": [0] * 24, "player_1": [0] * 24},
    "bars": {"player_0": 0, "player_1": 0},
    "borne_off": {"player_0": 15, "player_1": 15},
    "state": {"on_roll": "player_0", "decision_player": "player_0", "action": "roll", "dice": [4, 2]},
    "cube": {"value": 1, "owner": "center"},
    "score": {"player_0": 0, "player_1": 0, "match_length": 0},
    "rules": {"crawford": False, "jacoby": True, "beavers": None, "maximum_cube": 1024},
}
FACT_B = json.loads(json.dumps(FACT_A))
FACT_B["cube"]["value"] = 2


def fact_columns(prefix: str, fact: dict | None, error: str = "") -> dict[str, str]:
    if fact is None:
        result = {f"{prefix}status": "error", f"{prefix}error": error or "decode failed"}
        result.update({f"{prefix}{name}": "" for name in batch.FACT_FIELDS})
        return result
    return {
        f"{prefix}status": "ok", f"{prefix}error": "",
        f"{prefix}player_0_points": ";".join(map(str, fact["checker_points"]["player_0"])),
        f"{prefix}player_1_points": ";".join(map(str, fact["checker_points"]["player_1"])),
        f"{prefix}player_0_bar": str(fact["bars"]["player_0"]),
        f"{prefix}player_1_bar": str(fact["bars"]["player_1"]),
        f"{prefix}player_0_off": str(fact["borne_off"]["player_0"]),
        f"{prefix}player_1_off": str(fact["borne_off"]["player_1"]),
        f"{prefix}on_roll": fact["state"]["on_roll"],
        f"{prefix}decision_player": fact["state"]["decision_player"],
        f"{prefix}action": fact["state"]["action"],
        f"{prefix}dice": ";".join(map(str, fact["state"]["dice"] or [])),
        f"{prefix}cube_value": str(fact["cube"]["value"]),
        f"{prefix}cube_owner": fact["cube"]["owner"],
        f"{prefix}score_player_0": str(fact["score"]["player_0"]),
        f"{prefix}score_player_1": str(fact["score"]["player_1"]),
        f"{prefix}match_length": str(fact["score"]["match_length"]),
        f"{prefix}crawford": str(fact["rules"]["crawford"]).lower(),
        f"{prefix}jacoby": str(fact["rules"]["jacoby"]).lower(),
        f"{prefix}beavers": "" if fact["rules"]["beavers"] is None else str(fact["rules"]["beavers"]).lower(),
        f"{prefix}maximum_cube": str(fact["rules"]["maximum_cube"]),
    }


class FakeCalculatorRunner:
    def __init__(self, *, source_pair_mismatch: bool = False):
        self.source_pair_mismatch = source_pair_mismatch

    def run(self, input_csv: Path, output_dir: Path, progress_interval: int):
        with input_csv.open(newline="", encoding="utf-8") as handle:
            inputs = list(csv.DictReader(handle))
        records = []
        for index, source in enumerate(inputs, 1):
            xgid, gnuid = source["xgid"], source["gnuid"]
            if self.source_pair_mismatch:
                x_mid, g_mid = f"G-X-{index}", f"XGID=G-{index}"
                source_g_fact = FACT_B
                g_fact = FACT_B
            else:
                x_mid, g_mid = gnuid, xgid
                source_g_fact = FACT_A
                g_fact = FACT_A
            record = {
                "input_row": index, "source_xgid": xgid, "source_gnuid": gnuid,
                "x_primary_status": "ok", "x_primary_middle": x_mid, "x_primary_error": "",
                "x_roundtrip_status": "ok", "x_roundtrip_terminal": xgid, "x_roundtrip_error": "",
                "g_primary_status": "ok", "g_primary_middle": g_mid, "g_primary_error": "",
                "g_roundtrip_status": "ok", "g_roundtrip_terminal": gnuid, "g_roundtrip_error": "",
            }
            record.update(fact_columns("src_x__", FACT_A))
            record.update(fact_columns("src_g__", source_g_fact))
            record.update(fact_columns("x_mid__", FACT_A))
            record.update(fact_columns("x_terminal__", FACT_A))
            record.update(fact_columns("g_mid__", g_fact))
            record.update(fact_columns("g_terminal__", source_g_fact))
            records.append(record)
        reference = output_dir / "calculator-reference.csv"
        with reference.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        provenance = {
            "package_version": "0.2.0",
            "requested_release_ref": "v0.2.0",
            "resolved_release_commit": batch.RELEASE_COMMIT,
            "rscript": "fake-Rscript",
        }
        with (output_dir / "calculator-provenance.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(provenance))
            writer.writeheader()
            writer.writerow(provenance)
        return reference, provenance


def normal_methods(source_pair_mismatch: bool = False):
    def x_to_g(value: str) -> str:
        if source_pair_mismatch and value.startswith("XGID=SRC"):
            return value.replace("XGID=SRC", "G-X-")
        if source_pair_mismatch and value.startswith("XGID=G-"):
            return value.replace("XGID=G-", "G-SRC-")
        return value.replace("XGID=SRC-", "G-SRC-")

    def g_to_x(value: str) -> str:
        if source_pair_mismatch and value.startswith("G-SRC-"):
            return value.replace("G-SRC-", "XGID=G-")
        if source_pair_mismatch and value.startswith("G-X-"):
            return value.replace("G-X-", "XGID=SRC")
        return value.replace("G-SRC-", "XGID=SRC-")

    return x_to_g, g_to_x


class ExternalBatchTests(unittest.TestCase):
    def run_batch(
        self,
        *,
        rows=None,
        source_pair_mismatch=False,
        native=None,
        public=None,
        anki=None,
        facts=None,
    ):
        root_cm = tempfile.TemporaryDirectory()
        root = Path(root_cm.name)
        input_path = root / "input.csv"
        source_rows = rows or [{
            "gnuid": "G-SRC-1", "xgid": "XGID=SRC-1", "action": "roll",
            "match_length": "0", "crawford": "false", "note": "preserved extra",
        }]
        with input_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(source_rows[0]))
            writer.writeheader()
            writer.writerows(source_rows)
        x_to_g, g_to_x = normal_methods(source_pair_mismatch)
        surfaces = [
            native or batch.Surface("engine_kit_native", x_to_g, g_to_x),
            public or batch.Surface("engine_kit_public", x_to_g, g_to_x, public_bridge=True),
            anki or batch.Surface("ankigammon_direct", x_to_g, g_to_x),
        ]
        fact_map = {
            "G-SRC-1": FACT_B if source_pair_mismatch else FACT_A,
            "XGID=SRC-1": FACT_A,
            "G-X-1": FACT_A,
            "XGID=G-1": FACT_B,
        }
        fact_map.update(facts or {})
        stack = ExitStack()
        stack.enter_context(patch.object(batch, "_engine_factual", lambda identifier: fact_map.get(identifier, FACT_A)))
        outcome = batch.run_external_batch(
            input_path, root / "out",
            calculator_runner=FakeCalculatorRunner(source_pair_mismatch=source_pair_mismatch),
            surfaces=surfaces, progress_interval=0, repo=Path(__file__).parents[3],
        )
        return root_cm, stack, outcome

    def test_arbitrary_external_csv_and_extra_columns_are_accepted(self):
        root_cm, stack, outcome = self.run_batch()
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.summary["counts"]["action"], {"roll": 8})

    def test_missing_required_columns_fail_clearly_and_write_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bad.csv"
            source.write_text("gnuid,other\nG,ignored\n", encoding="utf-8")
            outcome = batch.run_external_batch(source, root / "out", calculator_runner=FakeCalculatorRunner(), surfaces=[], progress_interval=0, repo=Path(__file__).parents[3])
            self.assertEqual(outcome.exit_code, 1)
            self.assertIn("missing required column", outcome.summary["headline"]["fatal_error"])
            for name in ("summary.json", "roundtrip-results.csv", "errors.csv", "SHA256SUMS.txt"):
                self.assertTrue((root / "out" / name).is_file(), name)

    def test_source_pair_mismatch_is_diagnostic_not_engine_failure(self):
        root_cm, stack, outcome = self.run_batch(source_pair_mismatch=True)
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.summary["headline"]["source_pair_factual_mismatches"], 1)
            self.assertEqual(outcome.summary["headline"]["engine_kit_native_hard_failures"], 0)

    def test_native_factual_mismatch_is_hard_failure_and_outputs_exist(self):
        x_to_g, g_to_x = normal_methods()
        native = batch.Surface("engine_kit_native", lambda value: "G-BAD", g_to_x)
        root_cm, stack, outcome = self.run_batch(native=native, facts={"G-BAD": FACT_B})
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 1)
            self.assertGreater(outcome.summary["headline"]["engine_kit_native_hard_failures"], 0)
            self.assertTrue((outcome.output_dir / "summary.json").is_file())
            self.assertTrue((outcome.output_dir / "roundtrip-results.csv").is_file())

    def test_public_explicit_unsupported_is_not_hard_failure(self):
        _, g_to_x = normal_methods()
        def unsupported(value):
            raise BridgePreparationUnavailable("unsupported", unsupported_state=("pending_double",))
        public = batch.Surface("engine_kit_public", unsupported, g_to_x, public_bridge=True)
        root_cm, stack, outcome = self.run_batch(public=public)
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 0)
            self.assertGreater(outcome.summary["headline"]["public_bridge_unsupported_unavailable"], 0)

    def test_public_genuine_error_is_hard_failure(self):
        _, g_to_x = normal_methods()
        def fail(value):
            raise RuntimeError("bridge exploded")
        public = batch.Surface("engine_kit_public", fail, g_to_x, public_bridge=True)
        root_cm, stack, outcome = self.run_batch(public=public)
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 1)
            self.assertGreater(outcome.summary["headline"]["engine_kit_public_hard_failures"], 0)

    def test_successful_primary_plus_unsupported_return_preserves_middle(self):
        def unsupported_return(value):
            raise BridgePreparationUnavailable("unavailable", missing_state=("crawford",))
        public = batch.Surface(
            "engine_kit_public",
            unsupported_return,
            lambda value: "XGID=SRC-1",
            public_bridge=True,
        )
        root_cm, stack, outcome = self.run_batch(public=public)
        with root_cm, stack:
            with (outcome.output_dir / "roundtrip-results.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            row = next(item for item in rows if item["surface"] == "engine_kit_public" and item["direction"] == batch.GNUID_DIRECTION)
            self.assertEqual(row["primary_conversion_status"], "ok")
            self.assertEqual(row["surface_middle_identifier"], "XGID=SRC-1")
            self.assertEqual(row["round_trip_status"], "unavailable")
            self.assertEqual(row["round_trip_classification"], "unsupported/unavailable")
            self.assertEqual(outcome.exit_code, 0)

    def test_direct_anki_mismatch_visible_but_not_hard(self):
        _, g_to_x = normal_methods()
        anki = batch.Surface("ankigammon_direct", lambda value: "G-BAD", g_to_x)
        root_cm, stack, outcome = self.run_batch(anki=anki, facts={"G-BAD": FACT_B})
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 0)
            self.assertGreater(outcome.summary["headline"]["direct_ankigammon_factual_mismatches"], 0)
            self.assertGreater((outcome.output_dir / "mismatches.csv").stat().st_size, 100)

    def test_representational_normalization_is_not_hard(self):
        _, g_to_x = normal_methods()
        native = batch.Surface("engine_kit_native", lambda value: "G-ALT", g_to_x)
        root_cm, stack, outcome = self.run_batch(native=native, facts={"G-ALT": FACT_A})
        with root_cm, stack:
            self.assertEqual(outcome.exit_code, 0)
            self.assertGreater(outcome.summary["counts"]["primary_classification"].get("representational/default/normalization difference", 0), 0)

    def test_summary_counts_and_detail_row_count_reconcile(self):
        rows = [
            {"gnuid": f"G-SRC-{i}", "xgid": f"XGID=SRC-{i}", "action": "roll", "match_length": "0", "crawford": "false"}
            for i in (1, 2)
        ]
        root_cm, stack, outcome = self.run_batch(rows=rows)
        with root_cm, stack:
            with (outcome.output_dir / "roundtrip-results.csv").open(newline="", encoding="utf-8") as handle:
                detail_count = sum(1 for _ in csv.DictReader(handle))
            self.assertEqual(detail_count, 2 * 2 * 4)
            self.assertEqual(sum(outcome.summary["counts"]["surface"].values()), detail_count)
            self.assertEqual(sum(outcome.summary["counts"]["primary_classification"].values()), detail_count)

    def test_required_outputs_are_present(self):
        root_cm, stack, outcome = self.run_batch()
        with root_cm, stack:
            required = (
                "RUN-METADATA.txt", "summary.json", "summary.csv",
                "source-pair-diagnostics.csv", "roundtrip-results.csv", "mismatches.csv",
                "unsupported.csv", "errors.csv", "SHA256SUMS.txt", "summary.html",
            )
            self.assertTrue(all((outcome.output_dir / name).is_file() for name in required))


if __name__ == "__main__":
    unittest.main()
