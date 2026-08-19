import json
from pathlib import Path

import pytest

from backgammon_engine_kit.capabilities import capability_report
from backgammon_engine_kit.gnu.config import (
    GNU_NORMAL_MOVE_FILTER_PROFILE,
    gnu_configuration,
    verified_gnu_configuration,
)
from backgammon_engine_kit.gnu.invocation import build_invocation as build_gnu_invocation
from backgammon_engine_kit.models import AnalysisRequest, Position
from backgammon_engine_kit.sage.config import sage_configuration, verified_sage_configuration
from backgammon_engine_kit.sage.invocation import build_invocation as build_sage_invocation


class SageRuntime:
    python_executable = Path("/opt/bgsage/bin/python3")
    protocol_script = Path("/opt/engine-kit/sage-protocol.py")

    def environment(self):
        return {}


class GnuRuntime:
    executable = Path("/opt/gnu/bin/gnubg")
    data_dir = Path("/opt/gnu/share")
    package_data_dir = Path("/opt/gnu/share/gnubg")

    def environment(self):
        return {}


def sage_request(decision_type, setting, configuration):
    position = (
        "4PPgASTgc/ABMA:cAnqAAAAAAAE"
        if decision_type == "checker"
        else "bD3BAQyYd2cEAA:cAngAAAAAAAE"
    )
    return AnalysisRequest(
        position=Position(id=position, format="gnuid"),
        engine="sage",
        analysis_setting=setting,
        decision_type=decision_type,
        dice=(4, 2) if decision_type == "checker" else None,
        configuration=configuration,
    )


def gnu_request(decision_type, setting, configuration):
    return AnalysisRequest(
        position=Position(id="4PPgASTgc/ABMA:cAnqAAAAAAAE", format="gnuid"),
        engine="gnu",
        analysis_setting=setting,
        decision_type=decision_type,
        dice=(4, 2) if decision_type == "checker" else None,
        configuration=configuration,
    )


def test_legacy_one_ply_configuration_identity_is_preserved():
    assert sage_configuration() == verified_sage_configuration()
    assert gnu_configuration() == verified_gnu_configuration()


def test_historical_trial_profiles_pin_checker_and_cube_independently():
    sage = sage_configuration(checker_setting="4ply", cube_setting="3ply")
    sage_options = dict(sage.options)
    assert sage_options["checker_analysis_setting"] == "4ply"
    assert sage_options["cube_analysis_setting"] == "3ply"
    assert "checker-4ply-cube-3ply" in sage.profile
    assert sage.configuration_hash == sage_configuration("4ply", "3ply").configuration_hash

    gnu = gnu_configuration(checker_plies=3, cube_plies=2)
    gnu_options = dict(gnu.options)
    assert gnu_options["checker_evaluation_plies"] == 3
    assert gnu_options["cube_evaluation_plies"] == 2
    assert gnu_options["checker_move_filter_profile"] == GNU_NORMAL_MOVE_FILTER_PROFILE
    assert "checker-3ply-cube-2ply-normal-filter" in gnu.profile
    assert gnu.configuration_hash == gnu_configuration(3, 2).configuration_hash


def test_sage_trial_profile_flows_into_decision_specific_protocol_requests():
    configuration = sage_configuration(checker_setting="4ply", cube_setting="3ply", parallel_threads=2)
    checker = json.loads(
        build_sage_invocation(sage_request("checker", "4ply", configuration), SageRuntime()).stdin_text
    )
    cube = json.loads(
        build_sage_invocation(sage_request("cube", "3ply", configuration), SageRuntime()).stdin_text
    )
    assert checker["analysis"]["analysis_setting"] == "4ply"
    assert cube["analysis"]["analysis_setting"] == "3ply"
    assert checker["analysis"]["parallel_threads"] == 2
    assert cube["analysis"]["parallel_threads"] == 2


def test_gnu_trial_profile_flows_into_checker_cube_and_normal_filter_commands():
    configuration = gnu_configuration(checker_plies=3, cube_plies=2, threads=2)
    checker = build_gnu_invocation(gnu_request("checker", "3ply", configuration), GnuRuntime())
    cube = build_gnu_invocation(gnu_request("cube", "2ply", configuration), GnuRuntime())
    for invocation in (checker, cube):
        assert "set evaluation chequerplay evaluation plies 3\n" in invocation.stdin_text
        assert "set evaluation cubedecision evaluation plies 2\n" in invocation.stdin_text
        assert "set threads 2\n" in invocation.stdin_text
        assert "set evaluation movefilter 1 0 0 8 0.160\n" in invocation.stdin_text
        assert "set evaluation movefilter 2 1 -1 0 0.000\n" in invocation.stdin_text
        assert "set evaluation movefilter 3 2 0 2 0.040\n" in invocation.stdin_text
        assert "set evaluation movefilter 4 3 -1 0 0.000\n" in invocation.stdin_text
    assert checker.stdin_text.endswith("hint 8\nquit\n")
    assert cube.stdin_text.endswith("hint\nquit\n")


def test_numeric_one_through_four_ply_is_runtime_configurable_without_source_changes():
    sage = sage_configuration(checker_setting="2ply", cube_setting="4ply")
    assert dict(sage.options)["checker_analysis_setting"] == "2ply"
    assert dict(sage.options)["cube_analysis_setting"] == "4ply"
    checker = json.loads(
        build_sage_invocation(sage_request("checker", "2ply", sage), SageRuntime()).stdin_text
    )
    cube = json.loads(
        build_sage_invocation(sage_request("cube", "4ply", sage), SageRuntime()).stdin_text
    )
    assert checker["analysis"]["analysis_setting"] == "2ply"
    assert cube["analysis"]["analysis_setting"] == "4ply"

    gnu = gnu_configuration(checker_plies=2, cube_plies=4)
    invocation = build_gnu_invocation(gnu_request("checker", "2ply", gnu), GnuRuntime())
    assert "set evaluation chequerplay evaluation plies 2\n" in invocation.stdin_text
    assert "set evaluation cubedecision evaluation plies 4\n" in invocation.stdin_text
    assert dict(gnu.options)["checker_move_filter_profile"] == GNU_NORMAL_MOVE_FILTER_PROFILE


def test_capability_report_matches_retained_evidence_not_every_configurable_setting():
    report = capability_report()
    sage = report.for_engine("sage")
    gnu = report.for_engine("gnu")
    assert sage.supports("4ply", "checker") is True
    assert sage.supports("3ply", "cube") is True
    assert sage.supports("2ply", "checker") is False
    assert gnu.supports("3ply", "checker") is True
    assert gnu.supports("2ply", "cube") is True
    assert gnu.supports("4ply", "checker") is False


def test_settings_outside_numeric_one_through_four_remain_fail_closed():
    with pytest.raises(ValueError, match="Sage checker"):
        sage_configuration(checker_setting="rollout", cube_setting="3ply")
    with pytest.raises(ValueError, match="Sage cube"):
        sage_configuration(checker_setting="4ply", cube_setting="truncated1")
    with pytest.raises(ValueError, match="GNU checker"):
        gnu_configuration(checker_plies=5, cube_plies=2)
    with pytest.raises(ValueError, match="GNU cube"):
        gnu_configuration(checker_plies=3, cube_plies=5)
