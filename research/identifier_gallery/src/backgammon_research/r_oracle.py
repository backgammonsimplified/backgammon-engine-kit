from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PACKAGE_NAME = "bglab"
GITHUB_REPOSITORY = "lassehjorthmadsen/bglab"
GITHUB_REF = "main"
PUBLIC_FUNCTION = "gnuid2xgid"


def split_complete_gnuid(value: str) -> tuple[str, str]:
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"complete GNUID required: {value!r}")
    return parts[0], parts[1]


class BglabGnuidOracle:
    """Call the current GitHub bglab GNUID→XGID implementation through R."""

    def __init__(self, rscript: Path | str | None = None, r_library: Path | str | None = None) -> None:
        self.rscript = self._discover_rscript(rscript)
        self.r_library = Path(r_library).expanduser().resolve() if r_library else self._library_from_environment()
        self.cache: dict[str, dict[str, Any]] = {}
        self.provenance = self._verify_package()

    @staticmethod
    def _discover_rscript(explicit: Path | str | None = None) -> Path:
        preferred: list[Path] = []
        if explicit:
            preferred.append(Path(explicit))
        for variable in ("RSCRIPT", "R_SCRIPT"):
            value = os.environ.get(variable)
            if value:
                preferred.append(Path(value))
        found = shutil.which("Rscript") or shutil.which("Rscript.exe")
        if found:
            preferred.append(Path(found))
        for candidate in preferred:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        fallbacks: list[Path] = []
        for root in (Path("C:/Program Files/R"), Path("C:/Program Files (x86)/R")):
            if root.exists():
                fallbacks.extend(sorted(root.glob("R-*/bin/Rscript.exe"), reverse=True))
        for candidate in fallbacks:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        raise FileNotFoundError("Rscript not found. Set RSCRIPT or put Rscript on PATH.")

    @staticmethod
    def _library_from_environment() -> Path | None:
        value = os.environ.get("BGLAB_R_LIBRARY") or os.environ.get("R_LIBS_USER")
        return Path(value).expanduser().resolve() if value else None

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.r_library is not None:
            env["R_LIBS_USER"] = str(self.r_library)
        return env

    def _run(self, expression: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        argv = [str(self.rscript), "--vanilla", "-e", expression, *args]
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False, env=self._env())

    def _verify_package(self) -> dict[str, Any]:
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- if (length(args) >= 1 && nzchar(args[[1]])) args[[1]] else NA_character_
if (!is.na(lib)) .libPaths(c(lib, .libPaths()))
if (!requireNamespace("bglab", quietly=TRUE)) stop("bglab is not installed")
ns <- asNamespace("bglab")
if (!exists("gnuid2xgid", envir=ns, inherits=FALSE)) stop("bglab::gnuid2xgid is unavailable")
d <- utils::packageDescription("bglab")
fields <- c(package=as.character(d$Package), version=as.character(d$Version),
 remote_sha=ifelse(is.null(d$RemoteSha), "", as.character(d$RemoteSha)),
 remote_ref=ifelse(is.null(d$RemoteRef), "", as.character(d$RemoteRef)),
 remote_repo=ifelse(is.null(d$RemoteRepo), "", as.character(d$RemoteRepo)),
 remote_username=ifelse(is.null(d$RemoteUsername), "", as.character(d$RemoteUsername)),
 installed_path=system.file(package="bglab"))
cat(paste(names(fields), fields, sep="=", collapse="\n"), "\n", sep="")
'''
        library_arg = str(self.r_library) if self.r_library is not None else ""
        completed = self._run(expression, library_arg, timeout=60)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "<no stdout/stderr>"
            raise RuntimeError(f"Current GitHub bglab package is unavailable. Rscript={self.rscript}; exit_code={completed.returncode}; r_library={self.r_library}; detail={detail}")
        metadata: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()
        return {
            "oracle": "current_github_r_package", "package": metadata.get("package", PACKAGE_NAME),
            "package_version": metadata.get("version"), "github_repository": GITHUB_REPOSITORY,
            "requested_ref": GITHUB_REF, "remote_sha": metadata.get("remote_sha") or None,
            "remote_ref": metadata.get("remote_ref") or None, "remote_repo": metadata.get("remote_repo") or None,
            "remote_username": metadata.get("remote_username") or None, "installed_path": metadata.get("installed_path"),
            "rscript": str(self.rscript), "r_library": str(self.r_library) if self.r_library else None,
            "entry_point": "bglab::gnuid2xgid", "stability": "moving GitHub default branch; commit recorded at build time",
        }

    def convert(self, complete_gnuid: str) -> dict[str, Any]:
        cached = self.cache.get(complete_gnuid)
        if cached is not None:
            return {**cached, "cache_hit": True}
        position_id, match_id = split_complete_gnuid(complete_gnuid)
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]
pos_id <- args[[2]]
match_id <- args[[3]]
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))
if (!requireNamespace("bglab", quietly=TRUE)) stop("bglab is not installed")
xgid <- bglab::gnuid2xgid(pos_id=pos_id, match_id=match_id)
if (length(xgid) != 1 || is.na(xgid) || !startsWith(xgid, "XGID=")) stop("bglab::gnuid2xgid did not return one complete XGID")
cat(xgid, "\n", sep="")
'''
        library_arg = str(self.r_library) if self.r_library is not None else ""
        argv = [str(self.rscript), "--vanilla", "-e", expression, library_arg, position_id, match_id]
        completed = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False, env=self._env())
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"bglab::gnuid2xgid failed for {complete_gnuid!r}: {detail}")
        xgid = next((line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("XGID=")), None)
        if xgid is None:
            raise RuntimeError(f"bglab::gnuid2xgid emitted no complete XGID for {complete_gnuid!r}")
        result = {"input": complete_gnuid, "position_id": position_id, "match_id": match_id, "xgid": xgid,
                  "argv": argv, "stdout": completed.stdout, "stderr": completed.stderr, "exit_code": completed.returncode,
                  "cache_hit": False, "provenance": self.provenance}
        self.cache[complete_gnuid] = result
        return result


def write_provenance(path: Path, provenance: dict[str, Any]) -> None:
    path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
