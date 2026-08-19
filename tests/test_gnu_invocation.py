from pathlib import Path

import pytest

from backgammon_engine_kit.adapters import MalformedRawResponse
from backgammon_engine_kit.cache import cache_key
from backgammon_engine_kit.capabilities import capability_report
from backgammon_engine_kit.gnu.adapter import GnuAdapter
from backgammon_engine_kit.gnu.config import verified_gnu_configuration
from backgammon_engine_kit.gnu.invocation import build_invocation
from backgammon_engine_kit.models import AnalysisRequest, Position, RawSource
from backgammon_engine_kit.process import ProcessOutcome
from backgammon_engine_kit.service import AnalysisService


class FakeRuntime:
    executable = Path("/opt/gnu/bin/gnubg")
    data_dir = Path("/opt/gnu/share")
    package_data_dir = Path("/opt/gnu/share/gnubg")

    def __init__(self, error=None):
        self.error = error

    def validate_files(self):
        if self.error is not None:
            raise self.error

    def environment(self):
        return {"HOME": "/dev/null", "LANG": "C", "LC_ALL": "C", "OMP_NUM_THREADS": "1"}


def gnu_request(decision_type="checker", setting="1ply", configuration=None):
    return AnalysisRequest(
        position=Position(id="4PPgASTgc/ABMA:cAnqAAAAAAAE", format="gnuid"),
        engine="gnu",
        analysis_setting=setting,
        decision_type=decision_type,
        dice=(4, 2) if decision_type == "checker" else None,
        configuration=configuration or verified_gnu_configuration(),
    )


def test_checker_request_builds_deterministic_shell_free_invocation():
    first = build_invocation(gnu_request(), FakeRuntime())
    second = build_invocation(gnu_request(), FakeRuntime())
    assert first == second
    assert first.argv[-2:] == ("-c", "/dev/stdin")
    assert first.stdin_text.count("hint 8\n") == 1
    assert "set gnubgid 4PPgASTgc/ABMA:cAnqAAAAAAAE\n" in first.stdin_text
    assert "set evaluation chequerplay evaluation plies 1\n" in first.stdin_text
    assert "set evaluation movefilter 1 0 0 8 0.160\n" in first.stdin_text
    assert first.public_argv()[0] == "<GNU_EXECUTABLE>"


def test_cube_request_uses_one_cube_hint_without_checker_limit():
    invocation = build_invocation(gnu_request("cube"), FakeRuntime())
    assert invocation.stdin_text.endswith("hint\nquit\n")
    assert "hint 8\n" not in invocation.stdin_text


def test_gnu_cache_key_is_deterministic():
    assert cache_key(gnu_request()) == cache_key(gnu_request())


def test_capability_report_enables_only_evidenced_gnu_setting():
    gnu = capability_report().for_engine("gnu")
    assert gnu.engine_version == "1.08.003 20260710"
    assert gnu.supports("1ply", "checker") is True
    assert gnu.supports("1ply", "cube") is True
    assert gnu.supports("2ply", "checker") is False


def test_unsupported_gnu_analysis_setting_fails_before_execution():
    adapter = GnuAdapter(FakeRuntime(), process_runner=lambda *args, **kwargs: None)
    response = AnalysisService(adapters={"gnu": adapter}).analyze(gnu_request(setting="rollout"))
    assert response.result.failure.code == "unsupported_capability"


def test_changed_configuration_identity_fails_before_execution():
    configuration = verified_gnu_configuration()
    changed = configuration.__class__(
        engine="gnu",
        profile=configuration.profile,
        engine_version="changed-version",
        model_or_weights_identity=configuration.model_or_weights_identity,
        invocation_identity=configuration.invocation_identity,
        parser_version=configuration.parser_version,
        options=configuration.options,
    )
    adapter = GnuAdapter(FakeRuntime(), process_runner=lambda *args, **kwargs: None)
    response = AnalysisService(adapters={"gnu": adapter}).analyze(
        gnu_request(configuration=changed)
    )
    assert response.result.failure.code == "unsupported_capability"


def test_missing_gnu_executable_is_structured_engine_failure():
    adapter = GnuAdapter(FakeRuntime(FileNotFoundError("GNU Backgammon executable is unavailable")))
    response = AnalysisService(adapters={"gnu": adapter}).analyze(gnu_request())
    assert response.result.failure.code == "engine_failure"


def test_changed_gnu_resource_identity_is_unsupported():
    adapter = GnuAdapter(FakeRuntime(ValueError("GNU resource identity changed: gnubg.wd")))
    response = AnalysisService(adapters={"gnu": adapter}).analyze(gnu_request())
    assert response.result.failure.code == "unsupported_capability"


def test_changed_gnu_version_is_unsupported():
    def runner(*args, **kwargs):
        return ProcessOutcome("complete", 0, "GNU Backgammon changed\n", "", None)

    response = AnalysisService(adapters={"gnu": GnuAdapter(FakeRuntime(), runner)}).analyze(
        gnu_request()
    )
    assert response.result.failure.code == "unsupported_capability"


def test_gnu_timeout_and_nonzero_exit_are_structured():
    def timeout_runner(command, **kwargs):
        if command[-1] == "--version":
            return ProcessOutcome("complete", 0, "GNU Backgammon 1.08.003 20260710\n", "", None)
        return ProcessOutcome("failed", None, "", "", "timeout")

    timed = AnalysisService(adapters={"gnu": GnuAdapter(FakeRuntime(), timeout_runner)}).analyze(
        gnu_request()
    )
    assert timed.result.failure.code == "timeout"

    def failed_runner(command, **kwargs):
        if command[-1] == "--version":
            return ProcessOutcome("complete", 0, "GNU Backgammon 1.08.003 20260710\n", "", None)
        return ProcessOutcome("failed", 7, "", "failure", "engine_failure")

    failed = AnalysisService(adapters={"gnu": GnuAdapter(FakeRuntime(), failed_runner)}).analyze(
        gnu_request()
    )
    assert failed.result.failure.code == "engine_failure"
    assert "nonzero (7)" in failed.result.failure.message


def test_malformed_gnu_output_is_rejected():
    request = gnu_request()
    raw = RawSource.from_output(
        "GNU Backgammon 1.08.003 20260710\n"
        "Position ID: 4PPgASTgc/ABMA\n"
        "Match ID   : cAnqAAAAAAAE\n"
        "1-ply evaluation\nCubeful\n"
    )
    with pytest.raises(MalformedRawResponse, match="checker dice"):
        GnuAdapter(FakeRuntime()).parser.parse(request, raw)
