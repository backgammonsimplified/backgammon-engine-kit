"""Thin consumer of the pinned Engine Kit public adapter/configuration API."""
from __future__ import annotations

import importlib
import importlib.metadata
import sys
from pathlib import Path
from typing import Any

from .config import CampaignConfig


class EngineKitMismatch(RuntimeError):
    """Engine Kit configuration or verified runtime behavior differs from authority."""


def validate_actual_depth_evidence(
    engine: str,
    decision_type: str,
    configured_ply: int,
    recommended_actual_ply: int | None,
    candidate_actual_plies: list[int | None] | None,
) -> None:
    """Apply campaign depth rules without relabeling Engine Kit observations."""
    if engine == "gnu" and decision_type == "checker":
        actuals = candidate_actual_plies or []
        if recommended_actual_ply is None or not 0 <= recommended_actual_ply <= configured_ply:
            raise EngineKitMismatch("GNU recommended checker actual depth is impossible")
        if any(actual is None or not 0 <= actual <= configured_ply for actual in actuals):
            raise EngineKitMismatch("GNU candidate actual depth is impossible")
    elif recommended_actual_ply != configured_ply:
        raise EngineKitMismatch(f"{engine} {decision_type} actual depth mismatch")


class EngineKitSession:
    """Delegate every engine-facing decision operation to the isolated Engine Kit release."""

    def __init__(self, config: CampaignConfig):
        self.config = config
        models = importlib.import_module("backgammon_engine_kit.models")
        sage_module = importlib.import_module("backgammon_engine_kit.sage")
        gnu_module = importlib.import_module("backgammon_engine_kit.gnu")
        gnu_config_module = importlib.import_module("backgammon_engine_kit.gnu.config")
        position_contract = importlib.import_module("backgammon_engine_kit.position_contract")
        imported = Path(importlib.import_module("backgammon_engine_kit").__file__).resolve()
        environment = Path(sys.prefix).resolve()
        if not imported.is_relative_to(environment):
            raise EngineKitMismatch("imported Engine Kit is not isolated in the runner environment")
        expected_version = config.data["engine_kit"]["release"]["tag"].removeprefix("v")
        if importlib.metadata.version("backgammon-engine-kit") != expected_version:
            raise EngineKitMismatch("installed Engine Kit release version differs from campaign authority")

        self.AnalysisRequest = models.AnalysisRequest
        self.Position = models.Position
        self.position_from_gnuid = position_contract.position_from_gnuid
        sage_values = config.data["engines"]["sage"]
        gnu_values = config.data["engines"]["gnu"]
        self.sage_configuration = sage_module.sage_configuration(
            checker_setting=sage_values["checker_configured_target"],
            cube_setting=sage_values["cube_configured_target"],
            parallel_threads=sage_values["threads"],
            seed=sage_values["seed"],
        )
        self.gnu_configuration = gnu_module.gnu_configuration(
            checker_plies=int(gnu_values["checker_configured_target"].removesuffix("ply")),
            cube_plies=int(gnu_values["cube_configured_target"].removesuffix("ply")),
            threads=gnu_values["threads"],
        )
        if self.sage_configuration.configuration_hash != sage_values["configuration_hash"]:
            raise EngineKitMismatch("Sage Engine Kit configuration hash mismatch")
        if self.gnu_configuration.configuration_hash != gnu_values["configuration_hash"]:
            raise EngineKitMismatch("GNU Engine Kit configuration hash mismatch")
        settings = gnu_config_module.gnu_configuration_settings(self.gnu_configuration)
        if settings["move_filter_profile"] != gnu_values["checker_move_filter_identity"]:
            raise EngineKitMismatch("GNU normal-v1 filter identity mismatch")

        self.sage_runtime = sage_module.SageRuntimeConfiguration.discover()
        self.gnu_runtime = gnu_module.GnuRuntimeConfiguration.discover()
        self.sage_runtime.validate_files()
        self.gnu_runtime.validate_files()
        self._validate_runtime_identities()
        self.sage_adapter = sage_module.SageAdapter(self.sage_runtime)
        self.gnu_adapter = gnu_module.GnuAdapter(self.gnu_runtime)

    def _validate_runtime_identities(self) -> None:
        sage = self.sage_runtime.public_identity()
        expected_sage = self.config.data["engines"]["sage"]["runtime_identity"]
        observed_sage = {
            "engine_version": self.sage_configuration.engine_version,
            "python_sha256": sage["python"]["sha256"],
            "native_module_sha256": sage["native_module"]["sha256"],
            "model_identity": sage["model"]["name"],
            "model_sha256": sage["model"]["sha256"],
            "bearoff_sha256": sage["bearoff"]["sha256"],
            "gnuid_parser_sha256": sage["gnuid_parser"]["sha256"],
        }
        gnu = self.gnu_runtime.public_identity()
        expected_gnu = self.config.data["engines"]["gnu"]["runtime_identity"]
        observed_gnu = {
            "engine_version": self.gnu_configuration.engine_version,
            "executable_sha256": gnu["executable"]["sha256"],
            "resources": {item["name"]: item["sha256"] for item in gnu["resources"]},
        }
        if observed_sage != expected_sage:
            raise EngineKitMismatch("Sage public runtime identity differs from campaign authority")
        if observed_gnu != expected_gnu:
            raise EngineKitMismatch("GNU public runtime identity differs from campaign authority")

    def public_identity(self) -> dict[str, Any]:
        return {
            "sage": {
                "configuration": self.sage_configuration.to_dict(),
                "runtime": self.sage_runtime.public_identity(),
            },
            "gnu": {
                "configuration": self.gnu_configuration.to_dict(),
                "runtime": self.gnu_runtime.public_identity(),
            },
        }

    def analyze(
        self,
        engine: str,
        decision_type: str,
        gnuid: str,
        dice: tuple[int, int] | None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        values = self.config.data["engines"][engine]
        setting = values[f"{decision_type}_configured_target"]
        configuration = self.sage_configuration if engine == "sage" else self.gnu_configuration
        adapter = self.sage_adapter if engine == "sage" else self.gnu_adapter
        request = self.AnalysisRequest(
            position=self.Position(id=gnuid, format="gnuid"),
            engine=engine,
            analysis_setting=setting,
            decision_type=decision_type,
            dice=dice,
            configuration=configuration,
        )
        result = adapter.analyze(request, timeout_seconds=timeout_seconds)
        if result.status != "complete" or not result.matches_request(request):
            failure = result.failure.message if result.failure is not None else "result/request mismatch"
            raise EngineKitMismatch(f"{engine} {decision_type} failed verification: {failure}")
        decision = result.checker_decision if decision_type == "checker" else result.cube_decision
        assert decision is not None
        configured_ply = int(setting.removesuffix("ply"))
        candidate_actuals = (
            [candidate.actual_ply for candidate in decision.candidates]
            if decision_type == "checker"
            else None
        )
        validate_actual_depth_evidence(
            engine,
            decision_type,
            configured_ply,
            decision.actual_ply,
            candidate_actuals,
        )
        record = result.to_dict()
        record["campaign_depth_evidence"] = {
            "configured_target": setting,
            "recommended_actual_ply": decision.actual_ply,
            "candidate_actual_plies": candidate_actuals,
        }
        return record
