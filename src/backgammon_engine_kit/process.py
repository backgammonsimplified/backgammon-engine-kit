"""Bounded, shell-free process execution for future verified adapters."""

import subprocess
from dataclasses import dataclass
from typing import Optional


MAX_TIMEOUT_SECONDS = 3600.0


@dataclass(frozen=True)
class ProcessOutcome:
    status: str
    returncode: Optional[int]
    stdout: str
    stderr: str
    failure_code: Optional[str]

    def to_dict(self):
        return {
            "status": self.status,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failure_code": self.failure_code,
        }


def run_process(command, timeout_seconds, stdin_text=None, environment=None):
    if not isinstance(command, (tuple, list)) or not command or not all(isinstance(part, str) for part in command):
        raise ValueError("command must be a non-empty sequence of strings")
    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout must be greater than zero and no more than 3600 seconds")
    try:
        completed = subprocess.run(
            list(command),
            input=stdin_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout_seconds,
            shell=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return ProcessOutcome(
            status="failed",
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            failure_code="timeout",
        )
    return ProcessOutcome(
        status="complete" if completed.returncode == 0 else "failed",
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        failure_code=None if completed.returncode == 0 else "engine_failure",
    )
