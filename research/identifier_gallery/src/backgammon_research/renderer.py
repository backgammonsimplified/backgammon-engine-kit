from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

PACKAGE_NAME = "backgammonboard"
GITHUB_REPOSITORY = "backgammonsimplified/backgammonboard"
EXPECTED_COMMIT = "0bc70d30e458642f41d4976948e49492c2c6117c"
EXPECTED_VERSION = "0.1.1"
REQUESTED_RELEASE_REF = "v0.1.1"
REQUIRED_PUBLIC_API = ("ggboard", "validate_xgid", "board_colors", "board_style")
COLOR_PRESET = "bs"
STYLE_PRESET = "bs"
PERSPECTIVE = "player_1"


def release_provenance_matches(metadata: dict[str, str]) -> bool:
    """Return whether installed metadata proves the requested Board release."""

    return (
        metadata.get("remote_sha") == EXPECTED_COMMIT
        or metadata.get("github_sha1") == EXPECTED_COMMIT
        or metadata.get("remote_ref") == REQUESTED_RELEASE_REF
        or metadata.get("github_ref") == REQUESTED_RELEASE_REF
    )


class BackgammonBoardRenderer:
    """Render complete XGIDs with the current BS backgammonboard release target."""

    def __init__(self, r_library: Path | str | None = None) -> None:
        self.cache: dict[str, dict[str, Any]] = {}
        self.r_library = Path(r_library).expanduser().resolve() if r_library else self._library_from_environment()
        calculator_library = os.environ.get("BACKGAMMONCALCULATOR_R_LIBRARY")
        self.calculator_r_library = (
            Path(calculator_library).expanduser().resolve()
            if calculator_library
            else None
        )
        self.rscript = self._discover_rscript()
        if self.rscript is None:
            raise RuntimeError("Rscript was not found.")
        if self.r_library is None:
            raise RuntimeError("BACKGAMMONBOARD_R_LIBRARY is not set; use the gallery launcher.")
        self.provenance = self._verify_installed_package()

    @staticmethod
    def _discover_rscript() -> Path | None:
        found = shutil.which("Rscript") or shutil.which("Rscript.exe")
        if found:
            return Path(found).resolve()
        candidates: list[Path] = []
        for root in (Path("C:/Program Files/R"), Path("C:/Program Files (x86)/R")):
            if root.exists():
                candidates.extend(root.glob("R-*/bin/Rscript.exe"))
        return sorted(candidates)[-1].resolve() if candidates else None

    @staticmethod
    def _library_from_environment() -> Path | None:
        value = os.environ.get("BACKGAMMONBOARD_R_LIBRARY")
        return Path(value).expanduser().resolve() if value else None

    def _run(self, expression: str, *args: str, timeout: int = 60, cwd: Path | None = None):
        return subprocess.run(
            [str(self.rscript), "--vanilla", "-", *args],
            input=expression,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=dict(os.environ),
        )

    def _verify_installed_package(self) -> dict[str, Any]:
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]
required <- strsplit(args[[2]], ",", fixed=TRUE)[[1]]
expected_sha <- args[[3]]
expected_version <- args[[4]]
requested_ref <- args[[5]]
.libPaths(c(lib, .libPaths()))
if (!requireNamespace("backgammonboard", quietly=TRUE)) stop("backgammonboard is not installed")
missing <- setdiff(required, getNamespaceExports("backgammonboard"))
if (length(missing)) stop(paste("missing required public API:", paste(missing, collapse=", ")))
d <- utils::packageDescription("backgammonboard", lib.loc=lib)
remote_sha <- ifelse(is.null(d$RemoteSha), "", as.character(d$RemoteSha))
remote_ref <- ifelse(is.null(d$RemoteRef), "", as.character(d$RemoteRef))
github_sha1 <- ifelse(is.null(d$GithubSHA1), "", as.character(d$GithubSHA1))
github_ref <- ifelse(is.null(d$GithubRef), "", as.character(d$GithubRef))
if (!identical(remote_sha, expected_sha) && !identical(github_sha1, expected_sha) &&
    !identical(remote_ref, requested_ref) && !identical(github_ref, requested_ref)) {
  stop(paste("renderer source mismatch: requested", requested_ref, "resolved", expected_sha,
             "found RemoteSha", remote_sha, "RemoteRef", remote_ref,
             "GithubSHA1", github_sha1, "GithubRef", github_ref))
}
if (!identical(as.character(d$Version), expected_version)) {
  stop(paste("renderer version mismatch: expected", expected_version, "found", as.character(d$Version)))
}
fields <- c(
  package=as.character(d$Package),
  version=as.character(d$Version),
  installed_path=system.file(package="backgammonboard", lib.loc=lib),
  remote_sha=remote_sha,
  remote_ref=remote_ref,
  github_sha1=github_sha1,
  github_ref=github_ref
)
cat(paste(names(fields), fields, sep="=", collapse="\n"), "\n", sep="")
'''
        completed = self._run(
            expression,
            str(self.r_library),
            ",".join(REQUIRED_PUBLIC_API),
            EXPECTED_COMMIT,
            EXPECTED_VERSION,
            REQUESTED_RELEASE_REF,
        )
        if completed.returncode:
            raise RuntimeError(
                "Exact current backgammonboard unavailable: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        metadata = dict(
            line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
        )
        resolved_commit = EXPECTED_COMMIT
        return {
            "renderer": "github_source_r_package",
            "package": metadata.get("package", PACKAGE_NAME),
            "package_version": metadata.get("version"),
            "github_repository": GITHUB_REPOSITORY,
            "requested_release_ref": REQUESTED_RELEASE_REF,
            "resolved_release_commit": EXPECTED_COMMIT,
            "expected_commit": EXPECTED_COMMIT,
            "resolved_commit": resolved_commit,
            "installed_path": metadata.get("installed_path"),
            "remote_sha": metadata.get("remote_sha") or None,
            "remote_ref": metadata.get("remote_ref") or None,
            "github_sha1": metadata.get("github_sha1") or None,
            "github_ref": metadata.get("github_ref") or None,
            "rscript": str(self.rscript),
            "r_library": str(self.r_library),
            "entry_point": "backgammonboard::ggboard",
            "required_public_api": list(REQUIRED_PUBLIC_API),
            "color_preset": COLOR_PRESET,
            "style_preset": STYLE_PRESET,
            "perspective": PERSPECTIVE,
            "light_player": "near_player",
            "player_name_style": "checker",
            "score_format": "both",
            "verification": "release tag or immutable commit metadata, package version, required public API, and BS render presets",
        }

    @staticmethod
    def _parse_board_state(stdout: str) -> dict[str, Any] | None:
        fields = dict(
            line[5:].split("=", 1)
            for line in stdout.splitlines()
            if line.startswith("FACT:") and "=" in line
        )
        if not fields:
            return None

        def integer(name: str, default: int = 0) -> int:
            value = fields.get(name, "")
            return default if value in {"", "NA"} else int(value)

        def boolean(name: str) -> bool | None:
            value = fields.get(name, "")
            if value in {"TRUE", "FALSE"}:
                return value == "TRUE"
            return None

        def points(player: str) -> list[int]:
            return [int(value) for value in fields[f"{player}_points"].split(",")]

        dice = [int(value) for value in fields.get("dice", "").split(",") if value]
        return {
            "stable_player_identity": {
                "player_0": "player_0",
                "player_1": "player_1",
            },
            "checker_points": {
                "player_0": points("player_0"),
                "player_1": points("player_1"),
            },
            "bars": {
                "player_0": integer("player_0_bar"),
                "player_1": integer("player_1_bar"),
            },
            "borne_off": {
                "player_0": integer("player_0_off"),
                "player_1": integer("player_1_off"),
            },
            "state": {
                "on_roll": fields.get("on_roll") or None,
                "decision_player": fields.get("decision_player") or None,
                "action": fields.get("action") or None,
                "dice": dice or None,
            },
            "cube": {
                "value": integer("cube_value", 1),
                "owner": fields.get("cube_owner") or None,
            },
            "score": {
                "player_0": integer("score_player_0"),
                "player_1": integer("score_player_1"),
                "match_length": integer("match_length"),
            },
            "rules": {
                "crawford": boolean("crawford"),
                "jacoby": boolean("jacoby"),
                "beavers": boolean("beavers"),
                "maximum_cube": integer("maximum_cube"),
            },
        }

    def _render_identifier(
        self, identifier: str, output_dir: Path, name: str
    ) -> dict[str, Any]:
        cached = self.cache.get(identifier)
        if cached:
            return {**cached, "cache_hit": True}
        output_dir.mkdir(parents=True, exist_ok=True)
        output = (output_dir / f"{name}.svg").resolve()
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]
calculator_lib <- args[[2]]
identifier <- args[[3]]
output <- args[[4]]
paths <- c(lib, .libPaths())
if (nzchar(calculator_lib)) paths <- c(lib, calculator_lib, .libPaths())
.libPaths(paths)
if (startsWith(identifier, "XGID=")) backgammonboard::validate_xgid(identifier)
colors <- backgammonboard::board_colors("bs")
style <- backgammonboard::board_style("bs")
p <- backgammonboard::ggboard(
  identifier,
  colors=colors,
  style=style,
  perspective="player_1",
  light_player="near_player",
  player_name_style="checker",
  score_format="both",
  point_1_side="right"
)
position <- attr(p, "backgammon_position", exact=TRUE)
if (is.null(position)) stop("ggboard did not retain its public factual position")
emit <- function(name, value) cat("FACT:", name, "=", value, "\n", sep="")
occupancy <- position$point_occupancy
emit("player_0_points", paste(ifelse(occupancy$owner == "player_0", occupancy$count, 0L), collapse=","))
emit("player_1_points", paste(ifelse(occupancy$owner == "player_1", occupancy$count, 0L), collapse=","))
emit("player_0_bar", position$bar[["player_0"]])
emit("player_1_bar", position$bar[["player_1"]])
emit("player_0_off", position$off[["player_0"]])
emit("player_1_off", position$off[["player_1"]])
emit("on_roll", position$on_roll)
decision_player <- if (length(position$dice) && !all(is.na(position$dice))) {
  position$on_roll
} else if (!is.null(position$cube_action) && position$cube_action != "none") {
  if (position$on_roll == "player_0") "player_1" else "player_0"
} else {
  position$on_roll
}
emit("decision_player", decision_player)
emit("action", if (length(position$dice) && !all(is.na(position$dice))) "roll" else position$cube_action)
emit("dice", paste(position$dice[!is.na(position$dice)], collapse=","))
emit("cube_value", position$cube_value)
emit("cube_owner", position$cube_owner)
emit("score_player_0", position$score[["player_0"]])
emit("score_player_1", position$score[["player_1"]])
emit("match_length", ifelse(is.na(position$match_length), 0L, position$match_length))
emit("crawford", identical(position$crawford_status, "crawford"))
emit("jacoby", position$jacoby)
emit("beavers", position$beavers_allowed)
emit("maximum_cube", position$max_cube)
grDevices::svg(output, width=12, height=9.1, bg=colors$outside_fill, onefile=TRUE)
print(p)
invisible(grDevices::dev.off())
'''
        argv = [
            str(self.rscript),
            "--vanilla",
            "-",
            str(self.r_library),
            str(self.calculator_r_library) if self.calculator_r_library else "",
            identifier,
            str(output),
        ]
        completed = subprocess.run(
            argv,
            input=expression,
            cwd=output_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            env=dict(os.environ),
        )
        if completed.returncode or not output.exists():
            return {
                "input": identifier,
                "type": "unavailable",
                "output": "",
                "argv": argv,
                "r_script": expression,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "exit_code": completed.returncode,
                **self.provenance,
            }
        rendered = output.read_text(encoding="utf-8", errors="replace")
        start = rendered.lower().find("<svg")
        if start < 0:
            return {
                "input": identifier,
                "type": "unavailable",
                "output": "",
                "argv": argv,
                "r_script": expression,
                "stdout": completed.stdout,
                "stderr": "Renderer output did not contain SVG.",
                "exit_code": 1,
                **self.provenance,
            }
        result = {
            "input": identifier,
            "type": "svg",
            "output": rendered[start:],
            "argv": argv,
            "r_script": expression,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": 0,
            "cache_hit": False,
            "factual_state": self._parse_board_state(completed.stdout),
            **self.provenance,
        }
        self.cache[identifier] = result
        return result

    def render(self, xgid: str, output_dir: Path, name: str) -> dict[str, Any]:
        if not xgid.startswith("XGID="):
            raise ValueError("render() requires a complete XGID")
        return self._render_identifier(xgid, output_dir, name)

    def render_gnuid(self, gnuid: str, output_dir: Path, name: str) -> dict[str, Any]:
        if gnuid.startswith("XGID=") or gnuid.count(":") != 1:
            raise ValueError("render_gnuid() requires a complete GNUID")
        return self._render_identifier(gnuid, output_dir, name)
