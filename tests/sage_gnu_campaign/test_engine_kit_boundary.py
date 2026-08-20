from __future__ import annotations

import pytest

from runner.sage_gnu_campaign.engine_kit import EngineKitMismatch, validate_actual_depth_evidence


def test_gnu_checker_configured_target_is_distinct_from_shallower_actual_depths() -> None:
    validate_actual_depth_evidence("gnu", "checker", 3, 2, [2, 3, 1, 0])


def test_engine_depth_mismatches_fail_closed() -> None:
    with pytest.raises(EngineKitMismatch, match="candidate actual depth"):
        validate_actual_depth_evidence("gnu", "checker", 3, 3, [3, 4])
    with pytest.raises(EngineKitMismatch, match="actual depth mismatch"):
        validate_actual_depth_evidence("sage", "checker", 4, 3, [3])
    with pytest.raises(EngineKitMismatch, match="actual depth mismatch"):
        validate_actual_depth_evidence("gnu", "cube", 2, 1, None)
