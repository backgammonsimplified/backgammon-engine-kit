import sys

from backgammon_engine_kit.adapters import AdapterTimeout, EngineAdapter
from backgammon_engine_kit.capabilities import capability_report
from backgammon_engine_kit.process import run_process
from backgammon_engine_kit.service import AnalysisService

from helpers import request


class RaisingAdapter(EngineAdapter):
    engine = "sage"

    def __init__(self, error):
        self.error = error

    def analyze(self, request, timeout_seconds):
        raise self.error


class MalformedAdapter(EngineAdapter):
    engine = "sage"

    def analyze(self, request, timeout_seconds):
        return {"raw": "not a validated result"}


class PathLeakingAdapter(EngineAdapter):
    engine = "sage"

    def analyze(self, request, timeout_seconds):
        raise RuntimeError("engine failed while reading /home/private-user/runtime/output")


def test_unsupported_capability_fails_clearly():
    req = request(setting="rollout")
    response = AnalysisService().analyze(req)
    assert response.cache_outcome == "miss"
    assert response.result.status == "failed"
    assert response.result.failure.code == "unsupported_capability"
    assert response.result.checker_decision is None
    assert response.result.cube_decision is None


def test_capability_report_marks_unverified_work_unavailable():
    report = capability_report()
    assert report.for_engine("sage").supports("rollout", "checker") is False
    assert report.for_engine("gnu").supports("4ply", "cube") is False
    assert report.for_engine("sage").engine_version == "1.2.20260706"
    assert report.for_engine("sage").supports("1ply", "checker") is True
    assert report.for_engine("sage").supports("1ply", "cube") is True


def test_timeout_result():
    req = request()
    adapter = RaisingAdapter(AdapterTimeout("engine exceeded bounded timeout"))
    response = AnalysisService(adapters={"sage": adapter}).analyze(req, timeout_seconds=0.01)
    assert response.result.status == "failed"
    assert response.result.failure.code == "timeout"
    assert response.result.failure.retryable is True


def test_engine_failure_result():
    req = request()
    adapter = RaisingAdapter(RuntimeError("engine process exited"))
    response = AnalysisService(adapters={"sage": adapter}).analyze(req)
    assert response.result.status == "failed"
    assert response.result.failure.code == "engine_failure"
    assert response.result.raw_source is None


def test_engine_failure_does_not_leak_private_path():
    response = AnalysisService(adapters={"sage": PathLeakingAdapter()}).analyze(request())
    assert response.result.failure.code == "engine_failure"
    assert "private-user" not in response.result.failure.message
    assert response.result.failure.message == "engine failure details withheld by public-safety policy"


def test_malformed_raw_response():
    response = AnalysisService(adapters={"sage": MalformedAdapter()}).analyze(request())
    assert response.result.status == "failed"
    assert response.result.failure.code == "malformed_raw_response"


def test_process_runner_enforces_timeout():
    outcome = run_process(
        (sys.executable, "-c", "import time; time.sleep(0.2)"),
        timeout_seconds=0.01,
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "timeout"


def test_process_runner_structures_engine_failure():
    outcome = run_process((sys.executable, "-c", "raise SystemExit(7)"), timeout_seconds=1)
    assert outcome.status == "failed"
    assert outcome.returncode == 7
    assert outcome.failure_code == "engine_failure"


def test_service_rejects_unbounded_timeout_before_adapter_call():
    service = AnalysisService()
    try:
        service.analyze(request(), timeout_seconds=0)
    except ValueError as exc:
        assert "timeout" in str(exc)
    else:
        raise AssertionError("zero timeout was accepted")
