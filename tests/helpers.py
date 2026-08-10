from backgammon_engine_kit.models import (
    AnalysisRequest,
    AnalysisResult,
    CheckerCandidate,
    CheckerDecision,
    ConfigurationTrace,
    CubeAction,
    CubeDecision,
    EngineConfiguration,
    Position,
    RawSource,
)


VALID_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:1:21:0:0:0:7:10"


def configuration(engine="sage", **overrides):
    values = {
        "engine": engine,
        "profile": "phase1-contract",
        "engine_version": None,
        "model_or_weights_identity": None,
        "invocation_identity": None,
        "parser_version": "contract-parser-v1",
    }
    values.update(overrides)
    return EngineConfiguration(**values)


def request(engine="sage", setting="1ply", decision_type="checker", report_mode="quick"):
    return AnalysisRequest(
        position=Position(id=VALID_XGID, format="xgid"),
        engine=engine,
        analysis_setting=setting,
        decision_type=decision_type,
        dice=(2, 1) if decision_type == "checker" else None,
        configuration=configuration(engine),
        report_mode=report_mode,
    )


def checker_result(req=None):
    req = req or request()
    return AnalysisResult(
        position=req.position,
        engine=req.engine,
        analysis_setting=req.analysis_setting,
        configuration_trace=ConfigurationTrace.from_configuration(req.configuration),
        decision_type="checker",
        status="complete",
        checker_decision=CheckerDecision(
            candidates=(
                CheckerCandidate(
                    move_id="candidate-1",
                    rank=1,
                    notation=None,
                    is_played_move=None,
                    equity=None,
                    equity_difference=None,
                    probabilities=None,
                    actual_ply=None,
                    resulting_position_id=None,
                ),
            ),
            recommended_move_id="candidate-1",
        ),
        cube_decision=None,
        warnings=("contract-only result; no scientific values asserted",),
        assumptions=(),
        raw_source=RawSource.from_output("contract parser input"),
    )

def cube_result(req=None):
    req = req or request(decision_type="cube")
    return AnalysisResult(
        position=req.position,
        engine=req.engine,
        analysis_setting=req.analysis_setting,
        configuration_trace=ConfigurationTrace.from_configuration(req.configuration),
        decision_type="cube",
        status="complete",
        checker_decision=None,
        cube_decision=CubeDecision(
            actions=(
                CubeAction(
                    action_id="action-1",
                    rank=1,
                    label="unavailable",
                    equity=None,
                    match_winning_chance=None,
                    probabilities=None,
                ),
            ),
            recommended_action_id=None,
        ),
        warnings=("contract-only result; no scientific values asserted",),
        assumptions=(),
        raw_source=RawSource.from_output("contract parser input"),
    )
