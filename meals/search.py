"""Recipe search (`/find`): a plain-words request plus the preferences profile, in one web-enabled
`claude -p` run, gives up to MAX_OPTIONS RecipeOptions (PLAN.md → Recipe search).

"save N" and "add N to this week" are composed by the entry points (`mealie.import_url`, plan_state);
this module never touches Mealie or the weekly plan.
"""

import logging

from pydantic import Field

from meals import claude_runner
from meals.config import get_settings
from meals.contracts import Contract, RecipeOption

logger = logging.getLogger(__name__)

MAX_OPTIONS = 5
MAX_REQUEST_CHARS = 500


class _FindResult(Contract):
    options: tuple[RecipeOption, ...] = Field(
        max_length=MAX_OPTIONS,
        description="3-5 recipes that fit, best first; fewer only if fewer truly fit",
    )


def find(request: str) -> tuple[RecipeOption, ...]:
    """Search the web for recipes matching `request`, filtered by the profile.

    Raises ValueError for a blank or oversized request (before any Claude run), OSError if the
    profile can't be read, and ClaudeRunnerError if the run fails or returns more than
    MAX_OPTIONS options.
    """
    text = request.strip()
    if not text:
        raise ValueError("recipe request is empty")
    if len(text) > MAX_REQUEST_CHARS:
        raise ValueError(f"recipe request is over {MAX_REQUEST_CHARS} characters")
    prefs = get_settings().prefs_file.read_text(encoding="utf-8")
    prompt = claude_runner.load_prompt("search", "find", request=text, prefs=prefs)
    logger.info("find: searching for %r", text)
    options = claude_runner.run(prompt, schema=_FindResult).options
    logger.info("find: %d options for %r", len(options), text)
    return options
