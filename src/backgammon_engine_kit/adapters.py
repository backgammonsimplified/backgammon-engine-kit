"""Small evidence-gated adapter boundary; no unverified engine parser lives here."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .capabilities import capability_report


class AdapterError(RuntimeError):
    code = "engine_failure"
    retryable = True

    def __init__(self, message, raw_source=None):
        super().__init__(message)
        self.raw_source = raw_source


class AdapterTimeout(AdapterError):
    code = "timeout"


class MalformedRawResponse(AdapterError):
    code = "malformed_raw_response"
    retryable = False


class UnsupportedCapability(AdapterError):
    code = "unsupported_capability"
    retryable = False


class ConfigurationMismatch(AdapterError):
    code = "configuration_mismatch"
    retryable = False


@dataclass(frozen=True)
class AdapterOutput:
    result: object


class EngineAdapter(ABC):
    engine = None

    @abstractmethod
    def analyze(self, request, timeout_seconds):
        """Return a validated AnalysisResult or raise a structured AdapterError."""


class EngineOutputParser(ABC):
    """Interface for a future verified raw transcript parser."""

    @abstractmethod
    def parse(self, request, raw_source):
        """Return an AnalysisResult whose raw_source is the supplied immutable evidence."""


class UnavailableAdapter(EngineAdapter):
    def __init__(self, engine):
        self.engine = engine

    def analyze(self, request, timeout_seconds):
        available = capability_report().for_engine(self.engine)
        if not available.supports(request.analysis_setting, request.decision_type):
            raise UnsupportedCapability(
                "{} {} {} analysis lacks verified evidence".format(
                    request.engine, request.analysis_setting, request.decision_type
                )
            )
        raise UnsupportedCapability("adapter is unavailable")


def default_adapters():
    try:
        from .gnu import GnuAdapter, GnuRuntimeConfiguration

        gnu = GnuAdapter(GnuRuntimeConfiguration.discover())
    except (FileNotFoundError, ValueError):
        gnu = UnavailableAdapter("gnu")
    try:
        from .sage import SageAdapter, SageRuntimeConfiguration

        sage = SageAdapter(SageRuntimeConfiguration.discover())
    except (FileNotFoundError, ValueError):
        sage = UnavailableAdapter("sage")
    return {"sage": sage, "gnu": gnu}
