"""Stable foreground JSON input/output interface."""

import json
import sys

from .cache import FileCache, cache_key
from .capabilities import capability_report
from .codec import configuration_from_dict, request_from_dict
from .serialization import canonical_json
from .service import AnalysisResponse, AnalysisService


def handle(payload):
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    operation = payload.get("operation")
    if operation == "capabilities":
        return {"ok": True, "capabilities": capability_report().to_dict()}
    if operation == "validate_configuration":
        configuration = configuration_from_dict(payload.get("configuration"))
        return {"ok": True, "configuration": configuration.to_dict()}
    if operation == "analyze_fixture":
        request = request_from_dict(payload.get("request"))
        if request.engine == "gnu":
            from .gnu.fixtures import load_verified_bundle
        else:
            from .sage.fixtures import load_verified_bundle
        bundle = payload.get("fixture_bundle")
        if not isinstance(bundle, str) or not bundle:
            raise ValueError("fixture_bundle must be a non-empty path")
        result = load_verified_bundle(bundle, expected_request=request)
        response = AnalysisResponse("miss", cache_key(request), result)
        return {"ok": True, "analysis": response.to_dict()}
    if operation in ("validate_request", "cache_key", "cache_lookup", "analyze"):
        request = request_from_dict(payload.get("request"))
        if operation == "validate_request":
            return {"ok": True, "request": request.to_dict()}
        if operation == "cache_key":
            return {"ok": True, "cache_key": cache_key(request)}
        cache_root = payload.get("cache_root")
        cache = FileCache(cache_root) if cache_root else None
        if operation == "cache_lookup":
            lookup = (cache or FileCache(".backgammon-engine-kit-cache")).lookup(request)
            return {"ok": True, "cache": lookup.to_dict()}
        timeout_seconds = payload.get("timeout_seconds", 30.0)
        response = AnalysisService(cache=cache).analyze(request, timeout_seconds=timeout_seconds)
        return {"ok": True, "analysis": response.to_dict()}
    raise ValueError("unsupported operation")


def main():
    try:
        source = sys.stdin.read()
        payload = json.loads(source)
        output = handle(payload)
        exit_code = 0
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        output = {
            "ok": False,
            "error": {"code": "invalid_input", "message": str(exc) or exc.__class__.__name__},
        }
        exit_code = 2
    except OSError:
        output = {
            "ok": False,
            "error": {"code": "io_error", "message": "cache input/output operation failed"},
        }
        exit_code = 3
    sys.stdout.write(canonical_json(output) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
