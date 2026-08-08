from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PACKAGE_NAME = "backgammoncalculator"
EXPECTED_VERSION = "0.2.0"
GITHUB_REPOSITORY = "backgammonsimplified/backgammoncalculator"
REQUESTED_RELEASE_REF = "v0.2.0"
RELEASE_COMMIT = "a385a963ed01a6eac083dae7a1b246b1c150b3eb"


def release_provenance_matches(metadata: dict[str, str]) -> bool:
    """Accept immutable commit metadata or the requested immutable release tag."""

    commit_fields = ("remote_sha", "github_sha1")
    ref_fields = ("remote_ref", "github_ref")
    return any(metadata.get(field) == RELEASE_COMMIT for field in commit_fields) or any(
        metadata.get(field) == REQUESTED_RELEASE_REF for field in ref_fields
    )


class BackgammonCalculatorReference:
    """Call released backgammoncalculator 0.2.0 through Rscript.

    Calculator is a strong cross-language implementation reference, not an
    authority that overrides GNU semantics or the project position contract.
    The adapter records the installed package metadata used for each gallery.
    """

    def __init__(
        self,
        rscript: Path | str | None = None,
        r_library: Path | str | None = None,
    ) -> None:
        self.rscript = self._discover_rscript(rscript)
        env_library = os.environ.get("BACKGAMMONCALCULATOR_R_LIBRARY")
        self.r_library = (
            Path(r_library).expanduser().resolve()
            if r_library
            else (Path(env_library).expanduser().resolve() if env_library else None)
        )
        self.cache_xgid: dict[str, dict[str, Any]] = {}
        self.cache_gnuid: dict[str, dict[str, Any]] = {}
        self.cache_canonical: dict[str, dict[str, Any]] = {}
        self.provenance = self._verify_package()

    @staticmethod
    def _discover_rscript(explicit: Path | str | None = None) -> Path:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        for variable in ("RSCRIPT", "R_SCRIPT"):
            value = os.environ.get(variable)
            if value:
                candidates.append(Path(value))
        found = shutil.which("Rscript") or shutil.which("Rscript.exe")
        if found:
            candidates.append(Path(found))
        for root in (Path("C:/Program Files/R"), Path("C:/Program Files (x86)/R")):
            if root.exists():
                candidates.extend(sorted(root.glob("R-*/bin/Rscript.exe"), reverse=True))
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        raise FileNotFoundError("Rscript not found. Set RSCRIPT or put Rscript on PATH.")

    def _run(self, expression: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        argv = [str(self.rscript), "--vanilla", "-", *args]
        return subprocess.run(
            argv,
            input=expression,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=dict(os.environ),
        )

    def _verify_package(self) -> dict[str, Any]:
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- if (length(args) >= 1 && nzchar(args[[1]])) args[[1]] else NA_character_
if (!is.na(lib)) .libPaths(c(lib, .libPaths()))
if (!requireNamespace("backgammoncalculator", quietly=TRUE)) stop("backgammoncalculator is not installed")
required <- c("xgid_to_gnuid", "gnuid_to_xgid", "position_from_gnuid", "position_from_xgid")
missing <- setdiff(required, getNamespaceExports("backgammoncalculator"))
if (length(missing)) stop(paste("missing required API:", paste(missing, collapse=", ")))
emit <- function(name, value) cat(name, "=", value, "\n", sep="")
emit("package", "backgammoncalculator")
emit("version", as.character(utils::packageVersion("backgammoncalculator")))
emit("installed_path", system.file(package="backgammoncalculator"))
'''
        library_arg = str(self.r_library) if self.r_library else ""
        completed = self._run(expression, library_arg, timeout=60)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "<no stdout/stderr>"
            raise RuntimeError(
                "backgammoncalculator 0.2.0 is unavailable. "
                f"Rscript={self.rscript}; r_library={self.r_library}; detail={detail}"
            )
        metadata: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()
        installed_path = Path(metadata.get("installed_path", ""))
        description_path = installed_path / "DESCRIPTION"
        if not description_path.is_file():
            raise RuntimeError(
                f"Calculator DESCRIPTION not found at {description_path}"
            )
        for line in description_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if not line or line[0].isspace() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            description_key = {
                "RemoteSha": "remote_sha",
                "RemoteRef": "remote_ref",
                "GithubSHA1": "github_sha1",
                "GithubRef": "github_ref",
            }.get(key)
            if description_key:
                metadata[description_key] = value.strip()
        version = metadata.get("version")
        if version != EXPECTED_VERSION:
            raise RuntimeError(
                "reconciliation gallery requires released backgammoncalculator {} but found {}".format(
                    EXPECTED_VERSION, version or "unknown"
                )
            )
        if not release_provenance_matches(metadata):
            raise RuntimeError(
                "backgammoncalculator provenance mismatch: expected requested release "
                f"{REQUESTED_RELEASE_REF} resolving to {RELEASE_COMMIT}; "
                f"RemoteSha={metadata.get('remote_sha')!r}; "
                f"RemoteRef={metadata.get('remote_ref')!r}; "
                f"GithubSHA1={metadata.get('github_sha1')!r}; "
                f"GithubRef={metadata.get('github_ref')!r}"
            )
        return {
            "reference": "released_cross_language_implementation",
            "package": metadata.get("package", PACKAGE_NAME),
            "package_version": version,
            "github_repository": GITHUB_REPOSITORY,
            "requested_release_ref": REQUESTED_RELEASE_REF,
            "resolved_release_commit": RELEASE_COMMIT,
            "release_commit": RELEASE_COMMIT,
            "remote_sha": metadata.get("remote_sha") or None,
            "remote_ref": metadata.get("remote_ref") or None,
            "github_sha1": metadata.get("github_sha1") or None,
            "github_ref": metadata.get("github_ref") or None,
            "installed_path": metadata.get("installed_path"),
            "rscript": str(self.rscript),
            "r_library": str(self.r_library) if self.r_library else None,
            "command_transport": "Rscript --vanilla - <args>; R program supplied on stdin",
            "entry_points": [
                "backgammoncalculator::xgid_to_gnuid",
                "backgammoncalculator::gnuid_to_xgid",
            ],
            "canonical_entry_points": [
                "backgammoncalculator::position_from_xgid",
                "backgammoncalculator::position_from_gnuid",
            ],
            "verification": (
                "package version plus immutable resolved commit metadata or "
                "the requested immutable release tag"
            ),
        }

    @staticmethod
    def _parse_scalar(value: str) -> Any:
        if value == "":
            return None
        if value in {"TRUE", "FALSE"}:
            return value == "TRUE"
        try:
            return int(value)
        except ValueError:
            return value

    def canonical_position(self, identifier: str) -> dict[str, Any]:
        """Return Calculator's released canonical state in gallery fact shape."""

        cached = self.cache_canonical.get(identifier)
        if cached is not None:
            return cached
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]
identifier <- args[[2]]
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))
p <- if (startsWith(identifier, "XGID=")) {
  backgammoncalculator::position_from_xgid(identifier)
} else {
  backgammoncalculator::position_from_gnuid(identifier)
}
emit <- function(name, value) cat(name, "=", value, "\n", sep="")
emit("player_0_points", paste(p$players$player_0$points, collapse=","))
emit("player_1_points", paste(p$players$player_1$points, collapse=","))
emit("player_0_bar", p$players$player_0$bar)
emit("player_1_bar", p$players$player_1$bar)
emit("player_0_off", p$players$player_0$off)
emit("player_1_off", p$players$player_1$off)
emit("on_roll", p$turn$dice_owner)
emit("decision_player", p$turn$turn_owner)
emit("action", p$turn$action)
emit("dice", paste(p$turn$dice, collapse=","))
emit("cube_value", p$cube$value)
emit("cube_owner", p$cube$owner)
emit("score_player_0", p$score[["player_0"]])
emit("score_player_1", p$score[["player_1"]])
emit("match_length", p$match$length)
emit("crawford", p$match$crawford)
emit("jacoby", p$match$jacoby)
emit("maximum_cube", 2^p$cube$max_exponent)
'''
        library_arg = str(self.r_library) if self.r_library else ""
        completed = self._run(expression, library_arg, identifier)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"Calculator canonical decode failed for {identifier!r}: {detail}"
            )
        fields = dict(
            line.split("=", 1)
            for line in completed.stdout.splitlines()
            if "=" in line
        )
        points = {
            player: [int(value) for value in fields[f"{player}_points"].split(",")]
            for player in ("player_0", "player_1")
        }
        dice = [int(value) for value in fields.get("dice", "").split(",") if value]
        owner = fields.get("cube_owner")
        if owner == "centered":
            owner = "center"
        result = {
            "stable_player_identity": {
                "player_0": "player_0",
                "player_1": "player_1",
            },
            "checker_points": points,
            "bars": {
                player: int(fields[f"{player}_bar"])
                for player in ("player_0", "player_1")
            },
            "borne_off": {
                player: int(fields[f"{player}_off"])
                for player in ("player_0", "player_1")
            },
            "state": {
                "on_roll": fields.get("on_roll"),
                "decision_player": fields.get("decision_player"),
                "action": fields.get("action"),
                "dice": dice or None,
            },
            "cube": {
                "value": int(fields["cube_value"]),
                "owner": owner,
            },
            "score": {
                "player_0": int(fields["score_player_0"]),
                "player_1": int(fields["score_player_1"]),
                "match_length": int(fields["match_length"]),
            },
            "rules": {
                "crawford": self._parse_scalar(fields.get("crawford", "")),
                "jacoby": self._parse_scalar(fields.get("jacoby", "")),
                "beavers": None,
                "maximum_cube": int(fields["maximum_cube"]),
            },
        }
        self.cache_canonical[identifier] = result
        return result

    def xgid_to_gnuid(self, xgid: str) -> dict[str, Any]:
        cached = self.cache_xgid.get(xgid)
        if cached is not None:
            return {**cached, "cache_hit": True}
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]
xgid <- args[[2]]
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))
value <- backgammoncalculator::xgid_to_gnuid(xgid, allow_lossy=TRUE)
cat(value, "\n", sep="")
'''
        library_arg = str(self.r_library) if self.r_library else ""
        completed = self._run(expression, library_arg, xgid)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"backgammoncalculator::xgid_to_gnuid failed for {xgid!r}: {detail}")
        gnuid = next((line.strip() for line in completed.stdout.splitlines() if line.count(":") == 1), None)
        if not gnuid:
            raise RuntimeError("backgammoncalculator emitted no complete GNUID")
        result = {
            "input": xgid,
            "gnuid": gnuid,
            "argv": [str(self.rscript), "--vanilla", "-", library_arg, xgid],
            "r_script": expression,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
            "cache_hit": False,
            "allow_lossy": True,
            "provenance": self.provenance,
        }
        self.cache_xgid[xgid] = result
        return result

    def gnuid_to_xgid(self, gnuid: str) -> dict[str, Any]:
        cached = self.cache_gnuid.get(gnuid)
        if cached is not None:
            return {**cached, "cache_hit": True}
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]
gnuid <- args[[2]]
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))
value <- backgammoncalculator::gnuid_to_xgid(gnuid)
cat(value, "\n", sep="")
'''
        library_arg = str(self.r_library) if self.r_library else ""
        completed = self._run(expression, library_arg, gnuid)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"backgammoncalculator::gnuid_to_xgid failed for {gnuid!r}: {detail}")
        xgid = next((line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("XGID=")), None)
        if not xgid:
            raise RuntimeError("backgammoncalculator emitted no complete XGID")
        result = {
            "input": gnuid,
            "xgid": xgid,
            "argv": [str(self.rscript), "--vanilla", "-", library_arg, gnuid],
            "r_script": expression,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
            "cache_hit": False,
            "provenance": self.provenance,
        }
        self.cache_gnuid[gnuid] = result
        return result
