"""Cache-first position-analysis service boundary."""

from dataclasses import dataclass

from .adapters import AdapterError, MalformedRawResponse, default_adapters
from .cache import MemoryCache
from .models import AnalysisResult
from .process import MAX_TIMEOUT_SECONDS
from .serialization import ensure_public_safe


def _public_failure_message(exc):
    message = str(exc) or exc.__class__.__name__
    try:
        ensure_public_safe(message, "engine failure")
    except ValueError:
        return "engine failure details withheld by public-safety policy"
    return message


@dataclass(frozen=True)
class AnalysisResponse:
    cache_outcome: str
    cache_key: str
    result: AnalysisResult
    schema_version: str = "analysis-response-v1"

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "cache_outcome": self.cache_outcome,
            "cache_key": self.cache_key,
            "result": self.result.to_dict(),
        }


class AnalysisService:
    def __init__(self, cache=None, adapters=None):
        self.cache = cache or MemoryCache()
        self.adapters = adapters or default_adapters()

    def analyze(self, request, timeout_seconds=30.0):
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout must be greater than zero and no more than 3600 seconds")
        lookup = self.cache.lookup(request)
        if lookup.outcome == "hit":
            return AnalysisResponse("hit", lookup.key, lookup.result)
        adapter = self.adapters.get(request.engine)
        if adapter is None:
            result = AnalysisResult.failure_result(
                request, "unsupported_capability", "no adapter is registered", False
            )
            return AnalysisResponse("miss", lookup.key, result)
        try:
            result = adapter.analyze(request, timeout_seconds)
            if not isinstance(result, AnalysisResult):
                raise MalformedRawResponse("adapter did not return a validated analysis result")
            if result.status != "complete":
                raise MalformedRawResponse("adapter returned a non-success result")
            if not result.matches_request(request):
                raise MalformedRawResponse("adapter result does not match the request identity")
            self.cache.store(request, result)
            return AnalysisResponse("miss", lookup.key, result)
        except AdapterError as exc:
            result = AnalysisResult.failure_result(
                request,
                code=exc.code,
                message=_public_failure_message(exc),
                retryable=exc.retryable,
                raw_source=exc.raw_source,
            )
            return AnalysisResponse("miss", lookup.key, result)
        except Exception as exc:
            result = AnalysisResult.failure_result(
                request,
                code="engine_failure",
                message=_public_failure_message(exc),
                retryable=True,
            )
            return AnalysisResponse("miss", lookup.key, result)
