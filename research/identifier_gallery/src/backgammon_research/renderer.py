from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

PACKAGE_NAME = "backgammonboard"
GITHUB_REPOSITORY = "backgammonsimplified/backgammonboard"
EXPECTED_COMMIT = "a4ab56f712c9ecb8e8ad83782cc82d5b32d94883"
REQUIRED_PUBLIC_API = ("ggboard", "validate_xgid")


class BackgammonBoardRenderer:
    """Render complete XGIDs with one exact current-source board package."""

    def __init__(self, r_library: Path | str | None = None) -> None:
        self.cache: dict[str, dict[str, Any]] = {}
        self.r_library = Path(r_library).expanduser().resolve() if r_library else self._library_from_environment()
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
            if root.exists(): candidates.extend(root.glob("R-*/bin/Rscript.exe"))
        return sorted(candidates)[-1].resolve() if candidates else None

    @staticmethod
    def _library_from_environment() -> Path | None:
        value = os.environ.get("BACKGAMMONBOARD_R_LIBRARY")
        return Path(value).expanduser().resolve() if value else None

    def _run(self, expression: str, *args: str, timeout: int = 60, cwd: Path | None = None):
        return subprocess.run([str(self.rscript), "--vanilla", "-e", expression, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False, env=dict(os.environ))

    def _verify_installed_package(self) -> dict[str, Any]:
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]; required <- strsplit(args[[2]], ",", fixed=TRUE)[[1]]; expected_sha <- args[[3]]
.libPaths(c(lib, .libPaths()))
if (!requireNamespace("backgammonboard", quietly=TRUE)) stop("backgammonboard is not installed")
missing <- setdiff(required, getNamespaceExports("backgammonboard"))
if (length(missing)) stop(paste("missing required public API:", paste(missing, collapse=", ")))
d <- utils::packageDescription("backgammonboard")
remote_sha <- ifelse(is.null(d$RemoteSha), "", as.character(d$RemoteSha))
if (!identical(remote_sha, expected_sha)) stop(paste("renderer RemoteSha mismatch: expected", expected_sha, "found", remote_sha))
fields <- c(package=as.character(d$Package), version=as.character(d$Version), installed_path=system.file(package="backgammonboard"), remote_sha=remote_sha, remote_ref=ifelse(is.null(d$RemoteRef), "", as.character(d$RemoteRef)))
cat(paste(names(fields), fields, sep="=", collapse="\n"), "\n", sep="")
'''
        completed = self._run(expression, str(self.r_library), ",".join(REQUIRED_PUBLIC_API), EXPECTED_COMMIT)
        if completed.returncode:
            raise RuntimeError("Exact current backgammonboard unavailable: " + (completed.stderr.strip() or completed.stdout.strip()))
        metadata = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
        return {"renderer":"github_source_r_package","package":metadata.get("package",PACKAGE_NAME),"package_version":metadata.get("version"),"github_repository":GITHUB_REPOSITORY,"expected_commit":EXPECTED_COMMIT,"installed_path":metadata.get("installed_path"),"remote_sha":metadata.get("remote_sha"),"remote_ref":metadata.get("remote_ref") or None,"rscript":str(self.rscript),"r_library":str(self.r_library),"entry_point":"backgammonboard::ggboard","required_public_api":list(REQUIRED_PUBLIC_API),"perspective":"white","verification":"exact GitHub source commit and required public API"}

    def render(self, xgid: str, output_dir: Path, name: str) -> dict[str, Any]:
        cached = self.cache.get(xgid)
        if cached: return {**cached, "cache_hit": True}
        output_dir.mkdir(parents=True, exist_ok=True); output = (output_dir / f"{name}.svg").resolve()
        expression = r'''
args <- commandArgs(trailingOnly=TRUE)
lib <- args[[1]]; xgid <- args[[2]]; output <- args[[3]]
.libPaths(c(lib, .libPaths()))
backgammonboard::validate_xgid(xgid)
p <- backgammonboard::ggboard(xgid, perspective="white")
grDevices::svg(output, width=12, height=9.1, onefile=TRUE); print(p); invisible(grDevices::dev.off())
'''
        argv = [str(self.rscript), "--vanilla", "-e", expression, str(self.r_library), xgid, str(output)]
        completed = subprocess.run(argv, cwd=output_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False, env=dict(os.environ))
        if completed.returncode or not output.exists():
            return {"input":xgid,"type":"unavailable","output":"","argv":argv,"stdout":completed.stdout,"stderr":completed.stderr,"exit_code":completed.returncode,**self.provenance}
        rendered = output.read_text(encoding="utf-8", errors="replace"); start = rendered.lower().find("<svg")
        if start < 0:
            return {"input":xgid,"type":"unavailable","output":"","argv":argv,"stdout":completed.stdout,"stderr":"Renderer output did not contain SVG.","exit_code":1,**self.provenance}
        result={"input":xgid,"type":"svg","output":rendered[start:],"argv":argv,"stdout":completed.stdout,"stderr":completed.stderr,"exit_code":0,"cache_hit":False,**self.provenance};self.cache[xgid]=result;return result
