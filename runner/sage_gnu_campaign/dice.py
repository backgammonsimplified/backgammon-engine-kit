"""Historical deterministic physical-seat dice streams, now Benchmarker-owned."""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "physical-seat-dice-stream-v1"


def opposite_seat(seat: str) -> str:
    normalized = seat.upper()
    if normalized == "O":
        return "X"
    if normalized == "X":
        return "O"
    raise ValueError(f"unknown physical seat: {seat!r}")


def namespace_seed(base_seed: str, side: str) -> str:
    if side not in ("A", "B"):
        raise ValueError("match side must be A or B")
    return f"{base_seed}:match:{side}"


def dice_record(
    seed: str,
    match_number: int,
    match_length: int,
    game_number: int,
    seat: str,
    roll_index: int,
) -> dict[str, int]:
    """Recover the exact historical random.Random string-keyed record."""
    if roll_index <= 0:
        raise ValueError("roll_index must be positive")
    normalized = seat.upper()
    if normalized not in ("O", "X"):
        raise ValueError("seat must be O or X")
    key = (
        f"{seed}:match:{match_number}:length:{match_length}:game:{game_number}:"
        f"seat:{normalized}:roll:{roll_index}"
    )
    rng = random.Random(key)
    return {
        "roll_index": roll_index,
        "opening_die": rng.randint(1, 6),
        "die1": rng.randint(1, 6),
        "die2": rng.randint(1, 6),
    }


def stream_id(seed: str, game_number: int, seat: str) -> str:
    material = "\0".join(
        (SCHEMA_VERSION, seed, "1", "7", str(game_number), seat.upper())
    ).encode("utf-8")
    return "stream-" + hashlib.sha256(material).hexdigest()


def stream_rows(seed: str, game_number: int, seat: str, roll_count: int) -> list[dict[str, int]]:
    return [dice_record(seed, 1, 7, game_number, seat, index) for index in range(1, roll_count + 1)]


def stream_content(seed: str, game_number: int, seat: str, roll_count: int) -> bytes:
    lines = ["roll_index,opening_die,die1,die2"]
    for record in stream_rows(seed, game_number, seat, roll_count):
        lines.append(
            f"{record['roll_index']},{record['opening_die']},{record['die1']},{record['die2']}"
        )
    # Historical csv.DictWriter used the excel dialect with newline="", so the
    # immutable stream bytes use CRLF even though JSON evidence uses LF.
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def stream_sha256(seed: str, game_number: int, seat: str, roll_count: int) -> str:
    return hashlib.sha256(stream_content(seed, game_number, seat, roll_count)).hexdigest()


@dataclass
class SeatDiceController:
    root: Path
    base_seed: str
    side: str
    roll_count: int
    files_per_match: int
    engine_by_seat: dict[str, str]
    current_game_number: int = 1
    expected_next_roll_seat: str | None = None
    opening_attempt_index: dict[int, int] = field(default_factory=dict)
    checker_roll_index: dict[tuple[int, str], int] = field(default_factory=dict)
    consumption: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.side not in ("A", "B"):
            raise ValueError("match side must be A or B")
        if self.roll_count <= 0 or self.files_per_match <= 0:
            raise ValueError("dice stream bounds must be positive")
        if set(self.engine_by_seat) != {"O", "X"} or set(self.engine_by_seat.values()) != {"sage", "gnu"}:
            raise ValueError("engine mapping must assign Sage and GNU to O/X exactly once")

    @property
    def seed(self) -> str:
        return namespace_seed(self.base_seed, self.side)

    def _relative_stream_path(self, game_number: int, seat: str) -> Path:
        return Path(f"game_{game_number:03d}_seat_{seat.upper()}.csv")

    def prepare_files(self) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        identities = []
        for game_number in range(1, self.files_per_match + 1):
            for seat in ("O", "X"):
                path = self.root / self._relative_stream_path(game_number, seat)
                content = stream_content(self.seed, game_number, seat, self.roll_count)
                if path.exists() and path.read_bytes() != content:
                    raise RuntimeError(f"conflicting dice stream exists: {path}")
                if not path.exists():
                    path.write_bytes(content)
                identities.append(
                    {
                        "namespace": self.side,
                        "pair_member": self.side,
                        "match_side": self.side,
                        "game_number": game_number,
                        "physical_seat": seat,
                        "engine": self.engine_by_seat[seat],
                        "stream_id": stream_id(self.seed, game_number, seat),
                        "path": str(self._relative_stream_path(game_number, seat)),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
        return identities

    def prepare_opening(self, game_number: int) -> None:
        self.current_game_number = game_number
        self.expected_next_roll_seat = None

    def prepare_after_turn(self, game_number: int, mover_seat: str) -> None:
        self.current_game_number = game_number
        self.expected_next_roll_seat = opposite_seat(mover_seat)

    def _record(self, prompt_type: str, game_number: int, roll_index: int, seat: str, die1: int, die2: int | None) -> None:
        self.consumption.append(
            {
                "schema_version": SCHEMA_VERSION,
                "namespace": self.side,
                "pair_member": self.side,
                "match_side": self.side,
                "game_number": game_number,
                "prompt_type": prompt_type,
                "roll_index": roll_index,
                "physical_seat": seat,
                "engine": self.engine_by_seat[seat],
                "die1": die1,
                "die2": die2,
                "stream_id": stream_id(self.seed, game_number, seat),
                "stream_path": str(self._relative_stream_path(game_number, seat)),
            }
        )

    def opening_dice(self, game_number: int | None = None) -> tuple[int, int]:
        game = game_number or self.current_game_number
        index = self.opening_attempt_index.get(game, 0) + 1
        if index > self.roll_count:
            raise RuntimeError("opening dice stream exhausted")
        self.opening_attempt_index[game] = index
        o = dice_record(self.seed, 1, 7, game, "O", index)["opening_die"]
        x = dice_record(self.seed, 1, 7, game, "X", index)["opening_die"]
        self._record("opening", game, index, "O", o, None)
        self._record("opening", game, index, "X", x, None)
        if o > x:
            self.expected_next_roll_seat = "X"
        elif x > o:
            self.expected_next_roll_seat = "O"
        else:
            self.expected_next_roll_seat = None
        return o, x

    def checker_dice(self, seat: str, game_number: int | None = None) -> tuple[int, int]:
        game = game_number or self.current_game_number
        normalized = seat.upper()
        key = (game, normalized)
        index = self.checker_roll_index.get(key, 0) + 1
        if index > self.roll_count:
            raise RuntimeError("checker dice stream exhausted")
        self.checker_roll_index[key] = index
        record = dice_record(self.seed, 1, 7, game, normalized, index)
        self._record("checker", game, index, normalized, record["die1"], record["die2"])
        self.expected_next_roll_seat = opposite_seat(normalized)
        return record["die1"], record["die2"]

    def dice_for_prompt(self, output_before_prompt: str) -> tuple[int, int]:
        lower = output_before_prompt.lower()
        if "wins " in lower and " point" in lower:
            self.prepare_opening(self.current_game_number + 1)
        if self.expected_next_roll_seat is None:
            return self.opening_dice()
        return self.checker_dice(self.expected_next_roll_seat)

    def write_evidence(self) -> tuple[Path, Path]:
        streams = self.prepare_files()
        consumption_path = self.root / "seat_dice_consumption.jsonl"
        with consumption_path.open("w", encoding="utf-8", newline="") as handle:
            for entry in self.consumption:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "namespace": self.side,
            "namespace_seed": self.seed,
            "roll_count": self.roll_count,
            "files_per_match": self.files_per_match,
            "engine_by_physical_seat": self.engine_by_seat,
            "streams": streams,
            "consumption": {
                "path": consumption_path.name,
                "entries": len(self.consumption),
                "sha256": hashlib.sha256(consumption_path.read_bytes()).hexdigest(),
            },
        }
        manifest_path = self.root / "seat_dice_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest_path, consumption_path
