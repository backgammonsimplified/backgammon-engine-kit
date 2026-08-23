from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.sage_gnu_campaign.config import load_campaign_config
from runner.sage_gnu_campaign.engine_kit import (
    EngineKitMismatch,
    EngineKitSession,
    ReturnedAnalysis,
    analysis_result_forensics,
    validate_actual_depth_evidence,
)
from runner.sage_gnu_campaign.identity import pair_identity
from runner.sage_gnu_campaign.match import PairExecutor


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "experiments/sage-gnu-campaign-v1/campaign.json"


def test_gnu_checker_configured_target_is_distinct_from_shallower_actual_depths() -> None:
    validate_actual_depth_evidence("gnu", "checker", 3, 2, [2, 3, 1, 0])


def test_engine_depth_mismatches_fail_closed() -> None:
    with pytest.raises(EngineKitMismatch, match="candidate actual depth"):
        validate_actual_depth_evidence("gnu", "checker", 3, 3, [3, 4])
    with pytest.raises(EngineKitMismatch, match="actual depth mismatch"):
        validate_actual_depth_evidence("sage", "checker", 4, 3, [3])
    with pytest.raises(EngineKitMismatch, match="actual depth mismatch"):
        validate_actual_depth_evidence("gnu", "cube", 2, 1, None)


@dataclass(frozen=True)
class Position:
    id: str
    format: str


@dataclass(frozen=True)
class AnalysisRequest:
    position: Position
    engine: str
    analysis_setting: str
    decision_type: str
    dice: tuple[int, int] | None
    configuration: object


@dataclass(frozen=True)
class RawSource:
    inline: str
    content_sha256: str

    @classmethod
    def from_output(cls, output: str) -> "RawSource":
        return cls(output, hashlib.sha256(output.encode("utf-8")).hexdigest())

    def to_dict(self) -> dict[str, str]:
        return {"inline": self.inline, "content_sha256": self.content_sha256}


@dataclass(frozen=True)
class AnalysisResult:
    position: Position
    engine: str
    analysis_setting: str
    decision_type: str
    status: str
    checker_decision: object
    cube_decision: object | None
    raw_source: RawSource
    failure: object | None = None

    def matches_request(self, request: AnalysisRequest) -> bool:
        return (
            self.position == request.position
            and self.engine == request.engine
            and self.analysis_setting == request.analysis_setting
            and self.decision_type == request.decision_type
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "position": {"id": self.position.id, "format": self.position.format},
            "engine": {"name": self.engine, "analysis_setting": self.analysis_setting},
            "decision_type": self.decision_type,
            "status": self.status,
            "checker_decision": {
                "actual_ply": self.checker_decision.actual_ply,
                "candidates": [
                    {"move_id": candidate.move_id, "actual_ply": candidate.actual_ply}
                    for candidate in self.checker_decision.candidates
                ],
            },
            "cube_decision": None,
            "raw_source": self.raw_source.to_dict(),
            "failure": None,
        }


class FakeAdapter:
    def __init__(self, raw_source: RawSource) -> None:
        self.raw_source = raw_source
        self.returned: AnalysisResult | None = None

    def analyze(self, request: AnalysisRequest, timeout_seconds: float) -> AnalysisResult:
        assert timeout_seconds == 900.0
        decision = SimpleNamespace(
            actual_ply=3,
            candidates=(SimpleNamespace(move_id="m1", actual_ply=3),),
        )
        self.returned = AnalysisResult(
            position=request.position,
            engine=request.engine,
            analysis_setting=request.analysis_setting,
            decision_type=request.decision_type,
            status="complete",
            checker_decision=decision,
            cube_decision=None,
            raw_source=self.raw_source,
        )
        return self.returned


def engine_kit_session_with_fake_adapter() -> tuple[EngineKitSession, FakeAdapter]:
    config = load_campaign_config(CONFIG)
    session = EngineKitSession.__new__(EngineKitSession)
    session.config = config
    session.AnalysisRequest = AnalysisRequest
    session.Position = Position
    session.sage_configuration = object()
    session.gnu_configuration = object()
    adapter = FakeAdapter(RawSource.from_output("raw adapter response"))
    session.sage_adapter = adapter
    session.gnu_adapter = adapter
    return session, adapter


def test_real_session_boundary_journals_returned_result_before_depth_validation(
    tmp_path: Path,
) -> None:
    session, adapter = engine_kit_session_with_fake_adapter()
    returned = session.analyze_raw("sage", "checker", "position:match", (3, 1), 900.0)
    assert returned.result is adapter.returned
    assert returned.result.raw_source.inline == "raw adapter response"
    with pytest.raises(EngineKitMismatch, match="actual depth mismatch"):
        session.validate_analysis(returned)
    assert returned.result is adapter.returned
    assert returned.result.raw_source.inline == "raw adapter response"

    match_root = tmp_path / "match-A"
    match_root.mkdir()
    config = load_campaign_config(CONFIG)
    with pytest.raises(EngineKitMismatch, match="actual depth mismatch"):
        PairExecutor(config, session)._analyze_with_forensics(
            pair_identity(config, 1),
            "A",
            match_root,
            2,
            "O",
            "sage",
            "checker",
            "position:match",
            (3, 1),
            7,
            9,
        )

    request = json.loads((match_root / "analysis_requests.jsonl").read_text(encoding="utf-8"))
    result = json.loads((match_root / "analysis_results.jsonl").read_text(encoding="utf-8"))
    failure = json.loads((match_root / "analysis_failure.json").read_text(encoding="utf-8"))
    for record in (request, result, failure):
        assert record["match_side"] == "A"
        assert record["game_number"] == 2
        assert record["engine"] == "sage"
        assert record["physical_seat"] == "O"
        assert record["gnuid"] == "position:match"
        assert record["decision_type"] == "checker"
    assert result["returned_result"]["raw_source"]["inline"] == "raw adapter response"
    assert failure["returned_result"] == result["returned_result"]
    assert failure["returned_raw_evidence"][0]["value"]["inline"] == "raw adapter response"
    assert failure["exception_type"] == "EngineKitMismatch"
    assert failure["exception_message"] == "sage checker actual depth mismatch"


def test_forensic_capture_retains_raw_source_when_result_serialization_fails() -> None:
    raw_source = RawSource.from_output("unserialized raw adapter response")

    class UnserializableAnalysisResult:
        def __init__(self) -> None:
            self.raw_source = raw_source

        def to_dict(self):
            raise ValueError("serialization failed")

    record = analysis_result_forensics(UnserializableAnalysisResult())
    assert record["analysis_result_type"] == "UnserializableAnalysisResult"
    assert record["serialization_error_type"] == "ValueError"
    assert record["serialization_error_message"] == "serialization failed"
    assert record["raw_source"]["inline"] == "unserialized raw adapter response"


class UnsupportedAdapterResult:
    def __init__(self) -> None:
        self.raw_source = RawSource.from_output("unsupported-object raw adapter response")

    def __repr__(self) -> str:
        return "<UnsupportedAdapterResult safe-marker>"


@pytest.mark.parametrize(
    "returned_value",
    [
        None,
        "malformed string result",
        123,
        1.25,
        True,
        ["malformed", 7, False],
        {"status": "broken", "raw_source": {"inline": "mapping raw evidence"}},
        UnsupportedAdapterResult(),
    ],
    ids=["none", "string", "integer", "float", "boolean", "list", "mapping", "custom-object"],
)
def test_primitive_malformed_results_preserve_primary_contract_failure_and_evidence(
    tmp_path: Path, returned_value: object
) -> None:
    class PrimaryContractFailure(RuntimeError):
        pass

    primary = PrimaryContractFailure(
        f"primary validation rejected {type(returned_value).__name__}"
    )

    class MalformedResultSession:
        def analyze_raw(self, *_: object) -> ReturnedAnalysis:
            return ReturnedAnalysis(request=object(), result=returned_value)

        def validate_analysis(self, _: ReturnedAnalysis) -> dict[str, object]:
            raise primary

    config = load_campaign_config(CONFIG)
    match_root = tmp_path / "match-A"
    match_root.mkdir()
    with pytest.raises(PrimaryContractFailure) as caught:
        PairExecutor(config, MalformedResultSession())._analyze_with_forensics(  # type: ignore[arg-type]
            pair_identity(config, 1),
            "A",
            match_root,
            1,
            "O",
            "sage",
            "checker",
            "position:match",
            (3, 1),
            1,
            1,
        )
    assert caught.value is primary

    journal = json.loads(
        (match_root / "analysis_results.jsonl").read_text(encoding="utf-8")
    )["returned_result"]
    failure = json.loads(
        (match_root / "analysis_failure.json").read_text(encoding="utf-8")
    )
    assert failure["exception_type"] == "PrimaryContractFailure"
    assert failure["exception_message"] == str(primary)
    assert failure["returned_result"] == journal

    if isinstance(returned_value, dict):
        assert journal["status"] == "broken"
        assert journal["raw_source"]["inline"] == "mapping raw evidence"
    else:
        assert journal["analysis_result_type"] == type(returned_value).__name__
        if isinstance(returned_value, UnsupportedAdapterResult):
            assert journal["returned_value"] == {
                "value_type": "UnsupportedAdapterResult",
                "representation": "<UnsupportedAdapterResult safe-marker>",
            }
            assert journal["raw_source"]["inline"] == (
                "unsupported-object raw adapter response"
            )
        else:
            assert journal["returned_value"] == returned_value
