"""Evidence-gated BGSage 1-ply checker/cube adapter."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from ..adapters import (
    AdapterError,
    AdapterTimeout,
    ConfigurationMismatch,
    EngineAdapter,
    MalformedRawResponse,
    UnsupportedCapability,
)
from ..models import RawSource
from ..process import run_process
from .config import SAGE_ENGINE_VERSION, SAGE_MODEL_IDENTITY, SAGE_PROTOCOL_VERSION, verified_sage_configuration
from .invocation import build_invocation, identity_invocation
from .parser import SageJsonParser


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class SageExecutionRecord:
    invocation: object
    outcome: object
    started_at: str
    completed_at: str


@dataclass(frozen=True)
class SageRunRecord(SageExecutionRecord):
    result: object


class SageAdapter(EngineAdapter):
    engine = "sage"

    def __init__(self, runtime, process_runner=run_process, parser=None):
        self.runtime = runtime
        self.process_runner = process_runner
        self.parser = parser or SageJsonParser()

    def _validate_request(self, request):
        if request.engine != "sage":
            raise UnsupportedCapability("Sage adapter received a request for another engine")
        if request.analysis_setting != "1ply":
            raise UnsupportedCapability("Sage evidence supports only the 1ply analysis setting")
        if request.decision_type not in ("checker", "cube"):
            raise UnsupportedCapability("Sage evidence supports only checker and cube decisions")
        if request.position.format != "gnuid":
            raise UnsupportedCapability("Sage evidence requires a verified combined GNU ID")
        if request.configuration != verified_sage_configuration():
            raise ConfigurationMismatch("Sage request configuration identity differs from verified evidence")

    def _validate_runtime(self):
        try:
            self.runtime.validate_files()
        except FileNotFoundError as exc:
            raise AdapterError(str(exc))
        except ValueError as exc:
            raise ConfigurationMismatch(str(exc))
        invocation = identity_invocation(self.runtime)
        outcome = self.process_runner(
            invocation.argv,
            timeout_seconds=15.0,
            stdin_text=invocation.stdin_text,
            environment=invocation.environment,
        )
        if outcome.failure_code == "timeout":
            raise AdapterTimeout("BGSage identity verification exceeded its bounded timeout")
        if outcome.status != "complete":
            raise AdapterError("BGSage identity verification failed")
        try:
            output = json.loads(outcome.stdout)
            identity = output["identity"]
        except (KeyError, TypeError, ValueError):
            raise ConfigurationMismatch("BGSage identity response is malformed")
        if (
            output.get("status") != "complete"
            or output.get("protocol") != SAGE_PROTOCOL_VERSION
            or identity.get("engine_version") != SAGE_ENGINE_VERSION
            or identity.get("model_identity") != SAGE_MODEL_IDENTITY
        ):
            raise ConfigurationMismatch("BGSage version or model identity differs from verified evidence")

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
        return SageExecutionRecord(invocation, outcome, started_at, completed_at)

    def run_verified(self, request, timeout_seconds):
        execution = self.execute(request, timeout_seconds)
        raw_source = RawSource.from_output(execution.outcome.stdout, captured_at=execution.completed_at)
        if execution.outcome.failure_code == "timeout":
            raise AdapterTimeout("BGSage analysis exceeded its bounded timeout", raw_source=raw_source)
        if execution.outcome.status != "complete":
            raise AdapterError(
                "BGSage process exited nonzero ({})".format(execution.outcome.returncode),
                raw_source=raw_source,
            )
        try:
            result = self.parser.parse(
                request,
                raw_source,
                started_at=execution.started_at,
                completed_at=execution.completed_at,
            )
        except MalformedRawResponse as exc:
            exc.raw_source = raw_source
            raise
        return SageRunRecord(
            execution.invocation,
            execution.outcome,
            execution.started_at,
            execution.completed_at,
            result,
        )

    def analyze(self, request, timeout_seconds):
        return self.run_verified(request, timeout_seconds).result
