"""Atomic durable ledger with explicit resumable pair transitions."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import CampaignConfig
from .identity import all_pair_identities


LEDGER_SCHEMA = "sage-gnu-campaign-ledger-v1"
VALID_STATES = frozenset(("planned", "started", "failed", "committed"))
ALLOWED_TRANSITIONS = {
    ("planned", "started"),
    ("started", "started"),
    ("started", "failed"),
    ("started", "committed"),
    ("failed", "started"),
}


class LedgerError(RuntimeError):
    """A ledger operation would violate campaign identity or state safety."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class CampaignLedger:
    def __init__(self, path: Path, clock: Callable[[], str] = utc_now):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.clock = clock

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LedgerError(f"campaign ledger is locked: {self.lock_path}") from exc
            os.ftruncate(fd, 0)
            os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"cannot load campaign ledger: {self.path}") from exc
        if data.get("schema_version") != LEDGER_SCHEMA:
            raise LedgerError("unsupported campaign ledger schema")
        return data

    def initialize(
        self,
        config: CampaignConfig,
        benchmarker_commit: str,
        engine_kit_commit: str,
    ) -> dict[str, Any]:
        if self.exists():
            data = self.load()
            self.assert_authority(data, config, benchmarker_commit, engine_kit_commit)
            return data
        now = self.clock()
        pairs = {}
        for identity in all_pair_identities(config):
            pairs[identity.pair_id] = {
                **identity.to_dict(),
                "state": "planned",
                "attempt_count": 0,
                "active_attempt": None,
                "transitions": [{"from": None, "to": "planned", "at_utc": now, "reason": "campaign-plan"}],
                "committed_marker_sha256": None,
            }
        data = {
            "schema_version": LEDGER_SCHEMA,
            "campaign_id": config.campaign_id,
            "campaign_configuration_sha256": config.content_sha256,
            "benchmarker_commit": benchmarker_commit,
            "engine_kit_source_commit": engine_kit_commit,
            "created_at_utc": now,
            "updated_at_utc": now,
            "pairs": pairs,
        }
        _write_atomic(self.path, data)
        return data

    @staticmethod
    def assert_authority(
        data: dict[str, Any],
        config: CampaignConfig,
        benchmarker_commit: str,
        engine_kit_commit: str,
    ) -> None:
        expected = {
            "campaign_id": config.campaign_id,
            "campaign_configuration_sha256": config.content_sha256,
            "benchmarker_commit": benchmarker_commit,
            "engine_kit_source_commit": engine_kit_commit,
        }
        conflicts = [key for key, value in expected.items() if data.get(key) != value]
        if conflicts:
            raise LedgerError("campaign ledger authority mismatch: " + ", ".join(conflicts))

    def transition(
        self,
        pair_id: str,
        target: str,
        *,
        reason: str,
        attempt: int | None = None,
        committed_marker_sha256: str | None = None,
    ) -> dict[str, Any]:
        if target not in VALID_STATES:
            raise LedgerError(f"unknown target state: {target}")
        data = self.load()
        try:
            pair = data["pairs"][pair_id]
        except KeyError as exc:
            raise LedgerError(f"unknown campaign pair: {pair_id}") from exc
        source = pair.get("state")
        if (source, target) not in ALLOWED_TRANSITIONS:
            raise LedgerError(f"invalid pair transition: {source} -> {target}")
        if target == "started":
            expected_attempt = int(pair["attempt_count"]) + 1
            if attempt != expected_attempt:
                raise LedgerError(f"next attempt must be {expected_attempt}")
            pair["attempt_count"] = attempt
            pair["active_attempt"] = attempt
        elif target == "committed":
            if not committed_marker_sha256:
                raise LedgerError("committed transition requires marker SHA-256")
            pair["committed_marker_sha256"] = committed_marker_sha256
            pair["active_attempt"] = None
        elif target == "failed":
            pair["active_attempt"] = None
        now = self.clock()
        pair["state"] = target
        pair["transitions"].append(
            {"from": source, "to": target, "at_utc": now, "reason": reason, "attempt": attempt}
        )
        data["updated_at_utc"] = now
        _write_atomic(self.path, data)
        return pair
