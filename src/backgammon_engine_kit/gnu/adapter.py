"""Evidence-gated GNU Backgammon checker/cube adapter."""

from dataclasses import dataclass
from datetime import datetime, timezone

from ..adapters import (
    AdapterError,
    AdapterTimeout,
    EngineAdapter,
    MalformedRawResponse,
    UnsupportedCapability,
)
from ..models import RawSource
from ..process import run_process
from .config import (
    GNU_SUPPORTED_CHECKER_PLIES,
    GNU_SUPPORTED_CUBE_PLIES,
    GNU_VERSION_LINE,
    gnu_configuration_settings,
)
from .invocation import build_invocation
from .parser import GnuTextParser


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class GnuExecutionRecord:
    invocation: object
    outcome: object
    started_at: str
    completed_at: str


@dataclass(frozen=True)
class GnuRunRecord(GnuExecutionRecord):
    result: object


class GnuAdapter(EngineAdapter):
    engine = "gnu"

    def __init__(self, runtime, process_runner=run_process, parser=None):
        self.runtime = runtime
        self.process_runner = process_runner
        self.parser = parser or GnuTextParser()

    def _validate_request(self, request):
        if request.engine != "gnu":
            raise UnsupportedCapability("GNU adapter received a request for another engine")
        if request.decision_type not in ("checker", "cube"):
            raise UnsupportedCapability("GNU evidence supports only checker and cube decisions")
        try:
            requested_plies = int(request.analysis_setting[:-3])
        except (TypeError, ValueError):
            raise UnsupportedCapability("GNU analysis setting must be a supported ply value")
        supported = (
            GNU_SUPPORTED_CHECKER_PLIES
            if request.decision_type == "checker"
            else GNU_SUPPORTED_CUBE_PLIES
        )
        if requested_plies not in supported:
            raise UnsupportedCapability(
                "GNU evidence does not support {} {}".format(
                    request.analysis_setting,
                    request.decision_type,
                )
            )
        if request.position.format != "gnuid":
            raise UnsupportedCapability("GNU evidence requires a verified combined GNU ID")
        try:
            settings = gnu_configuration_settings(request.configuration)
        except ValueError as exc:
            raise UnsupportedCapability(str(exc))
        expected_plies = (
            settings["checker_plies"]
            if request.decision_type == "checker"
            else settings["cube_plies"]
        )
        if requested_plies != expected_plies:
            raise UnsupportedCapability(
                "GNU request setting does not match the pinned checker/cube profile"
            )

    def _validate_runtime(self):
        try:
            self.runtime.validate_files()
        except FileNotFoundError as exc:
            raise AdapterError(str(exc))
        except ValueError as exc:
            raise UnsupportedCapability(str(exc))
        version = self.process_runner(
            (str(self.runtime.executable), "--version"),
            timeout_seconds=5.0,
            environment=self.runtime.environment(),
        )
        if version.failure_code == "timeout":
            raise AdapterTimeout("GNU version verification exceeded its bounded timeout")
        if version.status != "complete":
            raise AdapterError("GNU version verification failed")
        first_line = version.stdout.splitlines()[0] if version.stdout.splitlines() else ""
        if first_line != GNU_VERSION_LINE:
            raise UnsupportedCapability("GNU engine version differs from verified evidence")

    def execute(self, request, timeout_seconds):
        self._validate_request(request)
        self._validate_runtime()
        invocation = build_invocation(request, self.runtime)
        started_at = _timestamp()
        outcome = self.process_runner(
            invocation.argv,
            timeout_seconds=timeout_seconds,
            stdin_text=invocation.stdin_text,
            environment=invocation.environment,
        )
        completed_at = _timestamp()
        return GnuExecutionRecord(invocation, outcome, started_at, completed_at)

    def run_verified(self, request, timeout_seconds):
        execution = self.execute(request, timeout_seconds)
        invocation = execution.invocation
        outcome = execution.outcome
        started_at = execution.started_at
        completed_at = execution.completed_at
        raw_source = RawSource.from_output(outcome.stdout, captured_at=completed_at)
        if outcome.failure_code == "timeout":
            raise AdapterTimeout("GNU analysis exceeded its bounded timeout", raw_source=raw_source)
        if outcome.status != "complete":
            raise AdapterError(
                "GNU process exited nonzero ({})".format(outcome.returncode),
                raw_source=raw_source,
            )
        try:
            result = self.parser.parse(
                request,
                raw_source,
                started_at=started_at,
                completed_at=completed_at,
            )
        except MalformedRawResponse as exc:
            exc.raw_source = raw_source
            raise
        return GnuRunRecord(invocation, outcome, started_at, completed_at, result)

    def analyze(self, request, timeout_seconds):
        return self.run_verified(request, timeout_seconds).result
