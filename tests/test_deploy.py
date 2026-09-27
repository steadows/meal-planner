"""deploy/: the five launchd agents, install.sh, uninstall.sh, drain.py and README.md (P3).

Authority: the P3 seam map (.context/seams/P3.md: D1-D12 and its RED list, items 1-10) and ADR-0001
(Decision :48-54; Jobs table :84-90; Consequences, rollback steps 1-4 :246-259; Integration Test
Points, "Plists").

Nothing here reaches the real launchd. The scripts run from a copy of deploy/ under tmp_path, so
ROOT, and every render, lock and log, is there too. HOME is a tmp dir, and PATH is built, not
inherited: first a shim dir holding a fake `launchctl` (it logs its argv; `print` succeeds only for
the labels in FAKE_LAUNCHCTL_LOADED) and a stub `claude`, then uv's dir and the system dirs. Every
test that waits on processes has its own namespace: MEALS_DRAIN_PATTERN is `<tag> (bot|job)` with
a fresh `meals<uuid>` tag, so a real bot or job on this machine never stalls it (D11). Child
processes are real and short-lived: a "<tag> job" that holds no lock, and flock holders whose argv
isn't a job's; each is SIGKILLed with its process group at teardown. install.sh tests that render
need plutil (macOS only); the uninstall and drain tests also run on the ubuntu CI.
"""

import ast
import contextlib
import fcntl
import importlib.util
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
AGENTS = ("bot", "sat_propose", "sat_nudge", "sun_autoapprove", "reconcile")  # D1
JOBS = AGENTS[1:]
LABELS = tuple(f"local.meals.{agent}" for agent in AGENTS)
PLIST_NAMES = sorted(f"{label}.plist" for label in LABELS)
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
UV = shutil.which("uv")
UID = os.getuid()
SAFE = "safe to switch code"  # D10 step 3
DETROIT = "/usr/share/zoneinfo/America/Detroit"

# D4: sat_propose and sun_autoapprove fire hourly 08:00-20:00 (13 entries), sat_nudge 16:00-21:00
# (6); reconcile is the single dict {Minute 15}.
SCHEDULES: dict[str, Any] = {
    "sat_propose": [{"Weekday": 6, "Hour": hour, "Minute": 0} for hour in range(8, 21)],
    "sat_nudge": [{"Weekday": 6, "Hour": hour, "Minute": 0} for hour in range(16, 22)],
    "sun_autoapprove": [{"Weekday": 0, "Hour": hour, "Minute": 0} for hour in range(8, 21)],
    "reconcile": {"Minute": 15},
}

needs_plutil = pytest.mark.skipif(
    shutil.which("plutil") is None, reason="install.sh lints with plutil, which is macOS-only"
)

# The fake launchctl (RED list, Instruments). It logs each call's argv tab-separated, one call per
# line. `print gui/<uid>/<label>` exits 0 only for a label listed in FAKE_LAUNCHCTL_LOADED, and 113
# (launchctl's "not loaded") otherwise; `bootout` exits FAKE_LAUNCHCTL_BOOTOUT_EXIT (default 0).
LAUNCHCTL = r"""#!/bin/bash
(IFS=$'\t'; printf '%s\n' "$*") >> "$FAKE_LAUNCHCTL_LOG"
case "$1" in
  print)
    for label in ${FAKE_LAUNCHCTL_LOADED:-}; do
      [ "$label" = "${2##*/}" ] && exit 0
    done
    exit 113 ;;
  bootout) exit "${FAKE_LAUNCHCTL_BOOTOUT_EXIT:-0}" ;;
esac
exit 0
"""
CLAUDE = "#!/bin/sh\nexit 0\n"  # only has to be found on PATH (D9); never run

# Child processes, run as `python -c <code> <argv...>`.
SLEEP = "import sys, time; time.sleep(float(sys.argv[1]))"
HOLD = (  # argv: <lock file> <seconds>; prints "locked" once it holds the flock
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "print('locked', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)
# A job spawned just before the bot exited (D11, "Why phase order matters"): it takes its lock only
# after 0.3 s, hands the fd to a child as claude_runner's hold_fds does, and exits at 0.6 s, while
# the child, whose argv isn't a job's, keeps the lock for 1.5 s more.
JOB_WITH_CHILD = (
    "import fcntl, os, subprocess, sys, time\n"
    "time.sleep(0.3)\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'], pass_fds=(fd,))\n"
    "time.sleep(0.3)\n"
)
# Loads drain.py in a process whose own argv contains `meals job`, and reports what it found.
PROBE = (
    "import importlib.util, json, os, sys\n"
    "spec = importlib.util.spec_from_file_location('deploy_drain', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "sys.modules[spec.name] = module\n"
    "spec.loader.exec_module(module)\n"
    "print(json.dumps({'self': os.getpid(), 'found': module.meals_processes()}))\n"
)


def _meals_processes_now() -> str:
    """For failure messages: any `meals bot|job` process on this machine stalls the drain."""
    found = subprocess.run(
        ["pgrep", "-fl", "meals (bot|job)"], capture_output=True, text=True, timeout=10
    )
    return found.stdout.strip() or "(none)"


@dataclass(frozen=True)
class Sandbox:
    """A ROOT holding a copy of deploy/, plus the fake HOME, shim dir and env the scripts run with."""

    root: Path
    home: Path
    bin: Path
    cwd: Path
    log: Path
    tag: str  # this sandbox's process namespace: its drain waits only on `<tag> bot|job`
    env: dict[str, str]

    @property
    def launch_agents(self) -> Path:
        return self.home / "Library" / "LaunchAgents"

    def run(self, script: str, *args: str, **env: str) -> tuple[int, str]:
        """Run deploy/<script> in its own process group; return (exit code, stdout + stderr)."""
        path = self.root / "deploy" / script
        if not os.access(path, os.X_OK):
            pytest.fail(
                f"deploy/{script} is not built yet, or isn't executable (P3 seam map D6/D10)",
                pytrace=False,
            )
        proc = subprocess.Popen(
            [str(path), *args],
            cwd=self.cwd,
            env=self.env | env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            pytest.fail(
                f"deploy/{script} {' '.join(args)} still running after 60 s. `meals bot|job` "
                f"processes now: {_meals_processes_now()}\n{out}"
            )
        return proc.returncode, out

    def calls(self) -> list[list[str]]:
        """Every launchctl call so far, each as its argv."""
        if not self.log.exists():
            return []
        return [line.split("\t") for line in self.log.read_text().splitlines()]

    def rendered(self) -> dict[str, bytes]:
        """$ROOT/data/launchd, file name to bytes."""
        directory = self.root / "data" / "launchd"
        return {p.name: p.read_bytes() for p in directory.iterdir()} if directory.is_dir() else {}


def _make_sandbox(tmp: Path, root: Path | None = None) -> Sandbox:
    if UV is None:
        pytest.fail("uv is not on PATH: install.sh bakes it and uninstall runs drain.py with it")
    root = tmp if root is None else root
    if DEPLOY.is_dir():
        shutil.copytree(DEPLOY, root / "deploy", ignore=shutil.ignore_patterns("__pycache__"))
    bin_dir, home, cwd = tmp / "bin", tmp / "home", tmp / "cwd"
    for directory in (bin_dir, home / "Library" / "LaunchAgents", cwd):
        directory.mkdir(parents=True)
    # claude is a symlink, as under nvm: D9 bakes its dir unresolved, the one that also holds node.
    (tmp / "claude-pkg").mkdir()
    for path, text in ((bin_dir / "launchctl", LAUNCHCTL), (tmp / "claude-pkg" / "cli.js", CLAUDE)):
        path.write_text(text)
        path.chmod(0o755)
    (bin_dir / "claude").symlink_to(tmp / "claude-pkg" / "cli.js")
    (tmp / "localtime").symlink_to(DETROIT)
    log = tmp / "launchctl.log"
    tag = f"meals{uuid.uuid4().hex}"
    env = {
        "PATH": f"{bin_dir}:{os.path.dirname(UV)}:{SYSTEM_PATH}",
        "HOME": str(home),
        "MEALS_LOCALTIME": str(tmp / "localtime"),
        "MEALS_DRAIN_PATTERN": f"{tag} (bot|job)",
        "FAKE_LAUNCHCTL_LOG": str(log),
        # uninstall runs `uv run --no-project python drain.py`. Pin the interpreter, keep uv
        # offline, and keep its cache under tmp: the fake HOME has none of uv's own state.
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_CACHE_DIR": str(tmp / "uv-cache"),
    }
    if "TMPDIR" in os.environ:
        env["TMPDIR"] = os.environ["TMPDIR"]
    return Sandbox(root=root, home=home, bin=bin_dir, cwd=cwd, log=log, tag=tag, env=env)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return _make_sandbox(tmp_path)


Spawn = Callable[..., "subprocess.Popen[str]"]


@pytest.fixture
def spawn() -> Iterator[Spawn]:
    """Start `python -c <code> <argv...>` in its own process group. Every group is SIGKILLed at
    teardown, which also reaches a child a spawned process left behind."""
    started: list[subprocess.Popen[str]] = []

    def start(code: str, *argv: str) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            [sys.executable, "-c", code, *argv],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        started.append(proc)
        return proc

    try:
        yield start
    finally:
        for proc in started:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            if proc.stdout is not None:
                proc.stdout.close()


def _hold(spawn: Spawn, path: Path, seconds: float) -> "subprocess.Popen[str]":
    """A process holding `path`'s flock for `seconds`, returned once it holds it."""
    proc = spawn(HOLD, str(path), str(seconds))
    assert proc.stdout is not None
    assert proc.stdout.readline() == "locked\n", f"the holder couldn't lock {path.name}"
    return proc


def _lockable(path: Path, flock: Callable[[int, int], None] = fcntl.flock) -> bool:
    """Whether a new open file description can take `path`'s flock right now (flock locks conflict
    across descriptions, even within one process). `flock` is bound at import, so a test's flock
    spy never sees these probes."""
    fd = os.open(path, os.O_RDONLY)
    try:
        flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        return True
    finally:
        os.close(fd)


def _names(line: str, pid: int) -> bool:
    return re.search(rf"\b{pid}\b", line) is not None


def _last_line(out: str) -> str:
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[-1] if lines else ""


@contextlib.contextmanager
def _deadline(seconds: int) -> Iterator[None]:
    """Fail instead of hanging: drain has no timeout of its own (D11)."""

    def expire(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError(
            f"drain still waiting after {seconds} s. `meals bot|job` processes now: "
            f"{_meals_processes_now()}"
        )

    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous if previous is not None else signal.SIG_DFL)


# ── install.sh --dry-run (RED list 1; D2-D6) ─────────────────────────────────


@dataclass(frozen=True)
class DryRun:
    box: Sandbox
    code: int
    out: str

    def plist(self, agent: str) -> dict[str, Any]:
        assert self.code == 0, f"install.sh --dry-run failed:\n{self.out}"
        path = self.box.root / "data" / "launchd" / f"local.meals.{agent}.plist"
        assert path.is_file(), f"install.sh --dry-run rendered no {path.name}:\n{self.out}"
        parsed: dict[str, Any] = plistlib.loads(path.read_bytes())
        return parsed


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory: pytest.TempPathFactory) -> DryRun:
    """One `install.sh --dry-run`, shared by the tests that only read its result."""
    box = _make_sandbox(tmp_path_factory.mktemp("dry-run"))
    return DryRun(box, *box.run("install.sh", "--dry-run"))


def _canonical(schedule: Any) -> Any:
    """StartCalendarInterval with an array's order ignored: launchd doesn't care about it."""
    if isinstance(schedule, list):
        return sorted(schedule, key=lambda entry: sorted(entry.items()))
    return schedule


@needs_plutil
def test_dry_run_renders_exactly_the_five_plists_and_they_lint(dry_run: DryRun) -> None:
    assert dry_run.code == 0, dry_run.out
    rendered = dry_run.box.rendered()
    assert sorted(rendered) == PLIST_NAMES
    paths = sorted(str(dry_run.box.root / "data" / "launchd" / name) for name in rendered)
    lint = subprocess.run(["plutil", "-lint", *paths], capture_output=True, text=True, timeout=30)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    for data in rendered.values():
        plistlib.loads(data)


@needs_plutil
@pytest.mark.parametrize("agent", AGENTS)
def test_every_agent_has_the_common_keys(dry_run: DryRun, agent: str) -> None:
    plist = dry_run.plist(agent)
    root = dry_run.box.root
    assert plist["Label"] == f"local.meals.{agent}"
    assert plist["RunAtLoad"] is True
    assert plist["WorkingDirectory"] == str(root)  # ROOT from the script's location, not the cwd
    # D5 and D9: uv's dir, then claude's (the shim dir here), then the system dirs.
    assert UV is not None
    expected_path = f"{os.path.dirname(UV)}:{dry_run.box.bin}:{SYSTEM_PATH}"
    assert plist["EnvironmentVariables"]["PATH"] == expected_path
    log = f"{root}/data/logs/launchd-{agent}.log"
    assert (plist["StandardOutPath"], plist["StandardErrorPath"]) == (log, log)
    assert "ExitTimeOut" not in plist
    assert "ProcessType" not in plist


@needs_plutil
def test_bot_runs_under_caffeinate_and_is_kept_alive(dry_run: DryRun) -> None:
    plist = dry_run.plist("bot")
    argv = ["/usr/bin/caffeinate", "-i", UV, "run", "python", "-m", "meals", "bot"]
    assert plist["ProgramArguments"] == argv
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 30
    assert plist["EnvironmentVariables"]["PYTHONUNBUFFERED"] == "1"


@needs_plutil
@pytest.mark.parametrize("job", JOBS)
def test_job_runs_its_command_on_its_schedule(dry_run: DryRun, job: str) -> None:
    plist = dry_run.plist(job)
    # D3: no --now and no --week; a scheduled job takes its week from the clock.
    assert plist["ProgramArguments"] == [UV, "run", "python", "-m", "meals", "job", job]
    assert _canonical(plist["StartCalendarInterval"]) == _canonical(SCHEDULES[job])
    assert plist.get("KeepAlive", False) is False  # D5: only the bot is kept alive


def _tree(directory: Path) -> dict[str, bytes]:
    files = [p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in files}


@needs_plutil
def test_dry_run_prints_the_bootstraps_and_writes_only_under_data(dry_run: DryRun) -> None:
    box = dry_run.box
    assert dry_run.code == 0, dry_run.out
    lines = dry_run.out.splitlines()
    for label in LABELS:
        assert any(
            f"launchctl bootstrap gui/{UID}" in line and f"{label}.plist" in line for line in lines
        ), f"--dry-run doesn't show the bootstrap for {label}:\n{dry_run.out}"
    assert box.calls() == [], "--dry-run called launchctl"
    assert [p for p in box.home.rglob("*") if p.is_file()] == [], "--dry-run wrote under HOME"
    assert list(box.cwd.iterdir()) == []
    assert _tree(box.root / "deploy") == _tree(DEPLOY)
    sandbox_entries = {"bin", "claude-pkg", "home", "cwd", "localtime", "launchctl.log", "uv-cache"}
    assert {p.name for p in box.root.iterdir()} <= sandbox_entries | {"deploy", "data"}


@needs_plutil
def test_dry_run_rerenders_byte_identical_files(sandbox: Sandbox) -> None:
    first = sandbox.run("install.sh", "--dry-run")
    first_render = sandbox.rendered()
    second = sandbox.run("install.sh", "--dry-run")
    assert (first[0], second[0]) == (0, 0), first[1] + second[1]
    assert sorted(first_render) == PLIST_NAMES
    assert sandbox.rendered() == first_render


# ── install.sh refusals (RED list 2-3; D2, D6, D8, D9) ───────────────────────


def test_install_refuses_a_zone_other_than_detroit(sandbox: Sandbox, tmp_path: Path) -> None:
    zone = tmp_path / "new-york"
    zone.symlink_to("/usr/share/zoneinfo/America/New_York")
    code, out = sandbox.run("install.sh", "--dry-run", MEALS_LOCALTIME=str(zone))
    assert code == 1, out
    assert "America/New_York" in out, "the refusal should name the zone it found"
    assert sandbox.rendered() == {}, "the zone is checked before anything is rendered"
    assert sandbox.calls() == []


@pytest.mark.parametrize("tool", ["uv", "claude"])
def test_install_refuses_without_uv_or_claude_on_path(sandbox: Sandbox, tool: str) -> None:
    assert UV is not None
    if tool == "claude":
        (sandbox.bin / "claude").unlink()
        path = f"{sandbox.bin}:{os.path.dirname(UV)}:{SYSTEM_PATH}"
    else:
        path = f"{sandbox.bin}:{SYSTEM_PATH}"
    if shutil.which(tool, path=path) is not None:
        pytest.skip(f"{tool} is installed next to uv or in a system dir on this machine")
    code, out = sandbox.run("install.sh", "--dry-run", PATH=path)
    assert code == 1, out
    assert f"run this from a shell where {tool} is on path" in out.lower(), out
    assert sandbox.rendered() == {}
    assert sandbox.calls() == []


@pytest.mark.parametrize("args", [(), ("--bogus",)], ids=["no-args", "unknown-arg"])
def test_install_without_a_mode_exits_2(sandbox: Sandbox, args: tuple[str, ...]) -> None:
    code, out = sandbox.run("install.sh", *args)
    assert code == 2, out
    assert "usage" in out.lower(), out
    # ADR risk #20: running the bare script must never activate anything.
    assert sandbox.calls() == []
    assert list(sandbox.launch_agents.iterdir()) == []


@needs_plutil
@pytest.mark.parametrize(
    "char",
    ["&", "<", ">", "|", "\\", "\n"],
    ids=["ampersand", "less-than", "greater-than", "pipe", "backslash", "newline"],
)
def test_install_refuses_a_root_that_would_corrupt_the_render(tmp_path: Path, char: str) -> None:
    box = _make_sandbox(tmp_path, root=tmp_path / f"a{char}b")
    code, out = box.run("install.sh", "--dry-run")
    assert code == 1, out


@needs_plutil
def test_install_refuses_a_placeholder_left_after_rendering(sandbox: Sandbox) -> None:
    template = sandbox.root / "deploy" / "local.meals.typo.plist.template"
    template.parent.mkdir(exist_ok=True)
    template.write_text(  # valid XML, so plutil alone wouldn't catch the typo
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
        "<key>WorkingDirectory</key><string>@@ROOTT@@</string></dict></plist>\n"
    )
    code, out = sandbox.run("install.sh", "--dry-run")
    assert code == 1, out


# ── install.sh --activate (RED list 4; D7) ───────────────────────────────────


def _assert_installed(box: Sandbox, calls: list[list[str]]) -> None:
    """The five rendered plists are in LaunchAgents, and each was bootstrapped once."""
    rendered = box.rendered()
    assert sorted(rendered) == PLIST_NAMES
    installed = {p.name: p.read_bytes() for p in box.launch_agents.iterdir()}
    assert installed == rendered
    bootstraps = [call for call in calls if call[0] == "bootstrap"]
    assert [call[:2] for call in bootstraps] == [["bootstrap", f"gui/{UID}"]] * 5, calls
    assert all(len(call) == 3 and Path(call[2]).is_file() for call in bootstraps), bootstraps
    assert sorted(Path(call[2]).name for call in bootstraps) == PLIST_NAMES


def _bootouts(calls: list[list[str]]) -> list[list[str]]:
    return sorted(call for call in calls if call[0] == "bootout")


@needs_plutil
def test_activate_boots_out_what_is_loaded_then_bootstraps_five(sandbox: Sandbox) -> None:
    (sandbox.launch_agents / "local.meals.bot.plist").write_bytes(b"stale")
    loaded = "local.meals.bot local.meals.reconcile"
    code, out = sandbox.run("install.sh", "--activate", FAKE_LAUNCHCTL_LOADED=loaded)
    assert code == 0, out
    calls = sandbox.calls()
    assert _bootouts(calls) == [
        ["bootout", f"gui/{UID}/local.meals.bot"],
        ["bootout", f"gui/{UID}/local.meals.reconcile"],
    ]
    _assert_installed(sandbox, calls)
    verbs = [call[0] for call in calls]
    last_bootout = max(i for i, verb in enumerate(verbs) if verb == "bootout")
    assert last_bootout < verbs.index("bootstrap"), "D7: uninstall runs before any bootstrap"
    assert SAFE in out, "D7: --activate goes through uninstall.sh first"


@needs_plutil
def test_activate_rerun_reaches_the_same_end_state(sandbox: Sandbox) -> None:
    code, out = sandbox.run("install.sh", "--activate")
    assert code == 0, out
    first = {p.name: p.read_bytes() for p in sandbox.launch_agents.iterdir()}
    sandbox.log.unlink()
    code, out = sandbox.run("install.sh", "--activate", FAKE_LAUNCHCTL_LOADED=" ".join(LABELS))
    assert code == 0, out
    calls = sandbox.calls()
    assert _bootouts(calls) == sorted(["bootout", f"gui/{UID}/{label}"] for label in LABELS)
    _assert_installed(sandbox, calls)
    assert {p.name: p.read_bytes() for p in sandbox.launch_agents.iterdir()} == first


@needs_plutil
def test_activate_installs_nothing_when_a_bootout_fails(sandbox: Sandbox) -> None:
    code, out = sandbox.run(
        "install.sh",
        "--activate",
        FAKE_LAUNCHCTL_LOADED="local.meals.bot",
        FAKE_LAUNCHCTL_BOOTOUT_EXIT="5",
    )
    calls = sandbox.calls()
    assert any(call[0] == "bootout" for call in calls), out
    assert code != 0, out
    assert not any(call[0] == "bootstrap" for call in calls), calls
    assert list(sandbox.launch_agents.iterdir()) == []


# ── uninstall.sh (RED list 5-6; D10, ADR rollback steps 1-4) ─────────────────


def test_uninstall_boots_out_only_what_is_loaded_and_removes_plists(sandbox: Sandbox) -> None:
    for label in LABELS[:-1]:  # reconcile's plist is already gone: `rm -f` must not mind
        (sandbox.launch_agents / f"{label}.plist").write_bytes(b"<plist/>")
    other = sandbox.launch_agents / "com.example.other.plist"
    other.write_bytes(b"other")
    loaded = "local.meals.bot local.meals.sat_nudge com.example.other"
    code, out = sandbox.run("uninstall.sh", FAKE_LAUNCHCTL_LOADED=loaded)
    assert code == 0, out
    calls = sandbox.calls()
    assert _bootouts(calls) == [
        ["bootout", f"gui/{UID}/local.meals.bot"],
        ["bootout", f"gui/{UID}/local.meals.sat_nudge"],
    ]
    assert not any("com.example.other" in arg for call in calls for arg in call)
    assert [p.name for p in sandbox.launch_agents.iterdir()] == ["com.example.other.plist"]
    assert other.read_bytes() == b"other"
    assert SAFE in _last_line(out), out


def test_uninstall_stops_before_safe_when_a_bootout_fails(sandbox: Sandbox) -> None:
    code, out = sandbox.run(
        "uninstall.sh", FAKE_LAUNCHCTL_LOADED="local.meals.bot", FAKE_LAUNCHCTL_BOOTOUT_EXIT="5"
    )
    assert any(call[0] == "bootout" for call in sandbox.calls()), out
    assert code != 0, out
    assert SAFE not in out


def test_uninstall_waits_for_a_job_that_holds_no_lock(sandbox: Sandbox, spawn: Spawn) -> None:
    # ADR rollback step 2: a job spawned in the last instant before the bot exited, before it
    # takes its lock. Only the process check can see it.
    job = spawn(SLEEP, "2", sandbox.tag, "job", "cart_fill", "--week", "2026-09-27")
    code, out = sandbox.run("uninstall.sh")
    finished = job.poll()
    assert code == 0, out
    assert finished is not None, f"uninstall reported safe while pid {job.pid} still ran:\n{out}"
    lines = [line for line in out.splitlines() if line.strip()]
    named = [i for i, line in enumerate(lines) if _names(line, job.pid)]
    assert named, f"uninstall never named the job it waited on (pid {job.pid}):\n{out}"
    assert SAFE in lines[-1], out
    assert named[0] < len(lines) - 1


def test_uninstall_waits_for_a_lock_in_data_locks(sandbox: Sandbox, spawn: Spawn) -> None:
    locks = sandbox.root / "data" / "locks"
    locks.mkdir(parents=True)
    _hold(spawn, locks / "chrome.lock", 2.0)
    code, out = sandbox.run("uninstall.sh")
    assert code == 0, out
    assert _lockable(locks / "chrome.lock"), "uninstall reported safe while chrome.lock was held"
    assert SAFE in _last_line(out), out


# ── deploy/drain.py (RED list 7-8; D11) ──────────────────────────────────────


@pytest.fixture
def tag(monkeypatch: pytest.MonkeyPatch) -> str:
    """This test's own process namespace (D11): drain waits only on `<tag> bot|job` processes."""
    value = f"meals{uuid.uuid4().hex}"
    monkeypatch.setenv("MEALS_DRAIN_PATTERN", f"{value} (bot|job)")
    return value


@pytest.fixture
def drain_py(monkeypatch: pytest.MonkeyPatch, tag: str) -> ModuleType:
    """deploy/drain.py, loaded from its file: it lives outside the `meals` package (D11). The tag's
    pattern is set first, so drain.py may read MEALS_DRAIN_PATTERN at import or at each call."""
    path = DEPLOY / "drain.py"
    if not path.is_file():
        pytest.fail(
            "deploy/drain.py is not built yet (P3 seam map D11: PATTERN, POLL_S, "
            "meals_processes, busy_locks, holders, drain, main)",
            pytrace=False,
        )
    spec = importlib.util.spec_from_file_location("deploy_drain", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # no __pycache__ in the repo's deploy/
    spec.loader.exec_module(module)
    return module


def test_drain_pattern_and_poll_interval(drain_py: ModuleType) -> None:
    assert drain_py.PATTERN == "meals (bot|job)"
    assert drain_py.POLL_S == 2.0


def test_drain_imports_only_the_standard_library(drain_py: ModuleType) -> None:
    tree = ast.parse((DEPLOY / "drain.py").read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
    names = [alias.name for node in imports if isinstance(node, ast.Import) for alias in node.names]
    for node in imports:
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, "drain.py runs as a script: no relative imports"
            names.append(node.module)
    roots = {name.split(".")[0] for name in names}
    assert sorted(roots - sys.stdlib_module_names) == [], "D11: drain.py is stdlib only"


def test_meals_processes_lists_bot_and_job_processes_but_not_itself(
    drain_py: ModuleType, spawn: Spawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A snapshot, not a wait, so it can use the default pattern against real argv shapes.
    monkeypatch.delenv("MEALS_DRAIN_PATTERN")
    bot = spawn(SLEEP, "2", "meals", "bot")
    job = spawn(SLEEP, "2", "meals", "job", "reconcile")
    near_miss = spawn(SLEEP, "2", "meals", "mcp")
    probe = subprocess.run(
        [sys.executable, "-B", "-c", PROBE, str(DEPLOY / "drain.py"), "meals", "job", "probe"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    result = json.loads(probe.stdout)
    by_pid = {entry.split(maxsplit=1)[0]: entry for entry in result["found"]}
    assert "meals bot" in by_pid.get(str(bot.pid), ""), result
    assert "meals job reconcile" in by_pid.get(str(job.pid), ""), result
    assert str(near_miss.pid) not in by_pid
    # Only Linux can tell here: macOS pgrep already leaves out its own ancestors.
    assert str(result["self"]) not in by_pid, "meals_processes listed its own process"


def test_meals_drain_pattern_gives_a_test_its_own_processes(
    drain_py: ModuleType, spawn: Spawn, tag: str
) -> None:
    ours = spawn(SLEEP, "2", tag, "job", "x")
    real = spawn(SLEEP, "2", "meals", "job", "x")
    found = {entry.split(maxsplit=1)[0] for entry in drain_py.meals_processes()}
    assert str(ours.pid) in found, "meals_processes ignored MEALS_DRAIN_PATTERN"
    assert str(real.pid) not in found


def test_busy_locks_returns_the_held_lock_and_releases_the_rest(
    drain_py: ModuleType, spawn: Spawn, tmp_path: Path
) -> None:
    locks = tmp_path / "locks"
    locks.mkdir()
    free, held = locks / "job-cart_fill.lock", locks / "claude-slot-1.lock"
    free.touch()
    _hold(spawn, held, 3.0)
    before, fds = sorted(locks.iterdir()), len(os.listdir("/dev/fd"))
    assert drain_py.busy_locks(locks) == [held]
    assert len(os.listdir("/dev/fd")) == fds, "busy_locks left file descriptors open"
    assert _lockable(free), "busy_locks still holds job-cart_fill.lock after returning"
    assert not _lockable(held)
    assert sorted(locks.iterdir()) == before


def test_busy_locks_holds_every_lock_at_one_instant(
    drain_py: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D11: `[]` means every lock was held by us at one instant. Before each attempt, count the
    # locks a fresh description can't take: the ones busy_locks already holds.
    locks = tmp_path / "locks"
    locks.mkdir()
    paths = [locks / name for name in ("chrome.lock", "claude-slot-0.lock", "job-reconcile.lock")]
    for path in paths:
        path.touch()
    real_flock = fcntl.flock
    operations: list[int] = []
    held_before: list[int] = []

    def flock_spy(fd: int, operation: int) -> None:
        operations.append(operation)
        held_before.append(sum(not _lockable(path) for path in paths))
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", flock_spy)
    for attribute, value in list(vars(drain_py).items()):  # `from fcntl import flock`
        if value is real_flock:
            monkeypatch.setattr(drain_py, attribute, flock_spy)
    assert drain_py.busy_locks(locks) == []
    assert not [op for op in operations if op & fcntl.LOCK_UN], "D11: release by close, not LOCK_UN"
    assert held_before == [0, 1, 2], "busy_locks let one lock go before trying the next"


def test_busy_locks_with_every_lock_free_is_empty_and_leaves_them_free(
    drain_py: ModuleType, tmp_path: Path
) -> None:
    locks = tmp_path / "locks"
    locks.mkdir()
    names = ("job-sat_propose.lock", "chrome.lock", "claude-slot-0.lock", "claude-slot-1.lock")
    paths = sorted(locks / name for name in names)
    for path in paths:
        path.touch()
    assert drain_py.busy_locks(locks) == []
    assert all(_lockable(path) for path in paths), "busy_locks kept a lock"
    assert sorted(locks.iterdir()) == paths


def test_busy_locks_on_a_missing_or_empty_dir_is_empty_and_creates_nothing(
    drain_py: ModuleType, tmp_path: Path
) -> None:
    assert drain_py.busy_locks(tmp_path / "missing") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert drain_py.busy_locks(empty) == []
    assert list(empty.iterdir()) == []


@pytest.mark.skipif(shutil.which("lsof") is None, reason="holders is best effort without lsof")
def test_holders_names_the_pid_holding_a_lock(
    drain_py: ModuleType, spawn: Spawn, tmp_path: Path
) -> None:
    lock = tmp_path / "chrome.lock"
    holder = _hold(spawn, lock, 3.0)
    found = drain_py.holders(lock)
    assert [entry for entry in found if entry.split(maxsplit=1)[0] == str(holder.pid)], found


def test_holders_is_empty_when_nobody_holds_it_or_lsof_is_missing(
    drain_py: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "chrome.lock"
    lock.touch()
    assert drain_py.holders(lock) == []
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    assert drain_py.holders(lock) == []


def test_drain_waits_for_a_job_process_and_names_it_once(
    drain_py: ModuleType, spawn: Spawn, tag: str, tmp_path: Path
) -> None:
    job = spawn(SLEEP, "1.5", tag, "job", "sat_nudge")
    lines: list[str] = []
    with _deadline(30):
        drain_py.drain(tmp_path / "locks", poll_s=0.05, out=lines.append)
    assert job.poll() is not None, "drain returned while a job process was running"
    naming = [line for line in lines if _names(line, job.pid)]
    assert naming, f"drain never named pid {job.pid}: {lines}"
    # ~15 polls happen while it waits; D11 prints only when the set of processes changes.
    assert len(naming) <= 3, f"drain reprinted what it waits on at every poll: {lines}"


def test_drain_waits_for_a_lock_holder_that_is_not_a_job_process(
    drain_py: ModuleType, spawn: Spawn, tmp_path: Path
) -> None:
    # ADR rollback step 3: an orphaned `claude --chrome` child still holds the Chrome lock after
    # its job died. Its argv isn't a job's, so only the lock check can see it.
    locks = tmp_path / "locks"
    locks.mkdir()
    job_lock, chrome_lock = locks / "job-cart_fill.lock", locks / "chrome.lock"
    job_lock.touch()
    holder = _hold(spawn, chrome_lock, 1.5)
    lines: list[str] = []
    job_lock_free: list[bool] = []  # at each report: drain holds no lock while it waits

    def report(line: str) -> None:
        lines.append(line)
        job_lock_free.append(_lockable(job_lock))

    with _deadline(30):
        drain_py.drain(locks, poll_s=0.05, out=report)
    assert _lockable(chrome_lock), "drain returned while chrome.lock was still held"
    assert job_lock_free and all(job_lock_free), "drain held job-cart_fill.lock while it waited"
    naming = [line for line in lines if "chrome.lock" in line]
    assert naming, f"drain never named the lock it waited on: {lines}"
    assert len(naming) <= 3, f"drain reprinted what it waits on at every poll: {lines}"
    if shutil.which("lsof") is not None:
        assert _names("\n".join(lines), holder.pid), f"drain never named pid {holder.pid}"


def test_drain_checks_processes_before_locks(
    drain_py: ModuleType, spawn: Spawn, tag: str, tmp_path: Path
) -> None:
    locks = tmp_path / "locks"
    locks.mkdir()
    lock = locks / "job-cart_fill.lock"
    lock.touch()  # lock files are never deleted, so it's there before the job takes it
    spawn(JOB_WITH_CHILD, str(lock), tag, "job", "cart_fill")
    with _deadline(30):
        drain_py.drain(locks, poll_s=0.05, out=lambda line: None)
    assert _lockable(lock), (
        "drain returned while the job's child still held job-cart_fill.lock: the locks must be "
        "checked after the job processes are gone, not before"
    )


def _exit_status(main: Callable[[list[str]], int], argv: list[str]) -> object:
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code


def test_main_takes_exactly_one_lock_dir(drain_py: ModuleType, tmp_path: Path) -> None:
    assert _exit_status(drain_py.main, []) == 2
    assert _exit_status(drain_py.main, [str(tmp_path), "extra"]) == 2
    with _deadline(30):
        assert drain_py.main([str(tmp_path / "locks")]) == 0


# ── README.md (RED list 9; D12) ──────────────────────────────────────────────

STUCK_RUN_STEPS = (
    "lsof",
    "data/locks/job-",
    "kill -TERM -",
    "interrupted",
    "cart_fill",
    "retry cart",
)


def _readme() -> str:
    readme = DEPLOY / "README.md"
    assert readme.is_file(), "deploy/README.md is not written yet (P3 seam map D12)"
    return readme.read_text()


def test_readme_has_the_stuck_run_section_the_stale_alert_points_to() -> None:
    # meals/jobs.py's stale alert says "see deploy/README.md, Stuck run".
    lines = _readme().splitlines()
    heading = re.compile(r"(#{1,6})[ \t]+(.*?)[ \t]*")
    starts = [
        (i, len(m[1]))
        for i, line in enumerate(lines)
        if (m := heading.fullmatch(line)) and m[2] == "Stuck run"
    ]
    assert starts, "deploy/README.md has no `Stuck run` heading"
    start, level = starts[0]
    section: list[str] = []
    fenced = False
    for line in lines[start + 1 :]:  # up to the next heading at its level or above
        fenced ^= line.lstrip().startswith(("```", "~~~"))  # a `# comment` in a fence isn't one
        m = heading.fullmatch(line)
        if m and not fenced and len(m[1]) <= level:
            break
        section.append(line)
    text = "\n".join(section)
    for step in STUCK_RUN_STEPS:
        assert step in text, f"the Stuck run section doesn't mention {step!r}"
    assert "never delete" in text.lower()


def test_readme_covers_install_uninstall_and_logs() -> None:
    text = _readme()
    for token in ("--dry-run", "--activate", "uninstall.sh", "data/logs/jobs.log", "launchd-"):
        assert token in text, f"deploy/README.md doesn't mention {token!r}"
    for agent in AGENTS:
        assert agent in text, f"deploy/README.md doesn't list the {agent} agent"


# ── guard (RED list 10; D10) ─────────────────────────────────────────────────


def test_no_deploy_script_can_bypass_the_launchctl_shim() -> None:
    # Bare `launchctl`, found on the caller's PATH, is what lets every test here shim it.
    absolute = re.compile(r"/launchctl\b")
    bypass = re.compile(absolute.pattern + r"|\bPATH=|command -p|env -i")
    for text, flagged in (
        ("/bin/launchctl bootout", True),
        ('export PATH="/usr/bin:$PATH"', True),
        ("command -p launchctl print", True),
        ("env -i launchctl bootout", True),
        ("launchctl bootout gui/501/local.meals.bot", False),
        ('BAKED_PATH="$(dirname "$UV")"', False),
    ):
        assert (bypass.search(text) is not None) is flagged, text
    tree = _tree(DEPLOY) if DEPLOY.is_dir() else {}
    files = {name: data.decode(errors="replace") for name, data in tree.items()}
    assert {"install.sh", "uninstall.sh"} <= files.keys(), "deploy/ scripts are not written yet"
    assert [name for name, text in files.items() if absolute.search(text)] == []
    assert [
        name for name, text in files.items() if name.endswith(".sh") and bypass.search(text)
    ] == []
