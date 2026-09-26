"""Runs Claude Code headless (`claude -p`). Only the contracts lane edits this file.

Call it as `claude_runner.run(...)` (module attribute), so tests can swap in FakeClaudeRunner.run.
"""

import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from string import Template
from typing import Any, TypeVar, overload

from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError

from meals.config import get_settings
from meals.contracts import ClaudeRunnerError

M = TypeVar("M", bound=BaseModel)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The child sees only these env vars: never ANTHROPIC_API_KEY (bills per call, disables Chrome)
# or CLAUDE_CODE_* (would attach it to a parent Claude Code session).
ENV_ALLOWLIST = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")

# Web only: no file tools, so a prompt-injected page can't read .env from disk.
ALLOWED_TOOLS = ("WebSearch", "WebFetch")

MAX_ATTEMPTS = 2
_SLOT_POLL_S = 0.1


class _TimedOut(Exception):
    pass


@overload
def run(prompt: str, schema: type[M], chrome: bool = False, timeout: int = 600) -> M: ...
@overload
def run(prompt: str, schema: None = None, chrome: bool = False, timeout: int = 600) -> Any: ...
def run(
    prompt: str,
    schema: type[BaseModel] | None = None,
    chrome: bool = False,
    timeout: int = 600,
) -> Any:
    """Run `claude -p` and return validated output.

    With `schema`: the model's JSON schema goes to `--json-schema`, and the returned
    `structured_output` is validated into a model instance. Without: `result` parsed as JSON.

    The prompt goes on stdin. The child runs with `--safe-mode --tools WebSearch,WebFetch
    --output-format json` (plus `--chrome` if asked), the ENV_ALLOWLIST env, and a fresh empty
    temp dir as cwd. It holds one of settings.claude_max_concurrent cross-process slots
    (settings.claude_lock_dir) for the whole run.

    Raises ClaudeRunnerError (with raw_output) on a non-zero exit, an is_error result, output
    that fails parsing or validation, or a timeout. Everything except a timeout is retried once
    (MAX_ATTEMPTS). On timeout the whole process group is killed.
    """
    command = _command(schema, chrome)
    with _slot():
        attempt = 1
        while True:
            try:
                return _parse(*_run_once(command, prompt, timeout), schema)
            except _TimedOut as exc:
                raise ClaudeRunnerError(f"claude timed out after {timeout}s", str(exc)) from None
            except ClaudeRunnerError:
                if attempt == MAX_ATTEMPTS:
                    raise
                attempt += 1


def load_prompt(lane: str, name: str, **variables: str) -> str:
    """Read PROMPTS_DIR/<lane>/<name>.md and fill `$placeholders` (string.Template).

    A missing variable raises KeyError, never a half-filled prompt.
    """
    template = (PROMPTS_DIR / lane / f"{name}.md").read_text(encoding="utf-8")
    return Template(template).substitute(variables)


def _command(schema: type[BaseModel] | None, chrome: bool) -> list[str]:
    command = [
        get_settings().claude_bin,
        "-p",
        "--safe-mode",
        "--output-format",
        "json",
        "--tools",
        ",".join(ALLOWED_TOOLS),
    ]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema.model_json_schema())]
    if chrome:
        command.append("--chrome")
    return command


def _child_env() -> dict[str, str]:
    return {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}


@contextmanager
def _slot() -> Iterator[None]:
    """Hold one of claude_max_concurrent OS-level file locks, waiting until one is free.

    OS locks are released by the kernel if the holder dies, so a crashed job can't leak a slot.
    `fallback_to_soft=False`: fail loudly on a filesystem without flock rather than silently
    switching to marker files that a crash would leave behind.
    """
    settings = get_settings()
    settings.claude_lock_dir.mkdir(parents=True, exist_ok=True)
    locks = [
        FileLock(settings.claude_lock_dir / f"claude-slot-{n}.lock", fallback_to_soft=False)
        for n in range(settings.claude_max_concurrent)
    ]
    while True:
        for lock in locks:
            try:
                lock.acquire(blocking=False)
            except Timeout:
                continue
            try:
                yield
            finally:
                lock.release()
            return
        time.sleep(_SLOT_POLL_S)


def _run_once(command: list[str], prompt: str, timeout: int) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory(prefix="meals-claude-") as cwd:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=_child_env(),
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise ClaudeRunnerError(f"could not start {command[0]!r}: {exc}") from exc
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise _TimedOut(_raw(stdout, stderr)) from None
    return process.returncode, stdout, stderr


def _parse(returncode: int, stdout: str, stderr: str, schema: type[BaseModel] | None) -> Any:
    raw = _raw(stdout, stderr)
    if returncode != 0:
        raise ClaudeRunnerError(f"claude exited with status {returncode}", raw)
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        raise ClaudeRunnerError("claude output is not JSON", raw) from None
    if not isinstance(envelope, dict):
        raise ClaudeRunnerError("claude output is not a result object", raw)
    if envelope.get("is_error"):
        raise ClaudeRunnerError("claude reported an error", raw)
    if schema is not None:
        if "structured_output" not in envelope:
            raise ClaudeRunnerError("claude returned no structured_output", raw)
        try:
            return schema.model_validate(envelope["structured_output"])
        except ValidationError as exc:
            raise ClaudeRunnerError(f"output failed {schema.__name__} validation", raw) from exc
    try:
        return json.loads(envelope.get("result", ""))
    except (json.JSONDecodeError, TypeError):
        raise ClaudeRunnerError("claude result is not JSON", raw) from None


def _raw(stdout: str | None, stderr: str | None) -> str:
    return f"{stdout or ''}\n--- stderr ---\n{stderr or ''}"
