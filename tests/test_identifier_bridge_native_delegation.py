"""Focused tests for XGID bridge delegation to the native identifier codec."""

from types import SimpleNamespace
from unittest.mock import patch

from backgammon_engine_kit import identifier_bridge


KNOWN_TOP_XGID = "XGID=-BDB-------------a------e-:1:-1:-1:42:0:0:0:5:8"
KNOWN_TOP_GNUID = "ewMAAD4gAAAAAA:AQGqAAAAAAAE"


def _request(raw_xgid):
    return SimpleNamespace(
        identifier=SimpleNamespace(
            identifier_format=identifier_bridge.IDENTIFIER_FORMAT_XGID,
            raw_identifier=raw_xgid,
        )
    )


def test_xgid_bridge_calls_native_codec_with_explicit_loss_opt_in():
    request = _request(KNOWN_TOP_XGID)
    with patch(
        "backgammon_engine_kit.position_contract.native_codec.xgid_to_gnuid",
        return_value=KNOWN_TOP_GNUID,
    ) as native_codec:
        assert identifier_bridge._encode_xgid_as_gnuid(request) == KNOWN_TOP_GNUID

    native_codec.assert_called_once_with(KNOWN_TOP_XGID, allow_lossy=True)


def test_engine_gnuid_encoder_routes_xgid_through_native_helper():
    request = _request(KNOWN_TOP_XGID)
    with patch.object(
        identifier_bridge,
        "_encode_xgid_as_gnuid",
        return_value=KNOWN_TOP_GNUID,
    ) as native_helper:
        assert identifier_bridge._encode_engine_gnuid(request) == KNOWN_TOP_GNUID

    native_helper.assert_called_once_with(request)
