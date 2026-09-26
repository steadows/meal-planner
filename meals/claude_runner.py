"""Runs Claude Code headless (`claude -p`). Only the contracts lane edits this file.

Call it as `claude_runner.run(...)` (module attribute), so tests can swap in FakeClaudeRunner.run.
"""

import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from string import Template
from typing import Any, TypeVar, overload

from pydantic import BaseModel, ValidationError

from meals.config import get_settings
from meals.contracts import ClaudeRunnerError, describe_rejection, validate_claude_output

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The child sees only these env vars: never ANTHROPIC_API_KEY (bills per call, disables Chrome)
# or CLAUDE_CODE_* (would attach it to a parent Claude Code session).
ENV_ALLOWLIST = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")

# Web only: no file tools, so a prompt-injected page can't read .env from disk. Chrome runs get
# none of these: the logged-in Meijer session must never reach the open web.
ALLOWED_TOOLS = ("WebSearch", "WebFetch")

MAX_ATTEMPTS = 2
_SLOT_POLL_S = 0.1
_KILL_DRAIN_S = 5
_JSON_FENCE = re.compile(r"\A\s*```(?:json)?[ \t]*\n(.*?)\n?```\s*\Z", re.DOTALL)
_PROMPT_PART = re.compile(
    r"[a-z0-9_-]+"
)  # no ".", "/" or "\": a lane or name can't leave PROMPTS_DIR


class _TimedOut(Exception):
    pass


class _PromptTemplate(Template):
    """`$name` and `${name}` are placeholders; any other `$` (e.g. "$35") is left as written."""

    pattern = r"""
    \$(?:
      (?P<escaped>\$) |
      (?P<named>[_a-z][_a-z0-9]*) |
      {(?P<braced>[_a-z][_a-z0-9]*)} |
      (?P<invalid>(?!))
    )
    """  # type: ignore[assignment]  # Template compiles a str pattern in __init_subclass__


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
    `structured_output` is validated into a model instance with `validate_claude_output`, which
    refuses fields only trusted code may set. Without: `result` parsed as JSON (a surrounding
    ```json fence is stripped); a caller that builds a model from it must use
    `validate_claude_output` too.

    The prompt goes on stdin. The child runs with `--safe-mode --output-format json`, the
    ENV_ALLOWLIST env, and a fresh empty temp dir as cwd. Plain runs get the ALLOWED_TOOLS,
    pre-approved; `chrome=True` runs get `--chrome` and no built-in tools. The run holds one of
    settings.claude_max_concurrent cross-process slots (settings.claude_lock_dir) throughout.

    Raises ClaudeRunnerError (with raw_output) on a non-zero exit, an is_error result, output
    that fails parsing or validation, or a timeout. A plain run retries once on anything except a
    timeout; a Chrome run is never retried, because it has side effects (a second run would
    double the cart). On timeout, and on any other exception once claude has started (Ctrl-C
    included), its process group is killed; that exception propagates as is, without a retry.
    Known gaps: Ctrl-C inside `Popen()` itself, before it returns, can't reach the child; and on
    macOS, if killpg is refused (zombie leader), only the leader is reaped.
    """
    command = _command(schema, chrome)
    attempts = 1 if chrome else MAX_ATTEMPTS
    with _slot() as slot_fd:
        attempt = 1
        while True:
            try:
                return _parse(*_run_once(command, prompt, timeout, slot_fd), schema)
            except _TimedOut as exc:
                logger.warning("claude timed out after %ss", timeout)
                raise ClaudeRunnerError(f"claude timed out after {timeout}s", str(exc)) from None
            except ClaudeRunnerError as exc:
                if attempt >= attempts:
                    raise
                logger.warning(
                    "claude attempt %d/%d failed, retrying: %s", attempt, attempts, exc.reason
                )
                attempt += 1


def load_prompt(lane: str, name: str, **variables: str) -> str:
    """Read PROMPTS_DIR/<lane>/<name>.md and fill `$placeholders`.

    A missing variable raises KeyError, never a half-filled prompt. A `$` not followed by a
    placeholder name (e.g. "$35") is left as written. `lane` and `name` must each be lowercase
    letters, digits, `_` or `-`, or ValueError is raised before any file is read, so neither can
    reach outside PROMPTS_DIR.
    """
    for part in (lane, name):
        if not _PROMPT_PART.fullmatch(part):
            raise ValueError(f"prompt lane and name must match {_PROMPT_PART.pattern}: {part!r}")
    template = (PROMPTS_DIR / lane / f"{name}.md").read_text(encoding="utf-8")
    return _PromptTemplate(template).substitute(variables)


def _command(schema: type[BaseModel] | None, chrome: bool) -> list[str]:
    tools = "" if chrome else ",".join(ALLOWED_TOOLS)
    command = [
        get_settings().claude_bin,
        "-p",
        "--safe-mode",
        "--output-format",
        "json",
        "--tools",
        tools,
    ]
    if chrome:
        command.append("--chrome")
    else:
        command += ["--allowedTools", tools]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema.model_json_schema())]
    return command


def _child_env() -> dict[str, str]:
    return {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}


@contextmanager
def _slot() -> Iterator[int]:
    """Hold one of claude_max_concurrent `flock` slots, waiting until one is free; yield its fd.

    The fd is passed to each `claude` child, so the lock lives as long as the last process
    holding it: a crashed caller can't free a slot while its orphaned claude still runs, and a
    dead one never leaks it. Lock files are never unlinked. A filesystem without flock raises.
    """
    settings = get_settings()
    settings.claude_lock_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        settings.claude_lock_dir / f"claude-slot-{n}.lock"
        for n in range(settings.claude_max_concurrent)
    ]
    while True:
        for path in paths:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            try:
                yield fd
            finally:
                os.close(fd)  # no LOCK_UN: a still-running child keeps the slot
            return
        time.sleep(_SLOT_POLL_S)


def _run_once(command: list[str], prompt: str, timeout: int, slot_fd: int) -> tuple[int, str, str]:
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
                pass_fds=(slot_fd,),
            )
        except OSError as exc:
            raise ClaudeRunnerError(f"could not start {command[0]!r}: {exc}") from exc
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            raise _TimedOut(_raw(*_drain(process))) from None
        except BaseException as exc:
            # Ctrl-C or any other error: start_new_session keeps the signal from reaching claude,
            # so it would run on (for a Chrome run, still filling the cart). Same as subprocess.run.
            logger.warning("claude run interrupted by %s; killing it", type(exc).__name__)
            _kill_tree(process)
            process.wait()
            _close_pipes(process)
            raise
    return process.returncode, stdout, stderr


def _kill_tree(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # macOS refuses killpg (EPERM) when the group leader is an unreaped zombie.
        process.kill()


def _drain(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Collect what the killed child wrote, without waiting on a descendant that left its group."""
    try:
        return process.communicate(timeout=_KILL_DRAIN_S)
    except subprocess.TimeoutExpired as exc:
        _close_pipes(process)
        process.wait()
        return _text(exc.output), _text(exc.stderr)


def _close_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()


def _text(data: str | bytes | None) -> str:
    """TimeoutExpired carries what was read so far, as bytes even in text mode."""
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return data or ""


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
            return validate_claude_output(schema, envelope["structured_output"])
        except ValidationError as exc:
            raise ClaudeRunnerError(describe_rejection(schema, exc), raw) from exc
    result = envelope.get("result")
    if not isinstance(result, str):
        raise ClaudeRunnerError("claude returned no result text", raw)
    fenced = _JSON_FENCE.match(result)
    try:
        return json.loads(fenced.group(1) if fenced else result)
    except json.JSONDecodeError:
        raise ClaudeRunnerError("claude result is not JSON", raw) from None


def _raw(stdout: str | None, stderr: str | None) -> str:
    return f"{stdout or ''}\n--- stderr ---\n{stderr or ''}"
