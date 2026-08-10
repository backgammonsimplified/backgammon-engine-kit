"""Immutable request, result, metadata, and traceability models."""

from dataclasses import dataclass, field
import re
from typing import Any, Optional, Tuple

from .serialization import ensure_public_safe, stable_hash, text_sha256


REQUEST_SCHEMA_VERSION = "analysis-request-v2"
RESULT_SCHEMA_VERSION = "analysis-result-v2"
POSITION_SCHEMA_VERSION = "normalized-position-v1"
ENGINES = frozenset(("sage", "gnu"))
ANALYSIS_SETTINGS = frozenset(
    ("1ply", "2ply", "3ply", "4ply", "truncated1", "truncated2", "truncated3", "rollout")
)
DECISION_TYPES = frozenset(("checker", "cube"))
REPORT_MODES = frozenset(("quick", "full"))
_GNU_ID = re.compile(r"^[A-Za-z0-9+/]{14}:[A-Za-z0-9+/]{12}$")


def _validate_identity(value, name):
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("{} must be a non-empty string or null".format(name))
        ensure_public_safe(value, name)


def _validate_digest(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("{} must be a lowercase SHA-256 digest".format(name))


@dataclass(frozen=True)
class NormalizedPosition:
    board: Tuple[int, ...]
    on_roll: str
    dice: Optional[Tuple[int, int]]
    cube_value: int
    cube_owner: str
    match_length: int
    player_score: int
    opponent_score: int
    crawford: bool
    jacoby: bool
    beaver: bool
    schema_version: str = POSITION_SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "board", tuple(self.board))
        if self.dice is not None:
            object.__setattr__(self, "dice", tuple(self.dice))
        if len(self.board) != 26 or not all(isinstance(point, int) for point in self.board):
            raise ValueError("normalized board must contain exactly 26 integers")
        if self.on_roll not in ("X", "O"):
            raise ValueError("on_roll must be X or O")
        if self.dice is not None and (
            len(self.dice) != 2 or any(not isinstance(die, int) or die < 1 or die > 6 for die in self.dice)
        ):
            raise ValueError("dice must contain two values from 1 through 6 or be null")
        if self.cube_value < 1 or self.cube_value & (self.cube_value - 1):
            raise ValueError("cube_value must be a positive power of two")
        if self.cube_owner not in ("centered", "player", "opponent", "unavailable"):
            raise ValueError("invalid cube_owner")
        if min(self.match_length, self.player_score, self.opponent_score) < 0:
            raise ValueError("match and score values cannot be negative")

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "board": list(self.board),
            "on_roll": self.on_roll,
            "dice": list(self.dice) if self.dice is not None else None,
            "cube_value": self.cube_value,
            "cube_owner": self.cube_owner,
            "match_length": self.match_length,
            "player_score": self.player_score,
            "opponent_score": self.opponent_score,
            "crawford": self.crawford,
            "jacoby": self.jacoby,
            "beaver": self.beaver,
        }


@dataclass(frozen=True)
class Position:
    id: Optional[str]
    format: str
    normalized: Optional[NormalizedPosition] = None

    def __post_init__(self):
        if (self.id is None) == (self.normalized is None):
            raise ValueError("position requires exactly one identifier or complete normalized position")
        if self.id is not None:
            if self.format == "xgid":
                if (
                    not isinstance(self.id, str)
                    or not self.id.startswith("XGID=")
                    or len(self.id) < 16
                    or len(self.id) > 200
                    or any(char.isspace() for char in self.id)
                    or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=:+/" for char in self.id)
                ):
                    raise ValueError("invalid xgid position identifier")
            elif self.format == "gnuid":
                if not isinstance(self.id, str) or _GNU_ID.fullmatch(self.id) is None:
                    raise ValueError("invalid GNU Position ID and Match ID")
            else:
                raise ValueError("identifier format must be xgid or gnuid")
        elif self.format != POSITION_SCHEMA_VERSION:
            raise ValueError("complete normalized positions use format {}".format(POSITION_SCHEMA_VERSION))

    def to_dict(self):
        return {
            "id": self.id,
            "format": self.format,
            "normalized": self.normalized.to_dict() if self.normalized is not None else None,
        }


@dataclass(frozen=True)
class EngineConfiguration:
    engine: str
    profile: str
    engine_version: Optional[str] = None
    model_or_weights_identity: Optional[str] = None
    invocation_identity: Optional[str] = None
    parser_version: Optional[str] = None
    options: Tuple[Tuple[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.engine not in ENGINES:
            raise ValueError("invalid engine")
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise ValueError("configuration profile is required")
        _validate_identity(self.profile, "configuration profile")
        for name in ("engine_version", "model_or_weights_identity", "invocation_identity", "parser_version"):
            _validate_identity(getattr(self, name), name)
        normalized_options = []
        for item in self.options:
            if not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[0], str):
                raise ValueError("configuration options must be key/value pairs")
            if item[1] is not None and not isinstance(item[1], (str, int, float, bool)):
                raise ValueError("configuration option values must be immutable JSON scalars")
            normalized_options.append((item[0], item[1]))
        normalized_options.sort(key=lambda item: item[0])
        if len({key for key, _ in normalized_options}) != len(normalized_options):
            raise ValueError("configuration option keys must be unique")
        object.__setattr__(self, "options", tuple(normalized_options))
        ensure_public_safe(self.to_dict(include_hash=False), "engine configuration")

    @property
    def configuration_hash(self):
        return stable_hash(self.to_dict(include_hash=False))

    def to_dict(self, include_hash=True):
        data = {
            "engine": self.engine,
            "profile": self.profile,
            "engine_version": self.engine_version,
            "model_or_weights_identity": self.model_or_weights_identity,
            "invocation_identity": self.invocation_identity,
            "parser_version": self.parser_version,
            "options": {key: value for key, value in self.options},
        }
        if include_hash:
            data["configuration_hash"] = self.configuration_hash
        return data


@dataclass(frozen=True)
class AnalysisRequest:
    position: Position
    engine: str
    analysis_setting: str
    decision_type: str
    configuration: EngineConfiguration
    dice: Optional[Tuple[int, int]] = None
    report_mode: str = "quick"
    report_mode_changes_data: bool = False
    schema_version: str = REQUEST_SCHEMA_VERSION

    def __post_init__(self):
        if self.engine not in ENGINES:
            raise ValueError("invalid engine")
        if self.analysis_setting not in ANALYSIS_SETTINGS:
            raise ValueError("invalid analysis setting")
        if self.decision_type not in DECISION_TYPES:
            raise ValueError("missing or invalid decision context")
        if self.configuration.engine != self.engine:
            raise ValueError("configuration engine does not match request engine")
        if self.report_mode not in REPORT_MODES:
            raise ValueError("invalid report mode")
        if not isinstance(self.report_mode_changes_data, bool):
            raise ValueError("report_mode_changes_data must be boolean")
        if self.dice is not None:
            object.__setattr__(self, "dice", tuple(self.dice))
        if self.decision_type == "checker":
            if self.dice is None or len(self.dice) != 2 or any(die < 1 or die > 6 for die in self.dice):
                raise ValueError("checker decision requires two dice values")
        elif self.dice is not None:
            raise ValueError("cube decision requires dice to be null")
        if self.position.normalized is not None and self.position.normalized.dice != self.dice:
            raise ValueError("request dice must agree with normalized position")

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "position": self.position.to_dict(),
            "engine": self.engine,
            "analysis_setting": self.analysis_setting,
            "decision_type": self.decision_type,
            "dice": list(self.dice) if self.dice is not None else None,
            "report_mode": self.report_mode,
            "report_mode_changes_data": self.report_mode_changes_data,
            "configuration": self.configuration.to_dict(),
        }

    def cache_identity(self):
        identity = {
            "position": self.position.to_dict(),
            "engine": self.engine,
            "analysis_setting": self.analysis_setting,
            "decision_type": self.decision_type,
            "dice": list(self.dice) if self.dice is not None else None,
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "engine_version": self.configuration.engine_version,
            "model_or_weights_identity": self.configuration.model_or_weights_identity,
            "configuration_hash": self.configuration.configuration_hash,
        }
        if self.report_mode_changes_data:
            identity["report_mode"] = self.report_mode
        return identity


@dataclass(frozen=True)
class ConfigurationTrace:
    configuration_hash: str
    engine_version: Optional[str]
    model_or_weights_identity: Optional[str]
    invocation_identity: Optional[str]
    parser_version: Optional[str]

    def __post_init__(self):
        _validate_digest(self.configuration_hash, "configuration_hash")
        for name in ("engine_version", "model_or_weights_identity", "invocation_identity", "parser_version"):
            _validate_identity(getattr(self, name), name)

    @classmethod
    def from_configuration(cls, configuration):
        return cls(
            configuration_hash=configuration.configuration_hash,
            engine_version=configuration.engine_version,
            model_or_weights_identity=configuration.model_or_weights_identity,
            invocation_identity=configuration.invocation_identity,
            parser_version=configuration.parser_version,
        )

    def to_dict(self):
        return {
            "configuration_hash": self.configuration_hash,
            "engine_version": self.engine_version,
            "model_or_weights_identity": self.model_or_weights_identity,
            "invocation_identity": self.invocation_identity,
            "parser_version": self.parser_version,
        }


@dataclass(frozen=True)
class RawSource:
    inline: Optional[str]
    content_sha256: str
    reference: Optional[str] = None
    captured_at: Optional[str] = None

    def __post_init__(self):
        _validate_digest(self.content_sha256, "raw source content_sha256")
        if (self.inline is None) == (self.reference is None):
            raise ValueError("raw source requires exactly one immutable output or content-addressed reference")
        if self.inline is not None and text_sha256(self.inline) != self.content_sha256:
            raise ValueError("raw source hash does not match inline output")
        if self.reference is not None and self.reference != "sha256:" + self.content_sha256:
            raise ValueError("raw source reference must be content addressed")
        ensure_public_safe(self.to_dict(), "raw source")

    @classmethod
    def from_output(cls, output, captured_at=None):
        return cls(inline=output, content_sha256=text_sha256(output), captured_at=captured_at)

    @classmethod
    def from_hash(cls, digest, captured_at=None):
        return cls(inline=None, content_sha256=digest, reference="sha256:" + digest, captured_at=captured_at)

    def to_dict(self):
        return {
            "inline": self.inline,
            "content_sha256": self.content_sha256,
            "reference": self.reference,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class OutcomeProbabilities:
    win: Optional[float] = None
    win_gammon: Optional[float] = None
    win_backgammon: Optional[float] = None
    lose: Optional[float] = None
    lose_gammon: Optional[float] = None
    lose_backgammon: Optional[float] = None

    def __post_init__(self):
        for name in (
            "win",
            "win_gammon",
            "win_backgammon",
            "lose",
            "lose_gammon",
            "lose_backgammon",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                or value > 1
            ):
                raise ValueError("outcome probabilities must be between zero and one or null")

    def to_dict(self):
        return {
            "win": self.win,
            "win_gammon": self.win_gammon,
            "win_backgammon": self.win_backgammon,
            "lose": self.lose,
            "lose_gammon": self.lose_gammon,
            "lose_backgammon": self.lose_backgammon,
        }


@dataclass(frozen=True)
class CheckerCandidate:
    move_id: str
    rank: int
    notation: Optional[str]
    raw_notation: Optional[str] = None
    is_played_move: Optional[bool] = None
    equity: Optional[float] = None
    equity_difference: Optional[float] = None
    probabilities: Optional[OutcomeProbabilities] = None
    actual_ply: Optional[int] = None
    resulting_position_id: Optional[str] = None
    cubeful: Optional[bool] = None
    notation_source: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.move_id, str) or not self.move_id:
            raise ValueError("checker move_id is required")
        if not isinstance(self.rank, int) or isinstance(self.rank, bool) or self.rank < 1:
            raise ValueError("checker rank must be a positive integer")
        if self.actual_ply is not None and (
            not isinstance(self.actual_ply, int) or isinstance(self.actual_ply, bool) or self.actual_ply < 0
        ):
            raise ValueError("actual_ply must be a non-negative integer or null")
        for name in ("notation", "raw_notation", "resulting_position_id", "notation_source"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError("{} must be a string or null".format(name))
        if self.cubeful is not None and not isinstance(self.cubeful, bool):
            raise ValueError("cubeful must be boolean or null")

    def to_dict(self):
        return {
            "move_id": self.move_id,
            "rank": self.rank,
            "notation": self.notation,
            "raw_notation": self.raw_notation,
            "is_played_move": self.is_played_move,
            "equity": self.equity,
            "equity_difference": self.equity_difference,
            "probabilities": self.probabilities.to_dict() if self.probabilities is not None else None,
            "actual_ply": self.actual_ply,
            "resulting_position_id": self.resulting_position_id,
            "cubeful": self.cubeful,
            "notation_source": self.notation_source,
        }


@dataclass(frozen=True)
class MoveFilter:
    evaluation_ply: int
    accept_ply: int
    extra_moves: int
    tolerance: float

    def __post_init__(self):
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (self.evaluation_ply, self.accept_ply, self.extra_moves)
        ) or min(self.evaluation_ply, self.accept_ply, self.extra_moves) < 0:
            raise ValueError("move-filter values cannot be negative")
        if not isinstance(self.tolerance, (int, float)) or isinstance(self.tolerance, bool) or self.tolerance < 0:
            raise ValueError("move-filter tolerance cannot be negative")

    def to_dict(self):
        return {
            "evaluation_ply": self.evaluation_ply,
            "accept_ply": self.accept_ply,
            "extra_moves": self.extra_moves,
            "tolerance": self.tolerance,
        }


@dataclass(frozen=True)
class CheckerDecision:
    candidates: Tuple[CheckerCandidate, ...]
    recommended_move_id: Optional[str]
    actual_evaluation_type: Optional[str] = None
    actual_ply: Optional[int] = None
    cubeful: Optional[bool] = None
    requested_candidate_count: Optional[int] = None
    exported_candidate_count: Optional[int] = None
    move_filter: Optional[MoveFilter] = None

    def __post_init__(self):
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not self.candidates:
            raise ValueError("checker decision requires at least one candidate")
        if len({candidate.move_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("checker move identifiers must be unique")
        if self.recommended_move_id is not None and self.recommended_move_id not in {
            candidate.move_id for candidate in self.candidates
        }:
            raise ValueError("recommended checker move must identify a candidate")
        if self.exported_candidate_count is not None and self.exported_candidate_count != len(self.candidates):
            raise ValueError("exported checker candidate count does not match candidates")
        for name in ("actual_ply", "requested_candidate_count", "exported_candidate_count"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError("{} must be a non-negative integer or null".format(name))
        if self.cubeful is not None and not isinstance(self.cubeful, bool):
            raise ValueError("cubeful must be boolean or null")

    def to_dict(self):
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "recommended_move_id": self.recommended_move_id,
            "actual_evaluation_type": self.actual_evaluation_type,
            "actual_ply": self.actual_ply,
            "cubeful": self.cubeful,
            "requested_candidate_count": self.requested_candidate_count,
            "exported_candidate_count": self.exported_candidate_count,
            "move_filter": self.move_filter.to_dict() if self.move_filter is not None else None,
        }


@dataclass(frozen=True)
class CubeAction:
    action_id: str
    rank: int
    label: str
    equity: Optional[float] = None
    match_winning_chance: Optional[float] = None
    probabilities: Optional[OutcomeProbabilities] = None

    def __post_init__(self):
        if not isinstance(self.action_id, str) or not self.action_id:
            raise ValueError("cube action_id is required")
        if not isinstance(self.rank, int) or isinstance(self.rank, bool) or self.rank < 1:
            raise ValueError("cube rank must be a positive integer")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("cube action label is required")
        if self.match_winning_chance is not None and (
            not isinstance(self.match_winning_chance, (int, float))
            or isinstance(self.match_winning_chance, bool)
            or self.match_winning_chance < 0
            or self.match_winning_chance > 1
        ):
            raise ValueError("match_winning_chance must be between zero and one or null")

    def to_dict(self):
        return {
            "action_id": self.action_id,
            "rank": self.rank,
            "label": self.label,
            "equity": self.equity,
            "match_winning_chance": self.match_winning_chance,
            "probabilities": self.probabilities.to_dict() if self.probabilities is not None else None,
        }


@dataclass(frozen=True)
class CubeDecision:
    actions: Tuple[CubeAction, ...]
    recommended_action_id: Optional[str]
    gnu_recommendation: Optional[str] = None
    actual_evaluation_type: Optional[str] = None
    actual_ply: Optional[int] = None
    cubeful: Optional[bool] = None
    cubeless_equity: Optional[float] = None
    probabilities: Optional[OutcomeProbabilities] = None
    cube_efficiency: Optional[float] = None
    raw_recommendation: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "actions", tuple(self.actions))
        if not self.actions:
            raise ValueError("cube decision requires at least one action")
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("cube action identifiers must be unique")
        if self.recommended_action_id is not None and self.recommended_action_id not in {
            action.action_id for action in self.actions
        }:
            raise ValueError("recommended cube action must identify an action")
        if self.actual_ply is not None and (
            not isinstance(self.actual_ply, int) or isinstance(self.actual_ply, bool) or self.actual_ply < 0
        ):
            raise ValueError("actual_ply must be a non-negative integer or null")
        if self.cubeful is not None and not isinstance(self.cubeful, bool):
            raise ValueError("cubeful must be boolean or null")

    def to_dict(self):
        return {
            "actions": [action.to_dict() for action in self.actions],
            "recommended_action_id": self.recommended_action_id,
            "gnu_recommendation": self.gnu_recommendation,
            "actual_evaluation_type": self.actual_evaluation_type,
            "actual_ply": self.actual_ply,
            "cubeful": self.cubeful,
            "cubeless_equity": self.cubeless_equity,
            "probabilities": self.probabilities.to_dict() if self.probabilities is not None else None,
            "cube_efficiency": self.cube_efficiency,
            "raw_recommendation": self.raw_recommendation,
        }


@dataclass(frozen=True)
class EngineFailure:
    code: str
    message: str
    retryable: bool

    def to_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class AnalysisResult:
    position: Position
    engine: str
    analysis_setting: str
    configuration_trace: ConfigurationTrace
    decision_type: str
    status: str
    checker_decision: Optional[CheckerDecision]
    cube_decision: Optional[CubeDecision]
    warnings: Tuple[str, ...]
    assumptions: Tuple[str, ...]
    raw_source: Optional[RawSource]
    failure: Optional[EngineFailure] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    schema_version: str = RESULT_SCHEMA_VERSION

    def __post_init__(self):
        if isinstance(self.warnings, (str, bytes)) or isinstance(self.assumptions, (str, bytes)):
            raise ValueError("warnings and assumptions must be sequences of strings")
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        if not all(isinstance(item, str) for item in self.warnings + self.assumptions):
            raise ValueError("warnings and assumptions must contain only strings")
        if self.engine not in ENGINES or self.analysis_setting not in ANALYSIS_SETTINGS:
            raise ValueError("invalid result engine or analysis setting")
        if self.decision_type not in DECISION_TYPES:
            raise ValueError("invalid result decision type")
        if self.status == "complete":
            if self.failure is not None or self.raw_source is None:
                raise ValueError("successful result requires raw traceability and no failure")
            applicable = self.checker_decision if self.decision_type == "checker" else self.cube_decision
            inapplicable = self.cube_decision if self.decision_type == "checker" else self.checker_decision
            if applicable is None or inapplicable is not None:
                raise ValueError("successful result requires exactly one applicable decision section")
        elif self.status == "failed":
            if self.failure is None or self.checker_decision is not None or self.cube_decision is not None:
                raise ValueError("failed result requires failure and null decision sections")
        else:
            raise ValueError("result status must be complete or failed")
        ensure_public_safe(self.to_dict(), "analysis result")

    def matches_request(self, request):
        return (
            self.position == request.position
            and self.engine == request.engine
            and self.analysis_setting == request.analysis_setting
            and self.decision_type == request.decision_type
            and self.configuration_trace == ConfigurationTrace.from_configuration(request.configuration)
        )

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "position": self.position.to_dict(),
            "engine": {
                "name": self.engine,
                "version": self.configuration_trace.engine_version,
                "analysis_setting": self.analysis_setting,
                "model_or_weights_identity": self.configuration_trace.model_or_weights_identity,
            },
            "configuration_trace": self.configuration_trace.to_dict(),
            "decision_type": self.decision_type,
            "status": self.status,
            "checker_decision": self.checker_decision.to_dict() if self.checker_decision is not None else None,
            "cube_decision": self.cube_decision.to_dict() if self.cube_decision is not None else None,
            "warnings": list(self.warnings),
            "assumptions": list(self.assumptions),
            "raw_source": self.raw_source.to_dict() if self.raw_source is not None else None,
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def failure_result(cls, request, code, message, retryable, raw_source=None):
        return cls(
            position=request.position,
            engine=request.engine,
            analysis_setting=request.analysis_setting,
            configuration_trace=ConfigurationTrace.from_configuration(request.configuration),
            decision_type=request.decision_type,
            status="failed",
            checker_decision=None,
            cube_decision=None,
            warnings=(),
            assumptions=(),
            raw_source=raw_source,
            failure=EngineFailure(code=code, message=message, retryable=retryable),
        )
