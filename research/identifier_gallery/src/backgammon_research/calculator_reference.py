from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


PACKAGE_NAME = "backgammoncalculator"
EXPECTED_VERSION = "0.2.0"
GITHUB_REPOSITORY = "backgammonsimplified/backgammoncalculator"
RELEASE_COMMIT = "a385a963ed01a6eac083dae7a1b246b1c150b3eb"


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
        argv = [str(self.rscript), "--vanilla", "-e", expression, *args]
        return subprocess.run(
            argv,
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
ns <- asNamespace("backgammoncalculator")
required <- c("xgid_to_gnuid", "gnuid_to_xgid", "position_from_gnuid", "position_from_xgid")
missing <- required[!vapply(required, exists, logical(1), envir=ns, inherits=FALSE)]
if (length(missing)) stop(paste("missing required API:", paste(missing, collapse=", ")))
d <- utils::packageDescription("backgammoncalculator")
fields <- c(
  package=as.character(d$Package),
  version=as.character(d$Version),
  remote_sha=ifelse(is.null(d$RemoteSha), "", as.character(d$RemoteSha)),
  remote_ref=ifelse(is.null(d$RemoteRef), "", as.character(d$RemoteRef)),
  installed_path=system.file(package="backgammoncalculator")
)
cat(paste(names(fields), fields, sep="=", collapse="\n"), "\n", sep="")
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
        version = metadata.get("version")
        if version != EXPECTED_VERSION:
            raise RuntimeError(
                "reconciliation gallery requires released backgammoncalculator {} but found {}".format(
                    EXPECTED_VERSION, version or "unknown"
                )
            )
        return {
            "reference": "released_cross_language_implementation",
            "package": metadata.get("package", PACKAGE_NAME),
            "package_version": version,
            "github_repository": GITHUB_REPOSITORY,
            "release_commit": RELEASE_COMMIT,
            "remote_sha": metadata.get("remote_sha") or None,
            "remote_ref": metadata.get("remote_ref") or None,
            "installed_path": metadata.get("installed_path"),
            "rscript": str(self.rscript),
            "r_library": str(self.r_library) if self.r_library else None,
            "entry_points": [
                "backgammoncalculator::xgid_to_gnuid",
                "backgammoncalculator::gnuid_to_xgid",
            ],
        }

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
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
            "cache_hit": False,
            "provenance": self.provenance,
        }
        self.cache_gnuid[gnuid] = result
        return result
