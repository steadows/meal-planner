"""meals.__main__: `python -m meals job <name> [--now ISO] [--week YYYY-MM-DD] [--retry]`.

Authority: the P2 seam map (.context/seams/P2.md, `meals/__main__.py`: the argument rules, the Deps it
builds from the real modules, `fill` refusing, logging; the `JOBS` row, "__main__ uses it as the
argparse choices, so the spawn_job child refuses unknown names before doing anything") and ADR-0001
(Decision :52-53; `cart_fill` ships only after hold_fds and the P4.4 test, :202-203).

No job runs: `jobs.run_job` is replaced by a recording spy (and any alias of it in meals.__main__),
which inspects the Deps while the call is live. Settings come from env vars pointing at tmp_path,
with the project `.env` switched off, so main opens a throwaway database and builds clients that
are never called. `logging.FileHandler` is replaced by a handler that records its path and writes
nothing, and handlers main adds are removed afterwards.
"""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from meals import background, planner
from meals.config import PROJECT_ROOT, Settings
from meals.contracts import Components, MealieUnavailable, WeekProposal
from meals.mealie_client import HttpMealieClient
from meals.pantry import SqlitePantry

_MISSING: ImportError | None = None
try:
    from meals import __main__ as cli
    from meals import jobs
except ImportError as exc:  # RED: meals/__main__.py and meals/jobs.py aren't written yet
    _MISSING = exc

DETROIT = ZoneInfo("America/Detroit")
JOB_NAMES = ("sat_propose", "sat_nudge", "sun_autoapprove", "cart_fill", "reconcile")
NOW = ["--now", "2026-09-27T10:15"]
PLAN = WeekProposal(
    week_start=date(2026, 9, 27),
    custody="wed+sat_sun",
    recipe_options=(),
    components=Components(),
    lunch_builds=(),
    kid_nights=(),
    pantry_questions=(),
)


class RunJobSpy:
    """Stands in for jobs.run_job. It looks at the Deps during the call, because main may close
    them once run_job returns."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.code = 0
        self.log_files: list[Path] = []
        self.with_deps: Callable[[Any], object] | None = None  # runs on the live Deps
        self.raises: BaseException | None = None  # escapes run_job, as a crash would

    def __call__(
        self, name: str, deps: Any, *, now: datetime, week: date | None = None, retry: bool = False
    ) -> int:
        if self.with_deps is not None:
            self.with_deps(deps)
        if self.raises is not None:
            raise self.raises
        tables = {row[0] for row in deps.conn.execute("SELECT name FROM sqlite_master")}
        try:
            deps.fill(PLAN, ())
            refusal = None
        except Exception as exc:  # the refusal is what's being checked
            refusal = str(exc)
        self.calls.append(
            {"name": name, "now": now, "week": week, "retry": retry, "deps": deps}
            | {"tables": tables, "fill_refusal": refusal}
        )
        return self.code


def _replace_everywhere(
    monkeypatch: pytest.MonkeyPatch, real: object, fake: object, *modules: ModuleType
) -> None:
    for module in modules:
        for name, value in list(vars(module).items()):
            if value is real:
                monkeypatch.setattr(module, name, fake)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    loggers = [logging.getLogger(), logging.getLogger("meals")]
    saved = [(logger, list(logger.handlers), logger.level) for logger in loggers]
    yield
    for logger, handlers, level in saved:
        for handler in [h for h in logger.handlers if h not in handlers]:
            if not type(handler).__module__.startswith("_pytest"):
                logger.removeHandler(handler)
        logger.setLevel(level)


@pytest.fixture
def run_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_settings: None) -> RunJobSpy:
    if _MISSING is not None:
        pytest.fail(
            f"meals.__main__ / meals.jobs are not built yet (P2 seam map): {_MISSING}",
            pytrace=False,
        )
    monkeypatch.setitem(Settings.model_config, "env_file", None)  # never the project's .env
    env = {
        "PANTRY_DB": str(tmp_path / "pantry.sqlite"),
        "CLAUDE_LOCK_DIR": str(tmp_path / "locks"),
        "PREFS_FILE": str(tmp_path / "prefs.yaml"),
        "TELEGRAM_BOT_TOKEN": "123456:fake-token",
        "TELEGRAM_ALLOWED_CHAT_ID": "4242",
        "MEALIE_TOKEN": "fake-mealie-token",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spy = RunJobSpy()

    class RecordingFileHandler(logging.Handler):
        def __init__(self, filename: Any, *args: Any, **kwargs: Any) -> None:
            super().__init__()
            spy.log_files.append(Path(filename))

        def emit(self, record: logging.LogRecord) -> None:
            return None

    _replace_everywhere(monkeypatch, jobs.run_job, spy, jobs, cli)
    _replace_everywhere(monkeypatch, logging.FileHandler, RecordingFileHandler, logging, cli)
    return spy


def _main(argv: list[str]) -> int:
    """main's exit code, whether it returns it or raises SystemExit (argparse does)."""
    try:
        return cli.main(argv)
    except SystemExit as exit_:
        code = exit_.code
        return code if isinstance(code, int) else (0 if code is None else 1)


# ── the job subcommand ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", JOB_NAMES)
def test_every_job_reaches_run_job(run_job: RunJobSpy, name: str) -> None:
    week = ["--week", "2026-09-27"] if name == "cart_fill" else []

    assert _main(["job", name, *NOW, *week]) == 0

    assert [call["name"] for call in run_job.calls] == [name]
    if name != "cart_fill":
        assert (run_job.calls[0]["week"], run_job.calls[0]["retry"]) == (None, False)


def test_an_unknown_job_is_refused_before_anything_runs(run_job: RunJobSpy, tmp_path: Path) -> None:
    # The spawn_job child refuses an unknown name before doing anything (P1's CWE-88 LOW).
    assert _main(["job", "rm_rf"]) == 2

    assert run_job.calls == []
    assert not (tmp_path / "pantry.sqlite").exists()


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param("2026-09-26T08:00", datetime(2026, 9, 26, 8, tzinfo=DETROIT), id="naive-edt"),
        pytest.param("2026-11-01T08:00", datetime(2026, 11, 1, 8, tzinfo=DETROIT), id="naive-est"),
        pytest.param(
            "2026-09-26T12:00:00+00:00", datetime(2026, 9, 26, 8, tzinfo=DETROIT), id="aware"
        ),
    ],
)
def test_now_is_aware_and_a_naive_now_is_detroit_time(
    run_job: RunJobSpy, given: str, expected: datetime
) -> None:
    _main(["job", "sat_propose", "--now", given])

    now = run_job.calls[0]["now"]
    assert now.tzinfo is not None and now == expected


def test_now_defaults_to_the_current_time(run_job: RunJobSpy) -> None:
    _main(["job", "reconcile"])

    now = run_job.calls[0]["now"]
    assert now.tzinfo is not None
    assert abs(now - datetime.now(UTC)) < timedelta(minutes=1)


@pytest.mark.parametrize(
    "week", ["2026-09-28", "2026-13-01", "next-sunday"], ids=["monday", "no-such-day", "not-a-date"]
)
def test_week_must_be_a_sunday(run_job: RunJobSpy, week: str) -> None:
    assert _main(["job", "cart_fill", "--week", week, *NOW]) == 2
    assert run_job.calls == []


def test_cart_fill_needs_a_week(run_job: RunJobSpy) -> None:
    assert _main(["job", "cart_fill", *NOW]) != 0
    assert run_job.calls == []


def test_week_and_retry_reach_run_job_and_its_result_is_the_exit_code(
    run_job: RunJobSpy,
) -> None:
    run_job.code = 3

    assert _main(["job", "cart_fill", "--week", "2026-09-27", "--retry", *NOW]) == 3

    assert (run_job.calls[0]["week"], run_job.calls[0]["retry"]) == (date(2026, 9, 27), True)


def test_run_job_gets_deps_built_from_the_real_modules(run_job: RunJobSpy, tmp_path: Path) -> None:
    _main(["job", "reconcile", *NOW])

    call = run_job.calls[0]
    deps = call["deps"]
    assert "job_run" in call["tables"] and (tmp_path / "pantry.sqlite").exists()
    assert Path(deps.lock_dir) == tmp_path / "locks"
    assert isinstance(deps.send, jobs.TelegramSend)
    assert deps.spawn is background.spawn_job
    assert isinstance(deps.mealie, HttpMealieClient)
    refusal = call["fill_refusal"]
    assert refusal is not None and "wired" in refusal and "cart" in refusal


@pytest.mark.parametrize("unset", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID"])
def test_a_missing_telegram_setting_exits_2_with_a_message(
    run_job: RunJobSpy,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    unset: str,
) -> None:
    monkeypatch.delenv(unset)

    assert _main(["job", "reconcile", *NOW]) == 2

    assert run_job.calls == []
    assert "TELEGRAM" in (capsys.readouterr().err + caplog.text).upper()


def test_the_job_log_is_data_logs_jobs_log_under_the_project_root(run_job: RunJobSpy) -> None:
    _main(["job", "reconcile", *NOW])

    assert PROJECT_ROOT / "data" / "logs" / "jobs.log" in run_job.log_files


def test_propose_is_the_planner_bound_to_the_real_pantry_and_the_mealie_client(
    run_job: RunJobSpy, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seam map `__main__`: `planner.propose` bound to SqlitePantry and the Mealie client. The planner
    # is replaced by a recorder, so nothing reaches Claude or Mealie.
    seen: list[dict[str, Any]] = []

    def record_propose(week_start: date, custody: str, **kwargs: Any) -> WeekProposal:
        seen.append({"week": week_start, "custody": custody} | kwargs)
        return PLAN

    _replace_everywhere(monkeypatch, planner.propose, record_propose, planner, cli)
    run_job.with_deps = lambda deps: deps.propose(date(2026, 9, 27), "wed+sat_sun", ())

    _main(["job", "sat_propose", *NOW])

    assert len(seen) == 1, seen
    call = seen[0]
    assert (call["week"], call["custody"], tuple(call["recent"])) == (
        date(2026, 9, 27),
        "wed+sat_sun",
        (),
    )
    assert isinstance(call["pantry"], SqlitePantry)
    assert call["mealie"] is run_job.calls[0]["deps"].mealie


# ── code-review repairs (seam map "Also fixed"; review findings #3, #13) ─────


def _record_sends(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """TelegramSend without the network: the texts main tries to send."""
    sent: list[str] = []

    def record(self: object, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(jobs.TelegramSend, "__call__", record)
    return sent


@pytest.mark.parametrize("escaping", ["error", "sigterm"])
def test_anything_escaping_run_job_is_logged_reported_and_exits_1(
    run_job: RunJobSpy,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    escaping: str,
) -> None:
    # Review #3: __main__ catches whatever escapes run_job (a locked DB, a PlanStateError in the
    # catch-all, a SIGTERM outside it), logs it to jobs.log and tells Steve.
    sent = _record_sends(monkeypatch)
    error: BaseException = (
        RuntimeError("db locked")
        if escaping == "error"
        else jobs.JobTerminated("stopped by SIGTERM")
    )
    run_job.raises = error

    assert _main(["job", "reconcile", *NOW]) == 1

    assert any("job reconcile failed" in text for text in sent), sent
    assert str(error) in caplog.text


def test_a_startup_failure_logs_mealies_message_but_not_its_unvetted_cause(
    run_job: RunJobSpy, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Review #13 (contracts.MealieUnavailable: log the message, not the chained cause), on
    # __main__'s start-up failure path.
    sent = _record_sends(monkeypatch)

    def mealie_down(*args: object, **kwargs: object) -> None:
        raise MealieUnavailable("Mealie is down") from RuntimeError("UNVETTED-CAUSE")

    monkeypatch.setattr(HttpMealieClient, "from_settings", mealie_down)

    assert _main(["job", "reconcile", *NOW]) == 1

    assert run_job.calls == [] and sent, "a start-up failure is reported, and no job runs"
    assert "Mealie is down" in caplog.text
    assert "UNVETTED-CAUSE" not in caplog.text
