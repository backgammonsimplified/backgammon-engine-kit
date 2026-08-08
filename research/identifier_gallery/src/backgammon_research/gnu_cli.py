from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


POSITION_ID_RE = re.compile(r"Position ID\s*:\s*(\S+)")
MATCH_ID_RE = re.compile(r"Match ID\s*:\s*(\S+)")
VERSION_RE = re.compile(r"GNU Backgammon\s+([^\r\n]+)")


class GnuBackgammonCli:
    """Run the installed GNU Backgammon CLI as a live identifier oracle.

    The command-line form matches the GNU 1.08.003 Windows invocation retained
    in the s024 evidence: ``-q -t -r -D <dir> -P <dir> -c <commands>``.
    Each exact identifier is loaded independently; no scenario-only state is
    injected around it.
    """

    SHOW_COMMANDS = (
        "show board",
        "show fullboard",
        "show cube",
        "show dice",
        "show turn",
        "show score",
        "show matchlength",
        "show crawford",
        "show postcrawford",
        "show jacoby",
        "show beavers",
    )

    def __init__(
        self,
        executable: Path | str | None = None,
        datadir: Path | str | None = None,
        pkgdatadir: Path | str | None = None,
    ) -> None:
        self.executable = self.discover(executable)
        default_data = self.executable.parent
        self.datadir = Path(datadir).resolve() if datadir else default_data
        self.pkgdatadir = Path(pkgdatadir).resolve() if pkgdatadir else default_data
        self.cache: dict[str, dict[str, Any]] = {}
        self.provenance = {
            "runner": "live_gnu_backgammon_cli",
            "executable": str(self.executable),
            "datadir": str(self.datadir),
            "pkgdatadir": str(self.pkgdatadir),
            "load_policy": "exact identifier only; no scenario-only state injected",
            "command_contract": "-q -t -r -D <dir> -P <dir> -c <commands>",
        }

    @staticmethod
    def discover(explicit: Path | str | None = None) -> Path:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        for variable in ("GNUBG_EXECUTABLE", "GNUBG_EXE", "GNU_EXE"):
            value = os.environ.get(variable)
            if value:
                candidates.append(Path(value))
        for executable_name in ("gnubg-cli.exe", "gnubg-cli", "gnubg.exe", "gnubg"):
            found = shutil.which(executable_name)
            if found:
                candidates.append(Path(found))
        candidates.extend((
            Path("C:/Program Files (x86)/gnubg/gnubg-cli.exe"),
            Path("C:/Program Files/gnubg/gnubg-cli.exe"),
            Path("C:/Program Files (x86)/gnubg/gnubg.exe"),
            Path("C:/Program Files/gnubg/gnubg.exe"),
        ))
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        raise FileNotFoundError("GNU Backgammon CLI not found. Set GNUBG_EXECUTABLE or pass --gnu-exe.")

    @staticmethod
    def _load_command(identifier: str) -> str:
        value = identifier.strip()
        if value.upper().startswith("XGID="):
            return f"set xgid {value}"
        if value.count(":") == 1 and not any(character.isspace() for character in value):
            return f"set gnubgid {value}"
        raise ValueError(f"unsupported identifier for GNU CLI: {identifier!r}")

    @staticmethod
    def _extract_ids(stdout: str, exported_text: str | None) -> tuple[str | None, str | None, str | None]:
        combined = "\n".join(part for part in (stdout, exported_text or "") if part)
        position_match = POSITION_ID_RE.search(combined)
        match_match = MATCH_ID_RE.search(combined)
        position_id = position_match.group(1) if position_match else None
        match_id = match_match.group(1) if match_match else None
        complete = f"{position_id}:{match_id}" if position_id and match_id else None
        return position_id, match_id, complete

    @staticmethod
    def _rawboard(stdout: str) -> tuple[str | None, list[str]]:
        rawboard = next((line.strip() for line in stdout.splitlines() if line.startswith("board:")), None)
        if rawboard is None:
            return None, []
        return rawboard, rawboard.split(":")

    def load(self, identifier: str, scratch: Path, name: str) -> dict[str, Any]:
        cached = self.cache.get(identifier)
        if cached is not None:
            return {**cached, "cache_hit": True}
        scratch.mkdir(parents=True, exist_ok=True)
        export_path = (scratch / f"{name}.position.txt").resolve()
        command_path = (scratch / f"{name}.commands.txt").resolve()
        commands = [
            "set confirm new off", "set player 0 human", "set player 1 human",
            "set output rawboard off", self._load_command(identifier), *self.SHOW_COMMANDS,
            "set output rawboard on", "show board", "set output rawboard off",
            f'export position text "{export_path}"', "quit",
        ]
        command_path.write_text("\n".join(commands) + "\n", encoding="utf-8")
        argv = [str(self.executable), "-q", "-t", "-r", "-D", str(self.datadir), "-P", str(self.pkgdatadir), "-c", str(command_path)]
        started = time.perf_counter()
        completed = subprocess.run(
            argv, cwd=scratch, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False, env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
        duration_ms = (time.perf_counter() - started) * 1000
        exported_text = export_path.read_text(encoding="utf-8", errors="replace") if export_path.exists() else None
        position_id, match_id, complete_gnuid = self._extract_ids(completed.stdout, exported_text)
        rawboard, normalized_rawboard = self._rawboard(completed.stdout)
        version_match = VERSION_RE.search(completed.stdout)
        result = {
            "input": identifier, "argv": argv, "commands": commands, "stdout": completed.stdout,
            "stderr": completed.stderr, "exit_code": completed.returncode, "duration_ms": duration_ms,
            "exported_text": exported_text, "position_id": position_id, "match_id": match_id,
            "complete_gnuid": complete_gnuid, "rawboard": rawboard, "normalized_rawboard": normalized_rawboard,
            "gnu_version": version_match.group(1).strip() if version_match else None, "cache_hit": False,
        }
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"GNU CLI failed for {identifier!r} with exit code {completed.returncode}: {detail}")
        if complete_gnuid is None:
            raise RuntimeError(f"GNU CLI did not emit a complete GNUID for {identifier!r}.")
        self.cache[identifier] = result
        return result
