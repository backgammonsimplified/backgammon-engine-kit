"""Deterministic, shell-free BGSage protocol invocation."""

from dataclasses import dataclass

from ..serialization import canonical_json
from .config import (
    SAGE_BEAROFF_SHA256,
    SAGE_ENGINE_VERSION,
    SAGE_MODEL_IDENTITY,
    SAGE_MODEL_NAME,
    SAGE_PROTOCOL_VERSION,
)


@dataclass(frozen=True)
class SageInvocation:
    argv: tuple
    stdin_text: str
    environment: dict

    def public_argv(self):
        return ["<BGSAGE_PYTHON>", "<ENGINE_KIT_SAGE_PROTOCOL>"]


def identity_invocation(runtime):
    return SageInvocation(
        argv=(str(runtime.python_executable), str(runtime.protocol_script)),
        stdin_text=canonical_json({"operation": "identify", "protocol": SAGE_PROTOCOL_VERSION}) + "\n",
        environment=runtime.environment(),
    )


def build_invocation(request, runtime):
    if request.position.format != "gnuid" or request.position.id is None:
        raise ValueError("Sage adapter requires a verified combined GNU ID")
    position_id, match_id = request.position.id.split(":", 1)
    payload = {
        "analysis": {
            "analysis_setting": request.analysis_setting,
            "cubeful": True,
            "decision_type": request.decision_type,
            "include_game_plans": False,
            "include_two_ply_cube_details": False,
            "parallel_threads": 1,
            "seed": 42,
        },
        "dice": list(request.dice) if request.dice is not None else None,
        "expected_identity": {
            "bearoff_sha256": SAGE_BEAROFF_SHA256,
            "engine_version": SAGE_ENGINE_VERSION,
            "model": SAGE_MODEL_NAME,
            "model_identity": SAGE_MODEL_IDENTITY,
        },
        "match_id": match_id,
        "operation": "analyze",
        "position_id": position_id,
        "protocol": SAGE_PROTOCOL_VERSION,
    }
    return SageInvocation(
        argv=(str(runtime.python_executable), str(runtime.protocol_script)),
        stdin_text=canonical_json(payload) + "\n",
        environment=runtime.environment(),
    )


def canonical_position_context(position):
    """Adapter-boundary seam for one authoritative canonical BGSage conversion."""
    from ..position_contract.bgsage import canonical_to_bgsage

    return canonical_to_bgsage(position)
