"""Stable foreground JSON input/output interface."""

import argparse
import json
import sys

from .cache import FileCache, cache_key
from .capabilities import capability_report
from .codec import configuration_from_dict, request_from_dict
from .position_contract import (
    BackgammonView,
    renderer_position_from_gnuid,
    renderer_position_from_xgid,
    renderer_position_json,
)
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
    if operation in (
        "validate_request",
        "cache_key",
        "cache_lookup",
        "analyze",
    ):
        request = request_from_dict(payload.get("request"))
        if operation == "validate_request":
            return {"ok": True, "request": request.to_dict()}
        if operation == "cache_key":
            return {"ok": True, "cache_key": cache_key(request)}
        cache_root = payload.get("cache_root")
        cache = FileCache(cache_root) if cache_root else None
        if operation == "cache_lookup":
            lookup = (
                cache or FileCache(".backgammon-engine-kit-cache")
            ).lookup(request)
            return {"ok": True, "cache": lookup.to_dict()}
        timeout_seconds = payload.get("timeout_seconds", 30.0)
        response = AnalysisService(cache=cache).analyze(
            request,
            timeout_seconds=timeout_seconds,
        )
        return {"ok": True, "analysis": response.to_dict()}
    raise ValueError("unsupported operation")


def _write_stdout(text):
    payload = (text + "\n").encode("utf-8")
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(payload.decode("utf-8"))
    else:
        stream.write(payload)


def _json_object_argument(text, label):
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "{} must be valid JSON: {}".format(label, exc.msg)
        ) from exc
    if not isinstance(value, dict):
        raise ValueError("{} must be a JSON object".format(label))
    return value


def _view_argument(text):
    value = _json_object_argument(text, "--view-json")
    try:
        return BackgammonView(**value)
    except TypeError as exc:
        raise ValueError(
            "--view-json is not a Backgammon View v1 object: {}".format(exc)
        ) from exc


def _command_parser():
    parser = argparse.ArgumentParser(
        prog="backgammon-engine-kit",
        description=(
            "Decode a supported position identifier into deterministic "
            "renderer JSON."
        ),
        epilog=(
            "Examples:\n"
            "  backgammon-engine-kit render-xgid "
            "'XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10'\n"
            "  backgammon-engine-kit render-gnuid "
            "'PAAAICMAAAAAAA:cAkAAAAAAAAE'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")
    for command, help_text in (
        ("render-xgid", "decode a supported XGID"),
        ("render-gnuid", "decode a supported GNU Position ID:Match ID"),
    ):
        command_parser = subparsers.add_parser(
            command,
            help=help_text,
            description=help_text,
        )
        command_parser.add_argument(
            "identifier",
            help="complete source identifier",
        )
        command_parser.add_argument(
            "--external-settings-json",
            metavar="JSON",
            help=(
                "explicit context used only to fill source-unknown "
                "Universal Position fields"
            ),
        )
        command_parser.add_argument(
            "--view-json",
            metavar="JSON",
            help=(
                "explicit Backgammon View v1 object; otherwise use the "
                "source/default view"
            ),
        )
    return parser


def _renderer_main(argv):
    parser = _command_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.error("a renderer command is required")
    try:
        view = (
            _view_argument(arguments.view_json)
            if arguments.view_json
            else None
        )
        settings = (
            _json_object_argument(
                arguments.external_settings_json,
                "--external-settings-json",
            )
            if arguments.external_settings_json
            else None
        )
        if arguments.command == "render-xgid":
            result = renderer_position_from_xgid(
                arguments.identifier,
                view=view,
                external_settings=settings,
            )
        else:
            result = renderer_position_from_gnuid(
                arguments.identifier,
                view=view,
                external_settings=settings,
            )
        _write_stdout(renderer_position_json(result))
        return 0
    except (ValueError, TypeError) as exc:
        message = str(exc) or exc.__class__.__name__
        sys.stderr.write(
            "backgammon-engine-kit: error: {}\n".format(message)
        )
        return 2


def _stdin_main():
    try:
        source = sys.stdin.read()
        payload = json.loads(source)
        output = handle(payload)
        exit_code = 0
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        output = {
            "ok": False,
            "error": {
                "code": "invalid_input",
                "message": str(exc) or exc.__class__.__name__,
            },
        }
        exit_code = 2
    except OSError:
        output = {
            "ok": False,
            "error": {
                "code": "io_error",
                "message": "cache input/output operation failed",
            },
        }
        exit_code = 3
    _write_stdout(canonical_json(output))
    return exit_code


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments:
        return _renderer_main(arguments)
    return _stdin_main()


if __name__ == "__main__":
    sys.exit(main())
