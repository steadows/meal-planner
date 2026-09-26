"""meals.claude_runner: the `claude -p` wrapper and prompt loading.

Authority: the claude_runner.py docstrings and constants, and the seam maps (`run()` /
`load_prompt()`; contracts-3-followup: kill on any exception, `load_prompt` hardening,
`validate_claude_output` and `RecipeOption.mealie_slug`). Unit tests drive a fake `claude`
executable written into tmp_path. The runner hands the child an allowlisted env, so the fake
can't be told through env where to write: it records each call to `calls.jsonl` and reads its
scripted behaviours from `script.json`, both next to itself.
"""

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from meals import claude_runner, contracts
from meals.config import get_settings
from meals.contracts import ClaudeRunnerError, Contract, Ingredient, RecipeOption, WeekProposal
from meals.fakes import FakeClaudeRunner

pytestmark = pytest.mark.usefixtures("isolated_settings")

ROOT = Path(__file__).resolve().parent.parent
PROMPT = 'Find three sheet-pan dinners.\nKeep the jalapeño mild and reply like {"ok": true}.'
RUN_TIMEOUT = 30  # bounds a hang if the runner never closes the child's stdin

# The fake `claude`. Behaviour N is used for call N; the last one repeats. With "grandchild", it
# first starts a sleeper that inherits its stdout, the way claude's own tool subprocesses would;
# "escaped" puts that sleeper in its own session, out of reach of a process-group kill.
FAKE_CLAUDE = """\
import json, os, subprocess, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
log = here / "calls.jsonl"


def record(event):
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(event) + "\\n").encode())
    finally:
        os.close(fd)


started = time.time()
earlier = log.read_text().splitlines() if log.exists() else []
index = sum(json.loads(line)["event"] == "start" for line in earlier)
behaviours = json.loads((here / "script.json").read_text())
behaviour = behaviours[min(index, len(behaviours) - 1)]
grandchild = None
if behaviour.get("grandchild"):
    sleeper = f"import time; time.sleep({behaviour.get('sleep', 0)})"
    escaped = behaviour["grandchild"] == "escaped"
    grandchild = subprocess.Popen([sys.executable, "-c", sleeper], start_new_session=escaped).pid
record({
    "event": "start",
    "pid": os.getpid(),
    "grandchild": grandchild,
    "time": started,
    "argv": sys.argv[1:],
    "stdin": sys.stdin.buffer.read().decode(),
    "cwd": os.getcwd(),
    "cwd_listing": sorted(os.listdir()),
    "env_keys": sorted(os.environ),
    "home": os.environ.get("HOME"),
    "path": os.environ.get("PATH"),
})
if "stdout_early" in behaviour:
    sys.stdout.write(behaviour["stdout_early"])
    sys.stdout.flush()
time.sleep(behaviour.get("sleep", 0))
# Before writing: an orphan whose reader is gone dies on the broken pipe.
record({"event": "end", "pid": os.getpid(), "time": time.time()})
sys.stdout.write(behaviour.get("stdout", ""))
sys.stderr.write(behaviour.get("stderr", ""))
sys.stdout.flush()
sys.stderr.flush()
sys.exit(behaviour.get("exit", 0))
"""


def _envelope(result: str, structured_output: object = None, is_error: bool = False) -> str:
    """The `--output-format json` result envelope, as claude 2.1.280 prints it."""
    envelope: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": result,
    }
    if structured_output is not None:
        envelope["structured_output"] = structured_output
    return json.dumps(envelope)


OK = {"stdout": _envelope(result='{"ok": true}', structured_output={"name": "eggs"})}


@pytest.fixture
def fake_claude_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> Path:
    """Directory holding the fake `claude`, wired in via CLAUDE_BIN, with a private lock dir."""
    home = tmp_path / "fake-claude"
    home.mkdir()
    binary = home / "claude"
    binary.write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
    binary.chmod(0o755)
    _script(home, OK)
    monkeypatch.setenv("CLAUDE_BIN", str(binary))
    monkeypatch.setenv("CLAUDE_LOCK_DIR", str(tmp_path / "locks"))
    return home


def _script(home: Path, *behaviours: Mapping[str, object]) -> None:
    (home / "script.json").write_text(json.dumps(behaviours))


def _events(home: Path, kind: str) -> list[dict[str, Any]]:
    log = home / "calls.jsonl"
    lines = log.read_text().splitlines() if log.exists() else []
    return [event for event in map(json.loads, lines) if event["event"] == kind]


def _calls(home: Path) -> list[dict[str, Any]]:
    return _events(home, "start")


def _flag_value(argv: list[str], flag: str) -> str | None:
    """The value of `--flag value` or `--flag=value`; None when the flag is absent."""
    for i, arg in enumerate(argv):
        if arg == flag:
            assert i + 1 < len(argv), f"{flag} is the last argument and has no value"
            return argv[i + 1]
        if arg.startswith(f"{flag}="):
            return arg.removeprefix(f"{flag}=")
    return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# ── the child's command line, stdin, env and cwd ─────────────────────────────


@pytest.mark.parametrize(
    ("schema", "chrome"), [(None, True), (Ingredient, False)], ids=["chrome", "schema"]
)
def test_argv_carries_the_required_flags(
    fake_claude_home: Path, schema: type[BaseModel] | None, chrome: bool
) -> None:
    claude_runner.run(PROMPT, schema=schema, chrome=chrome, timeout=RUN_TIMEOUT)

    (call,) = _calls(fake_claude_home)
    argv: list[str] = call["argv"]
    assert "-p" in argv or "--print" in argv
    assert "--safe-mode" in argv
    assert _flag_value(argv, "--output-format") == "json"
    assert ("--chrome" in argv) is chrome
    allowed = [_flag_value(argv, flag) for flag in ("--allowedTools", "--allowed-tools")]
    if chrome:
        # The logged-in Meijer session: no built-in tools at all, so no open web.
        assert _flag_value(argv, "--tools") == ""
        assert allowed == [None, None]
    else:
        # Pre-approved, so the run doesn't depend on ambient permission settings.
        assert _flag_value(argv, "--tools") == "WebSearch,WebFetch"
        assert "WebSearch,WebFetch" in allowed
    schema_arg = _flag_value(argv, "--json-schema")
    if schema is None:
        assert schema_arg is None
    else:
        assert schema_arg is not None
        assert json.loads(schema_arg) == schema.model_json_schema()


def test_prompt_goes_on_stdin_not_argv(fake_claude_home: Path) -> None:
    claude_runner.run(PROMPT, timeout=RUN_TIMEOUT)

    (call,) = _calls(fake_claude_home)
    assert call["stdin"] == PROMPT
    assert not any("sheet-pan" in arg for arg in call["argv"])


def test_child_env_is_allowlisted(fake_claude_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inheriting ANTHROPIC_API_KEY bills per call and breaks Chrome; CLAUDE_CODE_* attaches a session."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("MEALS_TEST_UNLISTED", "1")

    claude_runner.run(PROMPT, timeout=RUN_TIMEOUT)

    (call,) = _calls(fake_claude_home)
    # macOS: some Python builds (uv's 3.11) add this to their own environ at startup.
    env_keys = set(call["env_keys"]) - {"__CF_USER_TEXT_ENCODING"}
    assert env_keys <= set(claude_runner.ENV_ALLOWLIST)
    assert "ANTHROPIC_API_KEY" not in env_keys
    assert not [key for key in env_keys if key.startswith("CLAUDE_CODE_")]
    assert (call["home"], call["path"]) == (os.environ["HOME"], os.environ["PATH"])


def test_child_runs_in_an_empty_temp_dir_outside_the_project(
    fake_claude_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A prompt-injected page must not find `.env` (or anything else) in the child's cwd."""
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / ".env").write_text("TELEGRAM_BOT_TOKEN=not-for-claude\n")
    monkeypatch.chdir(caller)

    claude_runner.run(PROMPT, timeout=RUN_TIMEOUT)

    (call,) = _calls(fake_claude_home)
    cwd = Path(call["cwd"]).resolve()
    assert call["cwd_listing"] == []
    assert cwd not in (ROOT, caller.resolve())
    assert ROOT not in cwd.parents


# ── return values ────────────────────────────────────────────────────────────


def test_schema_run_returns_a_model_built_from_structured_output(
    fake_claude_home: Path,
) -> None:
    _script(
        fake_claude_home,
        {
            "stdout": _envelope(
                result='{"name": "not this one"}',
                structured_output={"name": "chicken thighs", "qty": 2, "unit": "lb"},
            )
        },
    )

    ingredient = claude_runner.run(PROMPT, schema=Ingredient, timeout=RUN_TIMEOUT)

    assert isinstance(ingredient, Ingredient)
    assert ingredient == Ingredient(name="chicken thighs", qty=2, unit="lb")


def test_schemaless_run_returns_the_parsed_result(fake_claude_home: Path) -> None:
    _script(fake_claude_home, {"stdout": _envelope(result='{"answer": 4, "items": ["a"]}')})

    assert claude_runner.run(PROMPT, timeout=RUN_TIMEOUT) == {"answer": 4, "items": ["a"]}


def test_schemaless_run_unwraps_a_json_code_fence(fake_claude_home: Path) -> None:
    _script(fake_claude_home, {"stdout": _envelope(result='```json\n{"answer": 4}\n```')})

    assert claude_runner.run(PROMPT, timeout=RUN_TIMEOUT) == {"answer": 4}


# ── failures and the single retry ────────────────────────────────────────────

# (schema, failing behaviour, markers of which raw_output must contain at least one).
# Each row is invalid for one reason only: is_error and nonzero_exit carry parseable JSON results,
# so a runner that skips that one check returns a value instead of raising.
NONZERO_EXIT = {
    "stdout": _envelope(result='{"partial": "marker-exit-stdout"}'),
    "stderr": "marker-exit-stderr",
    "exit": 1,
}
FAILURES = [
    pytest.param(
        None,
        {"stdout": _envelope(result='{"error": "marker-is-error"}', is_error=True)},
        ("marker-is-error",),
        id="is_error",
    ),
    pytest.param(
        None,
        NONZERO_EXIT,
        ("marker-exit-stdout", "marker-exit-stderr"),
        id="nonzero_exit",
    ),
    pytest.param(None, {"stdout": "marker-not-json"}, ("marker-not-json",), id="stdout_not_json"),
    pytest.param(
        None,
        {"stdout": _envelope(result="Sure! marker-result-prose")},
        ("marker-result-prose",),
        id="result_not_json",
    ),
    pytest.param(
        Ingredient,
        {"stdout": _envelope(result="{}", structured_output={"qty": "marker-invalid"})},
        ("marker-invalid",),
        id="fails_schema",
    ),
    pytest.param(
        Ingredient,
        {"stdout": _envelope(result="marker-no-structured-output")},
        ("marker-no-structured-output",),
        id="no_structured_output",
    ),
]


@pytest.mark.parametrize(("schema", "failure", "markers"), FAILURES)
def test_failure_is_retried_once_then_raised_with_raw_output(
    fake_claude_home: Path,
    schema: type[BaseModel] | None,
    failure: dict[str, object],
    markers: tuple[str, ...],
) -> None:
    _script(fake_claude_home, failure)

    with pytest.raises(ClaudeRunnerError) as caught:
        claude_runner.run(PROMPT, schema=schema, timeout=RUN_TIMEOUT)

    assert any(marker in caught.value.raw_output for marker in markers)
    assert len(_calls(fake_claude_home)) == 2


def test_failure_then_success_returns_the_second_answer(fake_claude_home: Path) -> None:
    _script(fake_claude_home, NONZERO_EXIT, OK)

    assert claude_runner.run(PROMPT, timeout=RUN_TIMEOUT) == {"ok": True}
    assert len(_calls(fake_claude_home)) == 2


def test_chrome_run_is_never_retried(fake_claude_home: Path) -> None:
    """A Chrome run adds items to the real cart; running it again would double the cart."""
    _script(fake_claude_home, NONZERO_EXIT, OK)

    with pytest.raises(ClaudeRunnerError):
        claude_runner.run(PROMPT, chrome=True, timeout=RUN_TIMEOUT)
    assert len(_calls(fake_claude_home)) == 1


def test_retry_logs_a_warning_with_the_failure_reason(
    fake_claude_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _script(fake_claude_home, NONZERO_EXIT)
    caplog.set_level(logging.WARNING, logger="meals.claude_runner")

    with pytest.raises(ClaudeRunnerError) as caught:
        claude_runner.run(PROMPT, timeout=RUN_TIMEOUT)

    # Both attempts fail the same way, so the retried failure's reason is the raised one's.
    assert any(caught.value.reason in message for message in _warnings(caplog))


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "meals.claude_runner" and record.levelno == logging.WARNING
    ]


def test_timeout_raises_without_retry_and_kills_the_process_group(
    fake_claude_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Killing only the child leaves its grandchild holding stdout, and the runner hangs on it."""
    _script(fake_claude_home, {"sleep": 20, "grandchild": True, **OK})
    caplog.set_level(logging.WARNING, logger="meals.claude_runner")
    started = time.monotonic()
    try:
        with pytest.raises(ClaudeRunnerError):
            claude_runner.run(PROMPT, timeout=1)
        elapsed = time.monotonic() - started

        assert elapsed < 10
        (call,) = _calls(fake_claude_home)
        assert not _alive(call["pid"])
        deadline = time.monotonic() + 5  # the orphaned grandchild is reaped asynchronously
        while _alive(call["grandchild"]) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(call["grandchild"])
        assert _warnings(caplog)
    finally:
        for call in _calls(fake_claude_home):
            _kill(call["pid"])
            _kill(call["grandchild"])


def test_timeout_does_not_wait_for_a_grandchild_that_left_the_group(
    fake_claude_home: Path,
) -> None:
    """A grandchild in its own session survives killpg and keeps stdout open. What the child
    wrote before the timeout must still reach raw_output."""
    behaviour = {"sleep": 30, "grandchild": "escaped", "stdout_early": "marker-partial", **OK}
    _script(fake_claude_home, behaviour)
    started = time.monotonic()
    try:
        with pytest.raises(ClaudeRunnerError) as caught:
            claude_runner.run(PROMPT, timeout=1)

        assert time.monotonic() - started < 20
        assert "marker-partial" in caught.value.raw_output
    finally:
        for call in _calls(fake_claude_home):
            _kill(call["pid"])
            _kill(call["grandchild"])


def test_timeout_falls_back_to_killing_the_child_when_killpg_is_refused(
    fake_claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS refuses killpg with EPERM when the group leader is an unreaped zombie."""

    def refuse(pgid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("meals.claude_runner.os.killpg", refuse)
    _script(fake_claude_home, {"sleep": 20, **OK})
    started = time.monotonic()
    try:
        with pytest.raises(ClaudeRunnerError):
            claude_runner.run(PROMPT, timeout=1)

        assert time.monotonic() - started < 10
        (call,) = _calls(fake_claude_home)
        assert not _alive(call["pid"])
    finally:
        for call in _calls(fake_claude_home):
            _kill(call["pid"])


def _interrupting(
    communicate: Callable[..., tuple[str, str]], interrupt: BaseException
) -> Callable[..., tuple[str, str]]:
    """A Popen.communicate that lets claude start, then raises `interrupt` mid-run."""

    def interrupted(
        process: subprocess.Popen[str], input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        with contextlib.suppress(subprocess.TimeoutExpired):
            communicate(process, input, timeout=1)  # claude starts, as in the timeout tests
        raise interrupt

    return interrupted


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt("marker-ctrl-c"), RuntimeError("marker-error")],
    ids=["keyboard_interrupt", "exception"],
)
def test_any_exception_mid_run_kills_the_process_group_and_propagates_unchanged(
    fake_claude_home: Path, monkeypatch: pytest.MonkeyPatch, interrupt: BaseException
) -> None:
    """`start_new_session` keeps Ctrl-C from reaching claude, so the runner must kill it. The
    exception is the caller's: re-raised as is, never retried or turned into ClaudeRunnerError."""
    # Not parametrized over chrome: the kill lives in _run_once, and chrome only changes argv and
    # the attempt count.
    _script(fake_claude_home, {"sleep": 20, "grandchild": True, **OK}, OK)
    monkeypatch.setenv("CLAUDE_MAX_CONCURRENT", "1")
    communicate = subprocess.Popen.communicate
    monkeypatch.setattr(subprocess.Popen, "communicate", _interrupting(communicate, interrupt))
    try:
        with pytest.raises(type(interrupt)) as caught:
            claude_runner.run(PROMPT, timeout=RUN_TIMEOUT)

        assert caught.value is interrupt
        (call,) = _calls(fake_claude_home)
        assert _events(fake_claude_home, "end") == []  # killed, not waited out
        assert not _alive(call["pid"])
        deadline = time.monotonic() + 5  # the orphaned grandchild is reaped asynchronously
        while _alive(call["grandchild"]) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(call["grandchild"])
        # The only slot is free again: another process's run gets it.
        monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
        assert _finish(_start_driver(dict(os.environ)), timeout=15) == {"ok": True}
    finally:
        for call in _calls(fake_claude_home):
            _kill(call["pid"])
            if call["grandchild"] is not None:
                _kill(call["grandchild"])


def test_exception_mid_run_still_kills_the_child_when_killpg_is_refused(
    fake_claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS refuses killpg with EPERM: the child still dies, and the caller gets its own
    exception, not the PermissionError. (The grandchild survives a refused killpg.)"""

    def refuse(pgid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("meals.claude_runner.os.killpg", refuse)
    _script(fake_claude_home, {"sleep": 20, "grandchild": True, **OK})
    interrupt = KeyboardInterrupt("marker-ctrl-c")
    interrupted = _interrupting(subprocess.Popen.communicate, interrupt)
    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            claude_runner.run(PROMPT, timeout=RUN_TIMEOUT)

        assert caught.value is interrupt
        (call,) = _calls(fake_claude_home)
        assert not _alive(call["pid"])
    finally:
        for call in _calls(fake_claude_home):
            _kill(call["pid"])
            if call["grandchild"] is not None:
                _kill(call["grandchild"])


# ── the cross-process slot cap ───────────────────────────────────────────────

DRIVER = (
    "import json; from meals import claude_runner; "
    f"print(json.dumps(claude_runner.run('hi', timeout={RUN_TIMEOUT})))"
)
HOLD_SECONDS = 0.75


def _start_driver(env: dict[str, str]) -> subprocess.Popen[str]:
    """A separate Python process calling run(): the bot and cron are separate processes."""
    return subprocess.Popen(
        [sys.executable, "-c", DRIVER],
        env=env,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(driver: subprocess.Popen[str], timeout: float = 60) -> object:
    try:
        stdout, stderr = driver.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        driver.kill()
        stdout, stderr = driver.communicate()
        pytest.fail(f"run() did not finish within {timeout}s\n{stderr}")
    assert driver.returncode == 0, stderr
    return json.loads(stdout)


@pytest.mark.parametrize(
    ("max_concurrent", "shared_lock_dir", "overlap"),
    [(1, True, False), (2, True, True), (1, False, True)],
    ids=["max1_serialises", "max2_overlaps", "separate_lock_dirs_overlap"],
)
def test_slot_cap_holds_across_processes(
    fake_claude_home: Path,
    tmp_path: Path,
    max_concurrent: int,
    shared_lock_dir: bool,
    overlap: bool,
) -> None:
    _script(fake_claude_home, {"sleep": HOLD_SECONDS, **OK})
    envs = [
        os.environ
        | {
            "CLAUDE_MAX_CONCURRENT": str(max_concurrent),
            "CLAUDE_LOCK_DIR": str(tmp_path / ("locks" if shared_lock_dir else f"locks-{i}")),
        }
        for i in range(2)
    ]
    drivers = [_start_driver(env) for env in envs]
    try:
        assert [_finish(driver) for driver in drivers] == [{"ok": True}] * 2
    finally:
        for driver in drivers:
            driver.kill()
            driver.wait()

    starts = {event["pid"]: event["time"] for event in _events(fake_claude_home, "start")}
    ends = {event["pid"]: event["time"] for event in _events(fake_claude_home, "end")}
    first, second = sorted(starts)
    overlapped = starts[first] < ends[second] and starts[second] < ends[first]
    assert overlapped is overlap


def _first_call(home: Path, holder: subprocess.Popen[str]) -> dict[str, Any]:
    """Wait until the holder's claude has started."""
    deadline = time.monotonic() + 30
    while not _calls(home):
        if holder.poll() is not None:
            pytest.fail(f"slot holder exited early:\n{holder.communicate()[1]}")
        assert time.monotonic() < deadline, "slot holder never started claude"
        time.sleep(0.05)
    return _calls(home)[0]


def test_slot_held_by_a_killed_process_is_released(fake_claude_home: Path, tmp_path: Path) -> None:
    """Why the slots are OS locks: a SIGKILLed holder must not block the next run forever."""
    _script(fake_claude_home, {"sleep": 60, **OK}, OK)
    env = os.environ | {"CLAUDE_MAX_CONCURRENT": "1", "CLAUDE_LOCK_DIR": str(tmp_path / "locks")}
    holder = _start_driver(env)
    try:
        orphan = _first_call(fake_claude_home, holder)
        holder.kill()
        holder.wait()
        _kill(orphan["pid"])

        assert _finish(_start_driver(env), timeout=15) == {"ok": True}
    finally:
        holder.kill()
        holder.wait()
        for call in _calls(fake_claude_home):
            _kill(call["pid"])


def test_slot_stays_held_while_a_killed_callers_claude_runs_on(
    fake_claude_home: Path, tmp_path: Path
) -> None:
    """The caller dies but its claude doesn't: that claude still counts against the cap."""
    _script(fake_claude_home, {"sleep": 2, **OK}, OK)
    env = os.environ | {"CLAUDE_MAX_CONCURRENT": "1", "CLAUDE_LOCK_DIR": str(tmp_path / "locks")}
    holder = _start_driver(env)
    successor: subprocess.Popen[str] | None = None
    try:
        orphan = _first_call(fake_claude_home, holder)
        holder.kill()
        holder.wait()
        successor = _start_driver(env)

        assert _finish(successor, timeout=30) == {"ok": True}
        deadline = time.monotonic() + 10
        while not (
            ends := [e for e in _events(fake_claude_home, "end") if e["pid"] == orphan["pid"]]
        ):
            assert time.monotonic() < deadline, "the orphaned claude never finished"
            time.sleep(0.05)
        (follower,) = [call for call in _calls(fake_claude_home) if call["pid"] != orphan["pid"]]
        assert follower["time"] >= ends[0]["time"]
    finally:
        for driver in (holder, successor):
            if driver is not None:
                driver.kill()
                driver.wait()
        for call in _calls(fake_claude_home):
            _kill(call["pid"])


# ── load_prompt ──────────────────────────────────────────────────────────────


@pytest.fixture
def prompts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    prompts = tmp_path / "prompts"
    (prompts / "planner").mkdir(parents=True)
    (prompts / "planner" / "weekly.md").write_text(
        'Plan the week for $kid.\nAnswer like {"meals": [{"name": "x"}]} and cc ${kid}.\n'
    )
    monkeypatch.setattr(claude_runner, "PROMPTS_DIR", prompts)
    return prompts


@pytest.mark.usefixtures("prompts_dir")
def test_load_prompt_fills_placeholders_and_leaves_json_braces_alone() -> None:
    assert claude_runner.load_prompt("planner", "weekly", kid="Miles") == (
        'Plan the week for Miles.\nAnswer like {"meals": [{"name": "x"}]} and cc Miles.\n'
    )


@pytest.mark.usefixtures("prompts_dir")
def test_load_prompt_missing_variable_raises_key_error() -> None:
    with pytest.raises(KeyError, match="kid"):
        claude_runner.load_prompt("planner", "weekly")


def test_load_prompt_leaves_dollar_amounts_alone(prompts_dir: Path) -> None:
    (prompts_dir / "cart").mkdir()
    (prompts_dir / "cart" / "fill.md").write_text("A $35 minimum and a $4.95 fee. Shop for $kid.\n")

    assert claude_runner.load_prompt("cart", "fill", kid="Miles") == (
        "A $35 minimum and a $4.95 fee. Shop for Miles.\n"
    )
    with pytest.raises(KeyError, match="kid"):
        claude_runner.load_prompt("cart", "fill")


@pytest.mark.usefixtures("prompts_dir")
def test_load_prompt_missing_file_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        claude_runner.load_prompt("planner", "no-such-prompt", kid="Miles")


def test_load_prompt_accepts_digits_underscores_and_hyphens(prompts_dir: Path) -> None:
    (prompts_dir / "lane_2-b").mkdir()
    (prompts_dir / "lane_2-b" / "week-1_v2.md").write_text("Shop for $kid.\n")

    assert claude_runner.load_prompt("lane_2-b", "week-1_v2", kid="Miles") == "Shop for Miles.\n"


# Each fails the `[a-z0-9_-]+` fullmatch (CWE-22). "<tmp>" stands for tmp_path, which holds
# secret.md next to prompts/: the path escapes would otherwise read it. Rows whose target doesn't
# exist also force the check before the read: reading first raises FileNotFoundError instead.
REJECTED_PROMPT_NAMES = [
    pytest.param("<tmp>", "secret", id="absolute_lane"),
    pytest.param("..", "secret", id="dotdot_lane"),
    pytest.param("planner", "../../secret", id="dotdot_name"),
    pytest.param("planner", "nested/../../../secret", id="dotdot_after_a_valid_prefix"),
    pytest.param("planner", "nested/deep", id="slash_in_name"),
    pytest.param("planner", "nested\\deep", id="backslash_in_name"),
    pytest.param("", "weekly", id="empty_lane"),
    pytest.param("planner", "", id="empty_name"),
    pytest.param("Planner", "weekly", id="uppercase_lane"),
    pytest.param("planner", "Weekly", id="uppercase_name"),
    pytest.param("planner", "weekly\n", id="trailing_newline"),
]


@pytest.mark.parametrize(("lane", "name"), REJECTED_PROMPT_NAMES)
def test_load_prompt_rejects_names_outside_the_allowlist(
    prompts_dir: Path, tmp_path: Path, lane: str, name: str
) -> None:
    (tmp_path / "secret.md").write_text("marker-outside-prompts $kid\n")
    (prompts_dir / "planner" / "nested").mkdir()
    (prompts_dir / "planner" / "nested" / "deep.md").write_text("marker-nested $kid\n")

    with pytest.raises(ValueError):
        claude_runner.load_prompt(lane.replace("<tmp>", str(tmp_path)), name, kid="Miles")


# ── Claude can't set RecipeOption.mealie_slug ────────────────────────────────


class _Picks(Contract):
    """Nests RecipeOption in a tuple field, the way a planner's draft would."""

    options: tuple[RecipeOption, ...]


# A RecipeOption as Claude returns it, and the same answer carrying a slug only the Mealie client
# may set.
RECIPE: dict[str, object] = {
    "name": "Sheet-pan gnocchi",
    "url": "https://example.com/sheet-pan-gnocchi",
    "source": "example.com",
    "hands_on_min": 10,
    "servings": 4,
    "batch_ok": True,
    "fit_note": "Quick, and Miles eats gnocchi",
    "ingredients": [{"name": "gnocchi", "qty": 1, "unit": "lb"}],
    "steps": ["Roast at 425F for 20 minutes."],
}
SLUGGED = {**RECIPE, "mealie_slug": "sheet-pan-gnocchi"}
NESTINGS = [
    pytest.param(RecipeOption, RECIPE, SLUGGED, id="top_level"),
    pytest.param(_Picks, {"options": [RECIPE]}, {"options": [SLUGGED]}, id="nested"),
]


@pytest.mark.parametrize("model", [RecipeOption, WeekProposal], ids=["top_level", "nested"])
def test_mealie_slug_is_hidden_from_the_schema_claude_is_given(model: type[BaseModel]) -> None:
    """run() sends model_json_schema() as --json-schema (test_argv_carries_the_required_flags)."""
    assert "mealie_slug" in RecipeOption.model_fields, "RecipeOption has no mealie_slug field"
    schema = model.model_json_schema()

    assert "mealie_slug" not in json.dumps(schema)
    definition = schema if model is RecipeOption else schema["$defs"]["RecipeOption"]
    assert definition["additionalProperties"] is False


@pytest.mark.parametrize(("schema", "answer", "slugged"), NESTINGS)
def test_claude_setting_mealie_slug_fails_validation_and_is_retried_once(
    fake_claude_home: Path,
    schema: type[BaseModel],
    answer: dict[str, object],
    slugged: dict[str, object],
) -> None:
    """The same answer without the slug is accepted, so the slug alone is what's rejected."""
    rejected = {"stdout": _envelope(result="{}", structured_output=slugged)}
    accepted = {"stdout": _envelope(result="{}", structured_output=answer)}
    _script(fake_claude_home, rejected, rejected, accepted)

    with pytest.raises(ClaudeRunnerError):
        claude_runner.run(PROMPT, schema=schema, timeout=RUN_TIMEOUT)
    assert len(_calls(fake_claude_home)) == 2

    result = claude_runner.run(PROMPT, schema=schema, timeout=RUN_TIMEOUT)
    option = result.options[0] if isinstance(result, _Picks) else result
    assert isinstance(option, RecipeOption)
    assert option.mealie_slug is None


@pytest.mark.parametrize(("schema", "answer", "slugged"), NESTINGS)
def test_fake_runner_rejects_mealie_slug_like_the_real_one(
    schema: type[BaseModel], answer: dict[str, object], slugged: dict[str, object]
) -> None:
    """A queued model is replayed as its dump, `mealie_slug: null` included, and that's accepted."""
    fake = FakeClaudeRunner([slugged, schema.model_validate(answer)])

    with pytest.raises(ClaudeRunnerError):
        fake.run(PROMPT, schema=schema)
    result = fake.run(PROMPT, schema=schema)
    option = result.options[0] if isinstance(result, _Picks) else result
    assert isinstance(option, RecipeOption)
    assert option.mealie_slug is None


def test_trusted_validation_keeps_mealie_slug_and_untrusted_validation_rejects_it() -> None:
    """The Mealie client and stored JSON set the slug; Claude's output never can."""
    trusted = RecipeOption.model_validate(SLUGGED)

    assert trusted.mealie_slug == "sheet-pan-gnocchi"
    assert RecipeOption.model_validate_json(trusted.model_dump_json()) == trusted
    with pytest.raises(ValidationError):
        contracts.validate_claude_output(RecipeOption, SLUGGED)
    with pytest.raises(ValidationError):
        RecipeOption.model_validate(SLUGGED, context={contracts.UNTRUSTED: True})
    assert contracts.validate_claude_output(RecipeOption, RECIPE) == RecipeOption.model_validate(
        RECIPE
    )


# A set slug must ASCII-fullmatch `[A-Za-z0-9_-]+`, the allowlist mealie's HTTP client enforces at
# the sink, on the trusted path too: a stored WeekProposal with a tampered slug fails to load.
@pytest.mark.parametrize(
    "slug",
    ["sheet-pan-chicken-fajitas", "Chili_2", None],
    ids=["hyphens", "mixed_case_underscore_digit", "none"],
)
def test_trusted_validation_accepts_a_mealie_slug_in_the_sink_allowlist(slug: str | None) -> None:
    assert RecipeOption.model_validate({**RECIPE, "mealie_slug": slug}).mealie_slug == slug


@pytest.mark.parametrize(
    "slug",
    ["../admin", "a/b", "a b", "", "slug\n", "café", "ｓｌｕｇ"],
    ids=["dotdot", "slash", "space", "empty", "trailing_newline", "non_ascii", "fullwidth"],
)
def test_trusted_validation_rejects_a_mealie_slug_outside_the_sink_allowlist(slug: str) -> None:
    assert "mealie_slug" in RecipeOption.model_fields, "RecipeOption has no mealie_slug field"

    with pytest.raises(ValidationError):
        RecipeOption.model_validate({**RECIPE, "mealie_slug": slug})


# ── integration: the real `claude` ───────────────────────────────────────────


@pytest.mark.integration
def test_real_claude_returns_a_validated_ingredient() -> None:
    """Definition of done for the lane. Needs `claude /login`; run with -m integration."""
    if shutil.which(get_settings().claude_bin) is None:
        pytest.skip(f"{get_settings().claude_bin!r} is not installed")
    ingredient = claude_runner.run("Return the ingredient: 2 lb chicken thighs", schema=Ingredient)

    assert isinstance(ingredient, Ingredient)
    assert "chicken" in ingredient.name.lower()
